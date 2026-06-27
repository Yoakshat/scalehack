import io
import json
import os
import socket
import threading

import qrcode
from openai import OpenAI
from flask import Flask, jsonify, render_template, request, send_file

import db
from config import DEEPSEEK_API_KEY, GMAIL_CONNECTION_NAME, SHEETS_CONNECTION_NAME
from profile import get_founder_email, get_founder_name, require_profile, save_profile

app = Flask(__name__)
db.init_db()

_deepseek = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

def _llm(prompt: str, max_tokens: int = 500) -> str:
    resp = _deepseek.chat.completions.create(
        model="deepseek-chat",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content.strip()

FIRMS = [
    {"id": "a16z",       "name": "Andreessen Horowitz"},
    {"id": "sequoia",    "name": "Sequoia Capital"},
    {"id": "yc",         "name": "Y Combinator"},
    {"id": "accel",      "name": "Accel"},
    {"id": "lightspeed", "name": "Lightspeed"},
    {"id": "gc",         "name": "General Catalyst"},
    {"id": "benchmark",  "name": "Benchmark"},
    {"id": "index",      "name": "Index Ventures"},
    {"id": "kpcb",       "name": "Kleiner Perkins"},
    {"id": "ff",         "name": "Founders Fund"},
]
FIRM_MAP = {f["id"]: f["name"] for f in FIRMS}


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.route("/")
def founder_view():
    return render_template("founder.html")

@app.route("/invest")
def investor_view():
    return render_template("investor.html", firms=FIRMS)

@app.route("/settings")
def settings_view():
    email = get_founder_email()
    permission = db.get_setting("permission", "confirm")
    connected = _gmail_connected()
    cal_connected = _calendar_connected()
    sheets_conn = _sheets_connected()
    return render_template("settings.html", email=email, permission=permission,
                           connected=connected, cal_connected=cal_connected,
                           sheets_connected=sheets_conn)

@app.route("/qr")
def qr_view():
    local_ip = _get_local_ip()
    url = f"http://{local_ip}:8080/invest"
    return render_template("qr.html", url=url)


# ── API: Investor ──────────────────────────────────────────────────────────────

@app.route("/api/message", methods=["POST"])
def post_message():
    data = request.json or {}
    firm_id      = data.get("firm_id", "").strip()
    firm_name    = FIRM_MAP.get(firm_id, firm_id)
    sender       = data.get("sender_name", "Investor").strip()
    sender_email = data.get("sender_email", "").strip()
    text         = data.get("text", "").strip()

    if not firm_id or not text:
        return jsonify({"error": "firm_id and text required"}), 400

    db.add_message(firm_id, firm_name, sender, text, sender_email)

    # background: embed + update memory
    threading.Thread(target=_process_message, args=(firm_id, firm_name, sender, text), daemon=True).start()

    return jsonify({"ok": True})


# ── API: Founder polling ───────────────────────────────────────────────────────

@app.route("/api/firms")
def get_firms():
    return jsonify(db.get_all_firms())

@app.route("/api/memory/<firm_id>")
def get_memory(firm_id):
    firms = db.get_all_firms()
    for f in firms:
        if f["firm_id"] == firm_id:
            return jsonify({"summary": f.get("summary", ""), "tier": f.get("tier", "cold")})
    return jsonify({"summary": "", "tier": "cold"})


# ── API: Morning Brief ─────────────────────────────────────────────────────────

def _brief_for_firm(firm: dict) -> dict:
    """Build an urgency + draft email for a single firm using its stored messages."""
    firm_id   = firm["firm_id"]
    firm_name = firm["firm_name"]
    messages  = db.get_firm_messages(firm_id, limit=10)
    msg_text  = "\n".join(f"- {m['sender_name']}: {m['text']}" for m in messages)

    # RAG: pull top memories from VectorAI (optional)
    firm_hits = []
    rag_context = ""
    try:
        from memory import search_firm_memory
        firm_hits = search_firm_memory(firm_id, "follow up commitment interest urgent traction", limit=4)
        if firm_hits:
            rag_context = "\n".join(f"- [{h.get('tier','?')}] {h.get('snippet','')[:150]}" for h in firm_hits)
    except Exception:
        pass

    # RAG: startup context from Google Sheets
    startup_hits = []
    startup_rag = ""
    try:
        from memory import search_startup_memory
        startup_hits = search_startup_memory(f"{firm_name} investor traction metrics revenue growth", limit=4)
        if startup_hits:
            startup_rag = "\n".join(
                f"- [{h.get('source','?')} · {h.get('name','?')}] {h.get('snippet','')[:200]}"
                for h in startup_hits
            )
    except Exception:
        pass

    prompt = f"""Sender / firm: {firm_name}
Their messages:
{msg_text}

Memory context (most relevant past signals):
{rag_context or '(none yet)'}

Startup progress (from Google Sheets metrics):
{startup_rag or '(no data sources connected yet)'}

You are helping a startup founder decide what to do with this investor contact.

Actions available:
- "email": send a follow-up email (only if there is a real reason — new traction, a condition was met, a deadline, or they asked for an update)
- "calendar": schedule a meeting (only if investor expressed interest in meeting)
- "both": email + schedule
- "none": nothing to do right now — no new signal, no pending commitment, nothing actionable

Return ONLY valid JSON:
{{
  "urgency": "high" | "medium" | "low",
  "action": "email" | "calendar" | "both" | "none",
  "reason": "<one sentence why, referencing investor signals and startup progress>",
  "email_subject": "<subject line, or empty string if action is none/calendar>",
  "email_body": "<draft follow-up email body, under 120 words, warm and specific. Empty if action is none/calendar>"
}}"""

    try:
        import re
        raw = _llm(prompt, max_tokens=550)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        result = json.loads(match.group()) if match else {}
    except Exception as e:
        result = {"urgency": "low", "action": "none", "reason": str(e), "email_subject": "", "email_body": ""}

    latest_msg = messages[-1] if messages else {}
    investor_email = latest_msg.get("sender_email", "")
    action = result.get("action", "none")

    # Persist action so /api/firms can rank cards without re-running the LLM
    try:
        db.set_firm_action(firm_id, action)
    except Exception:
        pass

    # Build attribution: every source that fed the AI decision
    attribution = []
    for m in messages[:5]:
        attribution.append({"type": "gmail", "name": f"{m['sender_name']} · {firm_name}", "snippet": m["text"][:120]})
    for h in firm_hits:
        label = h.get("intent_type") or h.get("tier") or "signal"
        attribution.append({"type": "memory", "name": f"Memory · {h.get('sender', firm_name)} [{label}]", "snippet": h.get("snippet", "")[:120]})
    for h in startup_hits:
        attribution.append({"type": h.get("source", "sheets"), "name": h.get("name", ""), "snippet": h.get("snippet", "")[:120]})

    return {
        "firm_id":        firm_id,
        "firm_name":      firm_name,
        "urgency":        result.get("urgency", "low"),
        "action":         action,
        "reason":         result.get("reason", ""),
        "email_subject":  result.get("email_subject", ""),
        "email_body":     result.get("email_body", ""),
        "investor_email": investor_email,
        "summary":        firm.get("summary", ""),
        "tier":           firm.get("tier", "cold"),
        "messages":       messages,
        "message_count":  firm["message_count"],
        "last_contact":   firm["last_message_at"],
        "attribution":    attribution,
    }


@app.route("/api/brief", methods=["POST"])
def morning_brief():
    from datetime import datetime, timezone
    firms = db.get_all_firms()
    if not firms:
        return jsonify({"results": []})

    # Sync Sheets once before per-firm analysis so RAG has fresh data
    last_synced_iso = db.get_setting("startup_last_synced_at", "")
    sources_synced = []
    try:
        from sheets_client import fetch_startup_data
        from memory import store_startup_memory
        chunks = fetch_startup_data(since_iso=last_synced_iso or None)
        for chunk in chunks:
            store_startup_memory(chunk["text"], chunk["source"], chunk["name"])
        if chunks:
            sources_synced.append("googlesheets")
    except Exception:
        pass
    db.set_setting("startup_last_synced_at", datetime.now(timezone.utc).isoformat())

    results = [_brief_for_firm(firm) for firm in firms]
    for r in results:
        r.pop("messages", None)

    order = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda x: order.get(x["urgency"], 3))
    return jsonify({"results": results, "sources_synced": sources_synced})


@app.route("/api/firm/<firm_id>")
def firm_detail(firm_id):
    firms = db.get_all_firms()
    firm = next((f for f in firms if f["firm_id"] == firm_id), None)
    if not firm:
        return jsonify({"error": "firm not found"}), 404
    return jsonify(_brief_for_firm(firm))


# ── API: Inbox watcher ─────────────────────────────────────────────────────────

@app.route("/api/inbox-status")
def inbox_status():
    from inbox_watcher import get_status
    return jsonify(get_status())

@app.route("/api/sync-inbox", methods=["POST"])
def sync_inbox():
    try:
        from inbox_watcher import sync_once
        return jsonify({"ok": True, **sync_once()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── API: Send / Draft email ────────────────────────────────────────────────────

@app.route("/api/send-email", methods=["POST"])
def send_email():
    data    = request.json or {}
    subject = data.get("subject", "Following up")
    body    = data.get("body", "")
    firm_id = data.get("firm_id", "")
    permission = db.get_setting("permission", "confirm")

    to      = data.get("to", "")
    result  = _create_gmail_draft(to=to, subject=subject, body=body)
    auto    = permission == "auto_send"
    return jsonify({"ok": result.get("ok", False), "auto": auto,
                    "draft_url": "https://mail.google.com/mail/u/0/#drafts",
                    "error": result.get("error"), "result": result.get("result")})


# ── API: Gmail / Settings ──────────────────────────────────────────────────────

@app.route("/api/auth-status")
def auth_status():
    return jsonify({"connected": _gmail_connected()})


# ── API: Calendar ──────────────────────────────────────────────────────────────

@app.route("/api/calendar-status")
def calendar_status():
    try:
        from calendar_client import calendar_connected
        return jsonify({"connected": calendar_connected()})
    except Exception as e:
        return jsonify({"connected": False, "error": str(e)})

@app.route("/api/connect-calendar")
def connect_calendar():
    try:
        from calendar_client import get_connect_link
        return jsonify({"url": get_connect_link()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/availability")
def availability():
    try:
        from calendar_client import get_availability
        return jsonify({"slots": get_availability()})
    except Exception as e:
        return jsonify({"slots": [], "error": str(e)}), 500

@app.route("/api/schedule", methods=["POST"])
def schedule():
    data = request.json or {}
    start = data.get("start", "")
    if not start:
        return jsonify({"ok": False, "error": "start is required"}), 400
    try:
        from calendar_client import create_event
        event = create_event(
            start=start,
            summary=data.get("summary", "Investor follow-up"),
            description=data.get("description", "Scheduled via Rai."),
            attendees=[data["to"]] if data.get("to") else None,
        )
        return jsonify({"ok": True, "event": event})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/sheets-status")
def sheets_status():
    try:
        return jsonify({"connected": _sheets_connected()})
    except Exception as e:
        return jsonify({"connected": False, "error": str(e)})

@app.route("/api/connect-sheets")
def connect_sheets():
    try:
        from sheets_client import get_connect_link
        return jsonify({"url": get_connect_link()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/connect-gmail")
def connect_gmail():
    try:
        from gmail_client import get_client
        email = get_founder_email()
        if not email:
            return jsonify({"error": "No profile. Go to /settings first."}), 400
        client = get_client()
        client.actions.get_or_create_connected_account(connection_name=GMAIL_CONNECTION_NAME, identifier=email)
        link = client.actions.get_authorization_link(identifier=email, connection_name=GMAIL_CONNECTION_NAME)
        return jsonify({"url": link.link})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/setup", methods=["POST"])
def setup():
    data = request.json or {}
    email = data.get("email", "").strip()
    name  = data.get("name", "").strip()
    if not email:
        return jsonify({"error": "Email required"}), 400
    save_profile({"email": email, "name": name or email.split("@")[0]})
    return jsonify({"ok": True})

@app.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.json or {}
    if "permission" in data:
        db.set_setting("permission", data["permission"])
    return jsonify({"ok": True})

@app.route("/api/qr-image")
def qr_image():
    local_ip = _get_local_ip()
    url = f"http://{local_ip}:8080/invest"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _gmail_connected() -> bool:
    try:
        from gmail_client import get_client
        email = get_founder_email()
        if not email:
            return False
        client = get_client()
        account = client.actions.get_or_create_connected_account(
            connection_name=GMAIL_CONNECTION_NAME, identifier=email
        )
        ca = account.connected_account
        return bool(ca and ca.status in ("ACTIVE", "CONNECTOR_STATUS_ACTIVE"))
    except Exception:
        return False

def _calendar_connected() -> bool:
    try:
        from calendar_client import calendar_connected
        return calendar_connected()
    except Exception:
        return False

def _sheets_connected() -> bool:
    try:
        from sheets_client import sheets_connected
        return sheets_connected()
    except Exception:
        return False

def _create_gmail_draft(to: str, subject: str, body: str) -> dict:
    try:
        from gmail_client import create_draft
        result = create_draft(to=to, subject=subject, body=body)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _process_message(firm_id: str, firm_name: str, sender: str, text: str):
    """Background: embed message + update memory summary."""
    try:
        from memory import store_email_memory
        store_email_memory(
            firm_key=firm_id, email_id=f"{firm_id}-{sender}-{text[:20]}",
            sender=sender, subject="Investor message", body_snippet=text,
            date="", tier="warm"
        )
    except Exception:
        pass

    try:
        messages = db.get_firm_messages(firm_id, limit=10)
        history  = "\n".join(f"- {m['sender_name']}: {m['text']}" for m in messages)
        summary = _llm(
            f"Investor firm: {firm_name}\nConversation so far:\n{history}\n\n"
            "Summarize in exactly 3 short bullet points what this investor cares about, "
            "any commitments or conditions they mentioned, and the current relationship status. "
            "Return ONLY the 3 bullets starting with •",
            max_tokens=200
        )
        # derive tier from content
        tier = "hot" if any(w in summary.lower() for w in ["lead", "term sheet", "invest", "committed"]) \
             else "warm" if any(w in summary.lower() for w in ["interested", "follow", "traction", "check back"]) \
             else "cold"
        db.update_firm_memory(firm_id, firm_name, summary, tier)
    except Exception:
        pass

def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    port  = int(os.getenv("PORT", "8080"))
    # In debug mode the reloader forks two processes; only start the watcher in the child.
    # In production (debug=False) there's only one process, so always start it.
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        try:
            from inbox_watcher import start_watcher
            start_watcher()
        except Exception as e:
            print(f"[inbox] failed to start watcher: {e}")
    app.run(debug=debug, port=port, host="0.0.0.0")

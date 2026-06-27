import io
import json
import os
import socket
import threading

import qrcode
from anthropic import Anthropic
from flask import Flask, jsonify, render_template, request, send_file

import db
from config import ANTHROPIC_API_KEY, GMAIL_CONNECTION_NAME
from profile import get_founder_email, get_founder_name, require_profile, save_profile

app = Flask(__name__)
db.init_db()

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

_claude = Anthropic(api_key=ANTHROPIC_API_KEY)


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
    return render_template("settings.html", email=email, permission=permission, connected=connected)

@app.route("/qr")
def qr_view():
    local_ip = _get_local_ip()
    url = f"http://{local_ip}:8080/invest"
    return render_template("qr.html", url=url)


# ── API: Investor ──────────────────────────────────────────────────────────────

@app.route("/api/message", methods=["POST"])
def post_message():
    data = request.json or {}
    firm_id   = data.get("firm_id", "").strip()
    firm_name = FIRM_MAP.get(firm_id, firm_id)
    sender    = data.get("sender_name", "Investor").strip()
    text      = data.get("text", "").strip()

    if not firm_id or not text:
        return jsonify({"error": "firm_id and text required"}), 400

    db.add_message(firm_id, firm_name, sender, text)

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

@app.route("/api/brief", methods=["POST"])
def morning_brief():
    firms = db.get_all_firms()
    if not firms:
        return jsonify({"results": []})

    results = []
    for firm in firms:
        firm_id   = firm["firm_id"]
        firm_name = firm["firm_name"]
        messages  = db.get_firm_messages(firm_id, limit=10)
        msg_text  = "\n".join(f"- {m['sender_name']}: {m['text']}" for m in messages)

        # RAG: pull top memories from VectorAI
        rag_context = ""
        try:
            from memory import search_firm_memory
            hits = search_firm_memory(firm_id, "follow up commitment interest urgent traction", limit=4)
            if hits:
                rag_context = "\n".join(f"- [{h.get('tier','?')}] {h.get('snippet','')[:150]}" for h in hits)
        except Exception:
            pass

        prompt = f"""Investor firm: {firm_name}
Their messages:
{msg_text}

Memory context (most relevant past signals):
{rag_context or '(none yet)'}

You are helping a startup founder decide whether to follow up with this investor.
Return ONLY valid JSON:
{{
  "urgency": "high" | "medium" | "low",
  "reason": "<one sentence why>",
  "email_subject": "<subject line>",
  "email_body": "<draft follow-up email body, under 120 words, warm and specific>"
}}"""

        try:
            resp = _claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )
            import re
            raw = resp.content[0].text.strip()
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            result = json.loads(match.group()) if match else {}
        except Exception as e:
            result = {"urgency": "low", "reason": str(e), "email_subject": "", "email_body": ""}

        results.append({
            "firm_id":       firm_id,
            "firm_name":     firm_name,
            "urgency":       result.get("urgency", "low"),
            "reason":        result.get("reason", ""),
            "email_subject": result.get("email_subject", f"Following up — {firm_name}"),
            "email_body":    result.get("email_body", ""),
            "message_count": firm["message_count"],
            "last_contact":  firm["last_message_at"],
        })

    order = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda x: order.get(x["urgency"], 3))
    return jsonify({"results": results})


# ── API: Send / Draft email ────────────────────────────────────────────────────

@app.route("/api/send-email", methods=["POST"])
def send_email():
    data    = request.json or {}
    subject = data.get("subject", "Following up")
    body    = data.get("body", "")
    firm_id = data.get("firm_id", "")
    permission = db.get_setting("permission", "confirm")

    if permission == "auto_send":
        result = _create_gmail_draft(subject, body)
        return jsonify({"ok": True, "auto": True, "draft_url": "https://mail.google.com/mail/u/0/#drafts", **result})
    else:
        result = _create_gmail_draft(subject, body)
        return jsonify({"ok": True, "auto": False, "draft_url": "https://mail.google.com/mail/u/0/#drafts", **result})


# ── API: Gmail / Settings ──────────────────────────────────────────────────────

@app.route("/api/auth-status")
def auth_status():
    return jsonify({"connected": _gmail_connected()})

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
        return bool(ca and ca.status == "CONNECTOR_STATUS_ACTIVE")
    except Exception:
        return False

def _create_gmail_draft(subject: str, body: str) -> dict:
    try:
        from gmail_client import create_draft
        result = create_draft(to="", subject=subject, body=body)
        return {"gmail_result": result}
    except Exception as e:
        return {"gmail_error": str(e)}

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
        resp = _claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{"role": "user", "content":
                f"Investor firm: {firm_name}\nConversation so far:\n{history}\n\n"
                "Summarize in exactly 3 short bullet points what this investor cares about, "
                "any commitments or conditions they mentioned, and the current relationship status. "
                "Return ONLY the 3 bullets starting with •"}]
        )
        summary = resp.content[0].text.strip()
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
    app.run(debug=True, port=8080, host="0.0.0.0")

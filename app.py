from flask import Flask, request, jsonify, render_template, session
import json, os
from profile import load_profile, save_profile, get_founder_email, get_founder_name

app = Flask(__name__)
app.secret_key = os.urandom(24)


@app.route("/")
def index():
    profile = load_profile()
    return render_template("index.html", profile=profile)


@app.route("/api/setup", methods=["POST"])
def setup():
    data = request.json
    email = data.get("email", "").strip()
    name = data.get("name", "").strip()
    if not email:
        return jsonify({"error": "Email is required"}), 400
    save_profile({"email": email, "name": name or email.split("@")[0]})
    return jsonify({"ok": True})


@app.route("/api/auth-status")
def auth_status():
    try:
        from gmail_client import get_client, GMAIL_CONNECTION_NAME
        from config import GMAIL_CONNECTION_NAME as CN
        email = get_founder_email()
        if not email:
            return jsonify({"connected": False, "reason": "no_profile"})
        client = get_client()
        account = client.actions.get_or_create_connected_account(
            connection_name=CN, identifier=email
        )
        ca = account.connected_account
        connected = ca and ca.status == "CONNECTOR_STATUS_ACTIVE"
        return jsonify({"connected": bool(connected)})
    except Exception as e:
        return jsonify({"connected": False, "reason": str(e)})


@app.route("/api/connect-gmail")
def connect_gmail():
    try:
        from gmail_client import get_client
        from config import GMAIL_CONNECTION_NAME as CN
        email = get_founder_email()
        if not email:
            return jsonify({"error": "No profile set up"}), 400
        client = get_client()
        client.actions.get_or_create_connected_account(connection_name=CN, identifier=email)
        link = client.actions.get_authorization_link(identifier=email, connection_name=CN)
        return jsonify({"url": link.url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/run", methods=["POST"])
def run_morning_job():
    data = request.json or {}
    traction = {
        "MRR": data.get("mrr", ""),
        "Enterprise pilots": data.get("pilots", ""),
        "Usage growth": data.get("usage", ""),
        "Churn": data.get("churn", ""),
    }
    traction = {k: v for k, v in traction.items() if v}

    try:
        results = _run_job(traction)
        return jsonify({"ok": True, "results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _run_job(traction: dict) -> list[dict]:
    from gmail_client import fetch_investor_emails
    from memory import store_email_memory, get_firm_summary
    from extractor import extract_intent
    from drafter import decide_action, draft_followup
    import json as _json

    with open("investors.json") as f:
        investors = _json.load(f)

    all_domains = [d for firm in investors["firms"] for d in firm["domains"]]
    individual_emails = investors.get("individual_emails", [])

    emails = fetch_investor_emails(all_domains, individual_emails)

    firm_map = {f["short"]: f["name"] for f in investors["firms"]}

    for email in emails:
        sender = email.get("from", email.get("sender", "unknown"))
        subject = email.get("subject", "(no subject)")
        body = email.get("body", email.get("snippet", ""))
        date = email.get("date", "")
        email_id = email.get("id", subject + date)
        source = email.get("_source_domain", "")

        firm_key = "unknown"
        for firm in investors["firms"]:
            if any(d in source or d in sender for d in firm["domains"]):
                firm_key = firm["short"]
                break

        intent = extract_intent(sender, subject, body)
        store_email_memory(
            firm_key=firm_key, email_id=email_id, sender=sender,
            subject=subject, body_snippet=body[:800], date=date,
            tier=intent.get("tier", "warm"),
            intent_type=intent.get("intent_type", "unknown"),
            follow_up_days=intent.get("follow_up_days"),
            condition=intent.get("condition"),
            raw_intent=intent,
        )

    urgency_rank = {"high": 0, "medium": 1, "low": 2}
    results = []

    for firm in investors["firms"]:
        firm_key = firm["short"]
        firm_name = firm["name"]
        summary = get_firm_summary(firm_key)
        if not summary["recent_context"] and not summary["hot_memories"]:
            continue

        decision = decide_action(summary, traction)
        action = decision.get("action", "wait")
        memories = summary["hot_memories"] + summary["recent_context"]
        recent = memories[0] if memories else {}

        draft = None
        if action == "follow_up":
            key_concern_raw = recent.get("intent", "{}")
            try:
                key_concern_obj = _json.loads(key_concern_raw) if isinstance(key_concern_raw, str) else key_concern_raw
            except Exception:
                key_concern_obj = {}
            concern = key_concern_obj.get("key_concern", "general interest")
            investor_name = recent.get("sender", "Investor").split("<")[0].strip()
            draft = draft_followup(
                investor_name=investor_name,
                firm_name=firm_name,
                key_concern=concern,
                traction_summary="; ".join(f"{k}: {v}" for k, v in traction.items()),
                memory_context=memories,
                founder_name=get_founder_name(),
            )

        results.append({
            "firm": firm_name,
            "firm_key": firm_key,
            "action": action,
            "urgency": decision.get("urgency", "low"),
            "reason": decision.get("reason", ""),
            "last_contact": summary.get("last_contact"),
            "draft": draft,
            "memories": [
                {"snippet": m.get("snippet", "")[:200], "tier": m.get("tier"), "date": m.get("date", "")}
                for m in memories[:3]
            ],
        })

    results.sort(key=lambda x: urgency_rank.get(x["urgency"], 3))
    return results


if __name__ == "__main__":
    app.run(debug=True, port=8080)

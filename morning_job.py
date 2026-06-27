"""
Morning job: ingest new investor emails → update memory → reason per firm → output drafts.
"""
import json
from datetime import datetime

from gmail_client import fetch_investor_emails, create_draft
from memory import store_email_memory, search_firm_memory, get_firm_summary
from extractor import extract_intent
from drafter import decide_action, draft_followup


def load_investors() -> dict:
    with open("investors.json") as f:
        return json.load(f)


def resolve_firm(email: dict, firms: list[dict]) -> tuple[str, str]:
    """Return (firm_short, firm_name) by matching _source_domain to investor list."""
    source = email.get("_source_domain", "")
    sender = email.get("from", email.get("sender", ""))

    for firm in firms:
        for domain in firm["domains"]:
            if domain in source or domain in sender:
                return firm["short"], firm["name"]

    # fallback: use domain as key
    key = source.split("@")[-1].replace(".", "_") if "@" in source else source
    return key, source


def ingest_emails(traction: dict):
    """Pull new emails, extract intent, store in VectorAI."""
    investors = load_investors()
    all_domains = [d for firm in investors["firms"] for d in firm["domains"]]
    individual_emails = investors.get("individual_emails", [])

    print(f"[{datetime.now():%H:%M}] Fetching investor emails...")
    emails = fetch_investor_emails(all_domains, individual_emails)
    print(f"  Found {len(emails)} emails")

    for email in emails:
        firm_key, firm_name = resolve_firm(email, investors["firms"])
        sender = email.get("from", email.get("sender", "unknown"))
        subject = email.get("subject", "(no subject)")
        body = email.get("body", email.get("snippet", ""))
        date = email.get("date", datetime.utcnow().isoformat())
        email_id = email.get("id", subject + date)

        intent = extract_intent(sender, subject, body)

        store_email_memory(
            firm_key=firm_key,
            email_id=email_id,
            sender=sender,
            subject=subject,
            body_snippet=body[:800],
            date=date,
            tier=intent.get("tier", "warm"),
            intent_type=intent.get("intent_type", "unknown"),
            follow_up_days=intent.get("follow_up_days"),
            condition=intent.get("condition"),
            raw_intent=intent,
        )
        print(f"  Stored: [{firm_key}] {subject[:50]} — {intent.get('tier')} / {intent.get('intent_type')}")


def run_morning_job(traction: dict, founder_name: str = "the founder", dry_run: bool = True):
    """
    Full morning job:
    1. Ingest new emails
    2. Per firm: decide action + draft if needed
    3. Print priority list; create drafts in Gmail if not dry_run
    """
    ingest_emails(traction)

    investors = load_investors()
    firm_keys = list({firm["short"] for firm in investors["firms"]})

    print(f"\n[{datetime.now():%H:%M}] Reasoning across {len(firm_keys)} firms...\n")

    actions = []
    for firm_key in firm_keys:
        summary = get_firm_summary(firm_key)
        if not summary["recent_context"] and not summary["hot_memories"]:
            continue

        decision = decide_action(summary, traction)
        actions.append((decision.get("urgency", "low"), firm_key, summary, decision))

    # Sort by urgency
    urgency_rank = {"high": 0, "medium": 1, "low": 2}
    actions.sort(key=lambda x: urgency_rank.get(x[0], 3))

    print("=" * 60)
    print("MORNING INVESTOR BRIEF")
    print("=" * 60)

    firm_names = {f["short"]: f["name"] for f in investors["firms"]}

    for urgency, firm_key, summary, decision in actions:
        firm_name = firm_names.get(firm_key, firm_key)
        action = decision.get("action", "wait")
        reason = decision.get("reason", "")
        last = summary.get("last_contact", "unknown")

        print(f"\n[{urgency.upper()}] {firm_name}")
        print(f"  Action    : {action}")
        print(f"  Reason    : {reason}")
        print(f"  Last touch: {last}")

        if action == "follow_up":
            memories = summary["hot_memories"] + summary["recent_context"]
            key_concern = (memories[0].get("intent", {}) if memories else {})
            if isinstance(key_concern, str):
                import json as _json
                try:
                    key_concern = _json.loads(key_concern)
                except Exception:
                    key_concern = {}
            concern_text = key_concern.get("key_concern", "general interest")

            # Guess investor contact from most recent memory
            recent = memories[0] if memories else {}
            investor_name = recent.get("sender", "Investor").split("<")[0].strip()

            draft = draft_followup(
                investor_name=investor_name,
                firm_name=firm_name,
                key_concern=concern_text,
                traction_summary=_format_traction(traction),
                memory_context=memories,
                founder_name=founder_name,
            )
            print(f"\n  --- DRAFT EMAIL ---")
            print(f"  To: {investor_name} ({firm_name})")
            print()
            for line in draft.split("\n"):
                print(f"  {line}")
            print(f"  --- END DRAFT ---")

            if not dry_run:
                to_email = recent.get("sender", "")
                if "<" in to_email:
                    to_email = to_email.split("<")[1].rstrip(">")
                if to_email:
                    create_draft(to=to_email, subject=f"Quick update", body=draft)
                    print(f"  ✓ Draft saved to Gmail")

    print("\n" + "=" * 60)
    print(f"Done. {len([a for a in actions if a[2]])} firms reviewed.")


def _format_traction(traction: dict) -> str:
    return "; ".join(f"{k}: {v}" for k, v in traction.items())

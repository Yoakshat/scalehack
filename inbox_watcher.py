"""Live Gmail ingestion: poll the founder's inbox, keep only startup-relevant
emails, and push them onto the dashboard (same SQLite tables as the QR flow).
"""
import os
import re
import threading
import time
import traceback

import db
from extractor import classify_email
from gmail_client import fetch_recent_emails

POLL_SECONDS = int(os.getenv("INBOX_POLL_SECONDS", "20"))
# On first start, mark whatever is already in the inbox as seen (without showing it),
# so the demo starts clean and only emails that arrive AFTER launch pop up live.
BASELINE_ON_START = os.getenv("INBOX_BASELINE_ON_START", "true").lower() == "true"

_started = False
_last_status = {"running": False, "last_poll": None, "relevant": 0, "ignored": 0, "error": None}


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return slug or "unknown"


def get_status() -> dict:
    return dict(_last_status)


def _ingest_email(em: dict) -> str:
    """Classify one parsed email and store it if relevant. Returns 'relevant'|'ignored'|'skip'."""
    result = classify_email(em["from"], em["subject"], em["snippet"])
    relevant = bool(result.get("relevant"))
    db.mark_email_processed(em["id"], relevant)
    if not relevant:
        return "ignored"

    company = (result.get("company") or "").strip()
    label = company or em["sender_name"] or em["sender_email"] or "Unknown"
    firm_id = _slugify(company or em["sender_email"] or em["sender_name"])
    text = result.get("summary") or em["snippet"] or em["subject"]

    db.add_message(firm_id, label, em["sender_name"] or label, text, em["sender_email"])
    db.update_firm_memory(firm_id, label, result.get("summary", ""), result.get("tier", "warm"))
    return "relevant"


def sync_once(baseline: bool = False) -> dict:
    """Process new inbox emails once. If baseline, mark unseen emails as seen without ingesting."""
    emails = fetch_recent_emails(max_results=15)
    relevant = ignored = 0
    for em in emails:
        if not em["id"] or db.is_email_processed(em["id"]):
            continue
        if baseline:
            db.mark_email_processed(em["id"], False)
            continue
        try:
            outcome = _ingest_email(em)
            if outcome == "relevant":
                relevant += 1
                print(f"[inbox] + {em['subject'][:60]} (from {em['sender_email']})")
            elif outcome == "ignored":
                ignored += 1
                print(f"[inbox] ignored (not relevant): {em['subject'][:60]}")
        except Exception:
            traceback.print_exc()
    _last_status.update(last_poll=time.strftime("%H:%M:%S"), relevant=relevant, ignored=ignored, error=None)
    return {"relevant": relevant, "ignored": ignored, "scanned": len(emails)}


def _loop():
    if BASELINE_ON_START:
        try:
            n = sync_once(baseline=True)
            print(f"[inbox] baseline complete — {n['scanned']} existing emails marked seen")
        except Exception as e:
            print(f"[inbox] baseline failed (will ingest normally): {e}")
    while True:
        try:
            sync_once()
            _last_status["running"] = True
        except Exception as e:
            _last_status.update(running=False, error=str(e))
            traceback.print_exc()
        time.sleep(POLL_SECONDS)


def start_watcher():
    """Start the background polling thread once."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="inbox-watcher").start()
    print(f"[inbox] watcher started (every {POLL_SECONDS}s, baseline={BASELINE_ON_START})")

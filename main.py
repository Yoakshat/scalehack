#!/usr/bin/env python
"""
scalehack — investor follow-up agent
Usage:
  python main.py auth              # connect Gmail via ScaleKit OAuth
  python main.py cal-auth          # connect Google Calendar via ScaleKit OAuth
  python main.py run               # run morning job (dry run, prints drafts)
  python main.py run --send        # run morning job + save drafts to Gmail
  python main.py add-email <addr>  # authorize an individual investor email
  python main.py add-firm <name> <domain>  # add a new firm to investor list
"""
import sys
import json


def cmd_auth():
    from gmail_client import ensure_gmail_connected
    connected = ensure_gmail_connected()
    if connected:
        print("Gmail is connected and active.")
    else:
        print("Auth URL opened. Complete the flow, then run this command again.")


def cmd_cal_auth():
    from calendar_client import ensure_calendar_connected
    connected = ensure_calendar_connected()
    if connected:
        print("Google Calendar is connected and active.")
    else:
        print("Auth URL opened. Complete the flow, then run this command again.")


def cmd_run(send: bool = False):
    from gmail_client import ensure_gmail_connected
    if not ensure_gmail_connected():
        print("Gmail not connected. Run: python main.py auth")
        return

    # Traction data — edit these or load from a spreadsheet later
    traction = {
        "MRR": "$54k (was $31k, +74%)",
        "Enterprise pilots": "4 (was 1)",
        "Usage growth": "+42% MoM",
        "Churn": "3% (was 6%)",
    }

    from profile import get_founder_name
    from morning_job import run_morning_job
    run_morning_job(traction=traction, founder_name=get_founder_name(), dry_run=not send)


def cmd_add_email(addr: str):
    with open("investors.json") as f:
        data = json.load(f)
    if addr not in data["individual_emails"]:
        data["individual_emails"].append(addr)
        with open("investors.json", "w") as f:
            json.dump(data, f, indent=2)
        print(f"Added {addr} to authorized investor emails.")
    else:
        print(f"{addr} already in list.")


def cmd_add_firm(name: str, domain: str):
    with open("investors.json") as f:
        data = json.load(f)
    short = name.lower().replace(" ", "_")
    data["firms"].append({"name": name, "short": short, "domains": [domain]})
    with open("investors.json", "w") as f:
        json.dump(data, f, indent=2)
    print(f"Added firm: {name} ({domain})")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "auth":
        cmd_auth()
    elif args[0] == "cal-auth":
        cmd_cal_auth()
    elif args[0] == "run":
        cmd_run(send="--send" in args)
    elif args[0] == "add-email" and len(args) >= 2:
        cmd_add_email(args[1])
    elif args[0] == "add-firm" and len(args) >= 3:
        cmd_add_firm(args[1], args[2])
    else:
        print(__doc__)

"""Local per-founder profile — stored in profile.json, never committed."""
import json
import os

PROFILE_PATH = os.path.join(os.path.dirname(__file__), "profile.json")


def load_profile() -> dict:
    if os.path.exists(PROFILE_PATH):
        with open(PROFILE_PATH) as f:
            return json.load(f)
    return {}


def save_profile(data: dict):
    with open(PROFILE_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get_founder_email() -> str | None:
    return load_profile().get("email")


def set_founder_email(email: str):
    profile = load_profile()
    profile["email"] = email
    save_profile(profile)


def get_founder_name() -> str:
    return load_profile().get("name", "the founder")


def set_founder_name(name: str):
    profile = load_profile()
    profile["name"] = name
    save_profile(profile)


def require_profile() -> dict:
    """Prompt for email/name if not set. Returns profile dict."""
    profile = load_profile()
    changed = False

    if not profile.get("email"):
        email = input("Enter your email (used to connect your Gmail): ").strip()
        profile["email"] = email
        changed = True

    if not profile.get("name"):
        name = input("Your first name (for email drafts): ").strip()
        profile["name"] = name
        changed = True

    if changed:
        save_profile(profile)

    return profile

import webbrowser
from scalekit import ScalekitClient
from config import (
    SCALEKIT_ENV_URL, SCALEKIT_CLIENT_ID, SCALEKIT_CLIENT_SECRET,
    GMAIL_CONNECTION_NAME,
)
from profile import require_profile

_client: ScalekitClient | None = None

def get_client() -> ScalekitClient:
    global _client
    if _client is None:
        _client = ScalekitClient(SCALEKIT_ENV_URL, SCALEKIT_CLIENT_ID, SCALEKIT_CLIENT_SECRET)
    return _client


def ensure_gmail_connected() -> bool:
    """Returns True if Gmail is active, otherwise opens OAuth URL and returns False."""
    profile = require_profile()
    identifier = profile["email"]
    client = get_client()

    account = client.actions.get_or_create_connected_account(
        connection_name=GMAIL_CONNECTION_NAME,
        identifier=identifier,
    )
    ca = account.connected_account
    status = ca.status if ca else None

    if status == "CONNECTOR_STATUS_ACTIVE":
        return True

    link = client.actions.get_authorization_link(
        identifier=identifier,
        connection_name=GMAIL_CONNECTION_NAME,
    )
    print(f"\nGmail not connected. Opening auth URL...\n{link.link}\n")
    webbrowser.open(link.link)
    return False


def _get_identifier() -> str:
    from profile import get_founder_email
    email = get_founder_email()
    if not email:
        raise RuntimeError("No founder profile. Run: python main.py auth")
    return email


def fetch_emails(query: str = "", max_results: int = 50) -> list[dict]:
    """Fetch emails matching query. Returns list of email dicts."""
    client = get_client()
    response = client.actions.execute_tool(
        tool_name="gmail_fetch_mails",
        identifier=_get_identifier(),
        connection_name=GMAIL_CONNECTION_NAME,
        tool_input={"query": query, "max_results": max_results},
    )
    data = response.data or {}
    return data.get("emails", data.get("messages", []))


def fetch_investor_emails(investor_domains: list[str], individual_emails: list[str]) -> list[dict]:
    """Fetch emails from known investor domains + individual authorized emails."""
    all_emails = []

    for domain in investor_domains:
        results = fetch_emails(query=f"from:@{domain}", max_results=20)
        for email in results:
            email["_source_domain"] = domain
        all_emails.extend(results)

    for addr in individual_emails:
        results = fetch_emails(query=f"from:{addr}", max_results=20)
        for email in results:
            email["_source_domain"] = addr
        all_emails.extend(results)

    return all_emails


def create_draft(to: str, subject: str, body: str) -> dict:
    """Create a Gmail draft on behalf of the founder."""
    client = get_client()
    response = client.actions.execute_tool(
        tool_name="gmail_create_draft",
        identifier=_get_identifier(),
        connection_name=GMAIL_CONNECTION_NAME,
        tool_input={"to": to, "subject": subject, "body": body},
    )
    return response.data or {}

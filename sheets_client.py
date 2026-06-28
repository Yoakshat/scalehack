"""Google Sheets integration via ScaleKit AgentKit.

Reads a founder-configured spreadsheet and returns its content
as text for embedding into LLM prompts.
"""
import re
import webbrowser
from config import (
    SCALEKIT_ENV_URL, SCALEKIT_CLIENT_ID, SCALEKIT_CLIENT_SECRET,
    SHEETS_CONNECTION_NAME,
)
from gmail_client import get_client
from profile import get_founder_email

_ACTIVE_STATUSES = {"ACTIVE", "CONNECTOR_STATUS_ACTIVE"}


def _get_identifier() -> str:
    email = get_founder_email()
    if not email:
        raise RuntimeError("No founder profile. Go to /settings first.")
    return email


def _extract_spreadsheet_id(url_or_id: str) -> str:
    """Accept either a full Sheets URL or a raw spreadsheet ID."""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url_or_id)
    return m.group(1) if m else url_or_id.strip()


def sheets_connected() -> bool:
    try:
        identifier = get_founder_email()
        if not identifier:
            return False
        client = get_client()
        account = client.actions.get_or_create_connected_account(
            connection_name=SHEETS_CONNECTION_NAME, identifier=identifier
        )
        ca = account.connected_account
        return bool(ca and ca.status in _ACTIVE_STATUSES)
    except Exception:
        return False


def ensure_sheets_connected() -> bool:
    identifier = _get_identifier()
    client = get_client()
    account = client.actions.get_or_create_connected_account(
        connection_name=SHEETS_CONNECTION_NAME, identifier=identifier
    )
    ca = account.connected_account
    if ca and ca.status in _ACTIVE_STATUSES:
        return True
    link = client.actions.get_authorization_link(
        identifier=identifier, connection_name=SHEETS_CONNECTION_NAME
    )
    webbrowser.open(link.link)
    return False


def get_connect_link() -> str:
    identifier = _get_identifier()
    client = get_client()
    client.actions.get_or_create_connected_account(
        connection_name=SHEETS_CONNECTION_NAME, identifier=identifier
    )
    link = client.actions.get_authorization_link(
        identifier=identifier, connection_name=SHEETS_CONNECTION_NAME
    )
    return link.link


def _read_sheet(spreadsheet_id: str, label: str = "Traction") -> str:
    """Read a spreadsheet's values and return as formatted text."""
    client = get_client()
    identifier = _get_identifier()

    for tool_name, tool_input in [
        ("googlesheets_get_values", {"spreadsheet_id": spreadsheet_id, "range": "A1:Z100"}),
        ("googlesheets_read_spreadsheet", {"spreadsheet_id": spreadsheet_id}),
    ]:
        try:
            resp = client.actions.execute_tool(
                tool_name=tool_name,
                identifier=identifier,
                connection_name=SHEETS_CONNECTION_NAME,
                tool_input=tool_input,
            )
            data = resp.data or {}
            rows = data.get("values", data.get("rows", data.get("data", [])))
            if rows and isinstance(rows, list):
                lines = []
                for row in rows[:50]:
                    if isinstance(row, list):
                        line = " | ".join(str(c) for c in row if c != "")
                        if line:
                            lines.append(line)
                    elif isinstance(row, dict):
                        lines.append(" | ".join(f"{k}: {v}" for k, v in row.items()))
                if lines:
                    return f"Sheet: {label}\n" + "\n".join(lines)
        except Exception:
            continue
    return ""


def fetch_startup_data(spreadsheet_url: str | None = None) -> list[dict]:
    """Fetch data from the configured spreadsheet.

    Returns list of {source, name, text} dicts ready for embedding.
    spreadsheet_url can be a full URL or raw spreadsheet ID.
    """
    if not spreadsheet_url:
        return []

    spreadsheet_id = _extract_spreadsheet_id(spreadsheet_url)
    if not spreadsheet_id:
        return []

    text = _read_sheet(spreadsheet_id, label="Startup Metrics")
    if text:
        return [{"source": "googlesheets", "name": "Startup Metrics", "text": text}]
    return []

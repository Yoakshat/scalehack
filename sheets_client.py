"""Google Sheets integration via ScaleKit AgentKit.

Reads spreadsheets from the founder's 'startup' folder and returns
their content as text chunks for embedding into VectorAI.
"""
import webbrowser
from config import (
    SCALEKIT_ENV_URL, SCALEKIT_CLIENT_ID, SCALEKIT_CLIENT_SECRET,
    SHEETS_CONNECTION_NAME,
)
from gmail_client import get_client
from profile import get_founder_email

_ACTIVE_STATUSES = {"ACTIVE", "CONNECTOR_STATUS_ACTIVE"}
STARTUP_FOLDER = "startup"


def _get_identifier() -> str:
    email = get_founder_email()
    if not email:
        raise RuntimeError("No founder profile. Go to /settings first.")
    return email


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


def _list_startup_files() -> list[dict]:
    """List spreadsheet files inside the 'startup' folder."""
    client = get_client()
    identifier = _get_identifier()

    for tool_name, tool_input in [
        ("googlesheets_list_files", {"folder_name": STARTUP_FOLDER}),
        ("googlesheets_list_spreadsheets", {"query": f"'{STARTUP_FOLDER}' in parents"}),
        ("googledrive_list_files", {"folder_name": STARTUP_FOLDER, "mime_type": "application/vnd.google-apps.spreadsheet"}),
    ]:
        try:
            resp = client.actions.execute_tool(
                tool_name=tool_name,
                identifier=identifier,
                connection_name=SHEETS_CONNECTION_NAME,
                tool_input=tool_input,
            )
            data = resp.data or {}
            files = data.get("files", data.get("spreadsheets", data.get("items", [])))
            if isinstance(files, list):
                return files
        except Exception:
            continue
    return []


def _read_sheet(file_id: str, file_name: str) -> str:
    """Read a spreadsheet's values and return as formatted text."""
    client = get_client()
    identifier = _get_identifier()

    for tool_name, tool_input in [
        ("googlesheets_get_values", {"spreadsheet_id": file_id, "range": "A1:Z100"}),
        ("googlesheets_read_spreadsheet", {"spreadsheet_id": file_id}),
        ("googlesheets_read_sheet", {"file_id": file_id, "range": "A1:Z100"}),
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
                        lines.append(" | ".join(str(c) for c in row if c))
                    elif isinstance(row, dict):
                        lines.append(" | ".join(f"{k}: {v}" for k, v in row.items()))
                return f"Sheet: {file_name}\n" + "\n".join(lines)
        except Exception:
            continue
    return ""


def fetch_startup_data(since_iso: str | None = None) -> list[dict]:
    """Fetch sheets from the startup folder, optionally delta-filtered by modifiedTime.

    Returns list of {source, name, text} dicts ready for embedding.
    """
    files = _list_startup_files()
    chunks = []
    for f in files[:10]:
        file_id   = f.get("id") or f.get("spreadsheetId") or f.get("file_id", "")
        file_name = f.get("name") or f.get("title", file_id)
        if not file_id:
            continue
        if since_iso:
            modified = f.get("modifiedTime") or f.get("modified_time") or f.get("modifiedAt", "")
            if modified and modified < since_iso:
                continue
        text = _read_sheet(file_id, file_name)
        if text:
            chunks.append({"source": "googlesheets", "name": file_name, "text": text})
    return chunks

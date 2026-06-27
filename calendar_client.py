"""Google Calendar integration via Scalekit AgentKit.

Mirrors gmail_client.py: same get_client(), connect/auth flow, and
execute_tool() calls — but for the GOOGLECALENDAR connector.
"""
import webbrowser
from datetime import datetime, timedelta, timezone

from config import (
    SCALEKIT_ENV_URL, SCALEKIT_CLIENT_ID, SCALEKIT_CLIENT_SECRET,
    GCAL_CONNECTION_NAME,
)
from gmail_client import get_client  # reuse the same singleton ScalekitClient
from profile import get_founder_email

# Scalekit SDK has reported both of these for an active connection across versions.
_ACTIVE_STATUSES = {"ACTIVE", "CONNECTOR_STATUS_ACTIVE"}

# Business-hour slot template (local time, 24h)
_SLOT_HOURS = [(9, 0), (10, 30), (11, 0), (13, 0), (14, 0), (15, 0)]
_SLOT_MINUTES = 30
_TIMEZONE = "America/Los_Angeles"


def _get_identifier() -> str:
    email = get_founder_email()
    if not email:
        raise RuntimeError("No founder profile. Go to /settings first.")
    return email


def ensure_calendar_connected() -> bool:
    """Returns True if Google Calendar is active, else opens OAuth URL and returns False."""
    identifier = _get_identifier()
    client = get_client()

    account = client.actions.get_or_create_connected_account(
        connection_name=GCAL_CONNECTION_NAME,
        identifier=identifier,
    )
    ca = account.connected_account
    if ca and ca.status in _ACTIVE_STATUSES:
        return True

    link = client.actions.get_authorization_link(
        identifier=identifier,
        connection_name=GCAL_CONNECTION_NAME,
    )
    print(f"\nCalendar not connected. Opening auth URL...\n{link.link}\n")
    webbrowser.open(link.link)
    return False


def calendar_connected() -> bool:
    """Non-mutating status check for the founder's Google Calendar."""
    try:
        identifier = get_founder_email()
        if not identifier:
            return False
        client = get_client()
        account = client.actions.get_or_create_connected_account(
            connection_name=GCAL_CONNECTION_NAME, identifier=identifier
        )
        ca = account.connected_account
        return bool(ca and ca.status in _ACTIVE_STATUSES)
    except Exception:
        return False


def get_connect_link() -> str:
    """Return an OAuth authorization link for connecting Google Calendar."""
    identifier = _get_identifier()
    client = get_client()
    client.actions.get_or_create_connected_account(
        connection_name=GCAL_CONNECTION_NAME, identifier=identifier
    )
    link = client.actions.get_authorization_link(
        identifier=identifier, connection_name=GCAL_CONNECTION_NAME
    )
    return link.link


def _list_busy(days_ahead: int = 14) -> list[dict]:
    """Fetch events in the next `days_ahead` days as busy blocks."""
    client = get_client()
    now = datetime.now(timezone.utc)
    time_min = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    time_max = time_min + timedelta(days=days_ahead)

    response = client.actions.execute_tool(
        tool_name="googlecalendar_list_events",
        identifier=_get_identifier(),
        connection_name=GCAL_CONNECTION_NAME,
        tool_input={
            "time_min": time_min.isoformat(),
            "time_max": time_max.isoformat(),
            "max_results": 50,
            "single_events": True,
            "order_by": "startTime",
        },
    )
    data = response.data or {}
    events = data.get("events", data.get("items", []))
    busy = []
    for ev in events:
        start = (ev.get("start") or {}).get("dateTime")
        end = (ev.get("end") or {}).get("dateTime")
        if start and end:
            busy.append({"start": start, "end": end})
    return busy


def _overlaps(start: datetime, end: datetime, busy: list[dict]) -> bool:
    for block in busy:
        try:
            b_start = datetime.fromisoformat(block["start"])
            b_end = datetime.fromisoformat(block["end"])
        except (ValueError, KeyError):
            continue
        if start < b_end and end > b_start:
            return True
    return False


def get_availability(limit: int = 3) -> list[dict]:
    """Return up to `limit` open 30-min weekday slots, avoiding existing events.

    Each slot: {id, label, start (RFC3339), end (RFC3339)}.
    """
    busy = _list_busy()
    slots: list[dict] = []
    base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

    for day_offset in range(14):
        if len(slots) >= limit:
            break
        day = base + timedelta(days=day_offset)
        if day.weekday() >= 5:  # skip Sat/Sun
            continue
        for hour, minute in _SLOT_HOURS:
            start = day.replace(hour=hour, minute=minute)
            end = start + timedelta(minutes=_SLOT_MINUTES)
            # Compare against busy blocks (which are tz-aware) using naive local — best effort
            start_aware = start.astimezone() if start.tzinfo else start.replace(tzinfo=datetime.now().astimezone().tzinfo)
            end_aware = end.astimezone() if end.tzinfo else end.replace(tzinfo=datetime.now().astimezone().tzinfo)
            if _overlaps(start_aware, end_aware, busy):
                continue
            slots.append({
                "id": f"slot-{start.strftime('%Y%m%dT%H%M')}",
                "label": start.strftime("%a %b %-d, %-I:%M %p"),
                "start": start_aware.isoformat(),
                "end": end_aware.isoformat(),
            })
            if len(slots) >= limit:
                break
    return slots


def create_event(start: str, summary: str, description: str = "",
                 duration_minutes: int = 30, attendees: list[str] | None = None) -> dict:
    """Create a calendar event. `start` must be RFC3339 (e.g. 2026-07-01T15:00:00-07:00)."""
    client = get_client()
    tool_input = {
        "summary": summary,
        "start_datetime": start,
        "event_duration_minutes": duration_minutes,
        "timezone": _TIMEZONE,
    }
    if description:
        tool_input["description"] = description
    if attendees:
        tool_input["attendees_emails"] = attendees

    response = client.actions.execute_tool(
        tool_name="googlecalendar_create_event",
        identifier=_get_identifier(),
        connection_name=GCAL_CONNECTION_NAME,
        tool_input=tool_input,
    )
    data = response.data or {}
    event = data.get("event", data)
    return {
        "id": event.get("id"),
        "html_link": event.get("htmlLink") or event.get("html_link"),
        "summary": event.get("summary", summary),
    }

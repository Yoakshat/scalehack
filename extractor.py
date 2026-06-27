import json
import re
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY

_client: Anthropic | None = None

SYSTEM_PROMPT = """You are an expert at analyzing investor emails for a startup founder.
Extract structured intent from the email and return ONLY valid JSON with this schema:
{
  "tier": "hot|warm|cold",
  "intent_type": "commitment|interest|pass|update|intro|other",
  "follow_up_days": <integer or null>,
  "condition": "<string describing metric/event trigger or null>",
  "key_concern": "<what the investor cared about most, 1 sentence>",
  "sentiment": "positive|neutral|negative",
  "summary": "<2-sentence summary of the email>",
  "recommended_action": "follow_up_now|follow_up_later|wait|no_action",
  "reason": "<why this action, 1 sentence>"
}

Tier rules:
- hot: explicit commitment, hard date, strong interest ("want to lead", "let's do a term sheet")
- warm: soft interest, check-in request, open questions, wants to see traction
- cold: pass, no response signal, generic reply

Return ONLY the JSON object. No explanation."""


def _claude() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def extract_intent(sender: str, subject: str, body: str) -> dict:
    """Run Claude over a single email and return structured intent."""
    user_msg = f"Sender: {sender}\nSubject: {subject}\n\nEmail body:\n{body[:3000]}"
    response = _claude().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = response.content[0].text.strip()
    result = _parse_json(raw)
    result["_raw"] = raw
    return result

import json
import re
from openai import OpenAI
from config import DEEPSEEK_API_KEY

_client: OpenAI | None = None

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


def _claude() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    return _client


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


CLASSIFY_PROMPT = """You screen a startup founder's email inbox for a live fundraising dashboard.

Decide if the email is RELEVANT based on its CONTENT AND INTENT — not on how the sender's
address looks. Senders may write from personal Gmail or .edu addresses, casually ("hey"),
and still be investors, customers, or partners. Judge what the message is ABOUT.

RELEVANT = the message concerns the founder's startup/business in any way: investor or VC
interest/feedback/passing, fundraising, product or traction feedback, a customer or sales
inquiry, partnership, hiring/recruiting, intros, or press. Even short or blunt notes like
"you need to ship faster" or "interested, let's talk" count if they are about the startup.

NOT RELEVANT = clearly personal/non-business (family/friends making plans), automated system
mail (security codes, login alerts, receipts, calendar invites), newsletters, and marketing/spam.

When unsure but the message plausibly touches the startup or fundraising, lean RELEVANT.

Return ONLY valid JSON:
{
  "relevant": true|false,
  "company": "<the company/firm the sender represents, '' if unclear or personal>",
  "person": "<sender's display name>",
  "tier": "hot|warm|cold",
  "intent_type": "commitment|interest|pass|update|intro|customer|recruiting|partnership|press|other",
  "key_concern": "<what they care about or are asking, 1 sentence>",
  "summary": "<2-sentence summary of the email>",
  "recommended_action": "follow_up_now|follow_up_later|wait|no_action",
  "reason": "<why this action, 1 sentence>"
}

Tier rules: hot = explicit commitment/strong interest/hard date; warm = soft interest, questions,
wants traction; cold = pass or generic. If not relevant, set relevant=false and the rest can be empty.
Return ONLY the JSON object."""


def classify_email(sender: str, subject: str, body: str) -> dict:
    """Relevance gate + structured extraction in one DeepSeek call."""
    user_msg = f"Sender: {sender}\nSubject: {subject}\n\nEmail body:\n{body[:2500]}"
    response = _claude().chat.completions.create(
        model="deepseek-chat",
        max_tokens=420,
        messages=[{"role": "system", "content": CLASSIFY_PROMPT}, {"role": "user", "content": user_msg}],
    )
    return _parse_json(response.choices[0].message.content.strip())


def extract_intent(sender: str, subject: str, body: str) -> dict:
    """Run Claude over a single email and return structured intent."""
    user_msg = f"Sender: {sender}\nSubject: {subject}\n\nEmail body:\n{body[:3000]}"
    response = _claude().chat.completions.create(
        model="deepseek-chat",
        max_tokens=512,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_msg}],
    )
    raw = response.choices[0].message.content.strip()
    result = _parse_json(raw)
    result["_raw"] = raw
    return result

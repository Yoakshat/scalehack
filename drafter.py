from openai import OpenAI
from config import DEEPSEEK_API_KEY

_client: OpenAI | None = None

SYSTEM_PROMPT = """You are a founder's ghostwriter drafting investor follow-up emails.
Write in a direct, confident, warm tone — never sycophantic.
The email should:
- Reference something specific the investor said or cared about last time
- Lead with the most compelling traction update
- Be concise (under 150 words)
- End with a clear, low-friction ask (call, coffee, quick update)
Return ONLY the email body text. No subject line. No "Here is the email:" preamble."""


def _claude() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    return _client


def draft_followup(
    investor_name: str,
    firm_name: str,
    key_concern: str,
    traction_summary: str,
    memory_context: list[dict],
    founder_name: str = "the founder",
) -> str:
    """Draft a personalized follow-up email using firm memory + traction data."""
    memory_text = "\n".join(
        f"- [{m.get('date', '?')}] {m.get('snippet', '')[:200]}"
        for m in memory_context[:5]
    )
    user_msg = f"""Investor: {investor_name} at {firm_name}
What they cared about last time: {key_concern}
Traction update: {traction_summary}
Founder name: {founder_name}

Prior conversation context:
{memory_text}

Write the follow-up email body."""

    response = _claude().chat.completions.create(
        model="deepseek-chat",
        max_tokens=400,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_msg}],
    )
    return response.choices[0].message.content.strip()


def decide_action(firm_summary: dict, traction: dict) -> dict:
    """Ask Claude whether to follow up and why, given firm state + traction."""
    hot = firm_summary.get("hot_memories", [])
    recent = firm_summary.get("recent_context", [])
    last_contact = firm_summary.get("last_contact", "unknown")

    context = "\n".join(
        f"- {m.get('snippet', '')[:200]} (tier: {m.get('tier')}, intent: {m.get('intent_type')})"
        for m in (hot + recent)[:6]
    )

    traction_text = "\n".join(f"- {k}: {v}" for k, v in traction.items())

    prompt = f"""Firm: {firm_summary['firm']}
Last contact: {last_contact}
Traction:
{traction_text}

Email history:
{context}

Should the founder follow up with this investor today?
Reply with JSON: {{"action": "follow_up"|"wait"|"no_action", "reason": "<1 sentence>", "urgency": "high"|"medium"|"low"}}
Return ONLY the JSON."""

    response = _claude().chat.completions.create(
        model="deepseek-chat",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )
    import json, re
    raw = response.choices[0].message.content.strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return {"action": "wait", "reason": "Could not parse response", "urgency": "low"}

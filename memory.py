import hashlib
import json
from datetime import datetime
from typing import Optional

from actian_vectorai import VectorAIClient, VectorParams, Distance, PointStruct
from actian_vectorai.http_exceptions import UnexpectedResponse
from fastembed import TextEmbedding

from config import VECTORAI_HOST, VECTOR_DIM

_vectorai: VectorAIClient | None = None
_embedder: TextEmbedding | None = None

IMPORTANCE_WEIGHTS = {"hot": 3.0, "warm": 1.5, "cold": 0.5}


def _db() -> VectorAIClient:
    global _vectorai
    if _vectorai is None:
        _vectorai = VectorAIClient(VECTORAI_HOST)
    return _vectorai


def _embed(text: str) -> list[float]:
    global _embedder
    if _embedder is None:
        _embedder = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    return list(next(_embedder.embed([text])))


def _firm_collection(firm_key: str) -> str:
    return f"firm-{firm_key.lower().replace(' ', '-')}"


def _text_id(text: str) -> int:
    return int(hashlib.md5(text.encode()).hexdigest(), 16) % (2**63)


def ensure_firm_collection(firm_key: str):
    name = _firm_collection(firm_key)
    try:
        _db().collections.create(
            name,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.Cosine),
        )
    except Exception:
        pass  # already exists
    return name


def store_email_memory(
    firm_key: str,
    email_id: str,
    sender: str,
    subject: str,
    body_snippet: str,
    date: str,
    tier: str = "warm",
    intent_type: str = "unknown",
    follow_up_days: Optional[int] = None,
    condition: Optional[str] = None,
    raw_intent: Optional[dict] = None,
):
    """Embed and store an email chunk in the firm's VectorAI collection."""
    collection = ensure_firm_collection(firm_key)
    text = f"From: {sender}\nSubject: {subject}\n\n{body_snippet}"
    vector = _embed(text)

    payload = {
        "email_id": email_id,
        "sender": sender,
        "subject": subject,
        "snippet": body_snippet[:500],
        "date": date,
        "tier": tier,
        "importance": IMPORTANCE_WEIGHTS.get(tier, 1.0),
        "intent_type": intent_type,
        "follow_up_days": follow_up_days,
        "condition": condition,
        "intent": json.dumps(raw_intent or {}),
        "stored_at": datetime.utcnow().isoformat(),
    }

    point_id = _text_id(email_id + subject)
    _db().points.upsert(collection, [PointStruct(id=point_id, vector=vector, payload=payload)])


def search_firm_memory(firm_key: str, query: str, limit: int = 8) -> list[dict]:
    """Semantic search over a firm's memory, returns payloads sorted by importance."""
    collection = ensure_firm_collection(firm_key)
    vector = _embed(query)
    try:
        results = _db().points.search(collection, vector=vector, limit=limit, with_payload=True)
        hits = [r.payload for r in results if r.payload]
        hits.sort(key=lambda h: h.get("importance", 1.0), reverse=True)
        return hits
    except Exception:
        return []


def get_firm_summary(firm_key: str) -> dict:
    """Return a lightweight summary: last contact, open commitments, hot memories."""
    hot = search_firm_memory(firm_key, "follow up commitment promise check back", limit=5)
    recent = search_firm_memory(firm_key, "latest update meeting interest", limit=3)

    hot_filtered = [m for m in hot if m.get("tier") == "hot"]
    last_contact = max(
        (m.get("date", "") for m in hot + recent if m.get("date")),
        default=None,
    )
    return {
        "firm": firm_key,
        "last_contact": last_contact,
        "hot_memories": hot_filtered,
        "recent_context": recent,
    }

import os
import base64
import json
import logging
import httpx

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"
_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

_RECEIPT_PROMPT = """You are an expense extractor for a bill/receipt scanner.
Look at this receipt or bill image and extract all expense items.

Return ONLY a valid JSON array, no markdown, no explanation:
[{"description": "Coffee", "amount": 150.0, "currency": "INR", "category": "Food"}]

Categories (use exactly): Food, Transport, Entertainment, Health, Shopping, Utilities, Personal Care, Education, Travel, Other
Currency: ₹ or Rs = INR, $ = USD, € = EUR. If unclear or not shown, use INR.

Rules:
- If the bill is itemized, return each line item separately.
- If it shows only a total, return one object with the total amount.
- Parse amounts exactly as shown — no rounding or inflation.
- If you cannot read the receipt clearly, return [].
Return ONLY the JSON array."""


async def download_image(media_id: str) -> bytes | None:
    headers = {"Authorization": f"Bearer {_ACCESS_TOKEN}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        meta = await client.get(f"{GRAPH_API_BASE}/{media_id}", headers=headers)
        if meta.status_code != 200:
            return None
        url = meta.json().get("url")
        if not url:
            return None
        img = await client.get(url, headers=headers)
        if img.status_code != 200:
            return None
        return img.content


async def scan_receipt(image_bytes: bytes, mime_type: str = "image/jpeg") -> list[dict] | None:
    """Send image to Gemini Flash (free tier) and extract expense items."""
    if not _GEMINI_KEY:
        logger.error("GEMINI_API_KEY not set")
        return None

    payload = {
        "contents": [{
            "parts": [
                {"text": _RECEIPT_PROMPT},
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode()}}
            ]
        }],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 512}
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(_GEMINI_URL, params={"key": _GEMINI_KEY}, json=payload)
            if resp.status_code != 200:
                logger.error("Gemini API error %s: %s", resp.status_code, resp.text[:300])
                return None
            raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            items = json.loads(raw)
            if isinstance(items, list):
                return items
    except Exception:
        logger.exception("Gemini receipt scan failed")
    return None

import os
import base64
import json
import logging
import httpx

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"
_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

_RECEIPT_PROMPT = """You are an expense extractor. Carefully read this bill or receipt image and extract all expense items.

Return ONLY a valid JSON array — no markdown fences, no explanation, just the array:
[{"description": "Masala Dosa", "amount": 120.0, "currency": "INR", "category": "Food"}]

Categories (pick exactly one): Food, Transport, Entertainment, Health, Shopping, Utilities, Personal Care, Education, Travel, Other

Currency detection:
- ₹ or Rs. or INR or no symbol on Indian receipts → "INR"
- $ or USD → "USD"
- € or EUR → "EUR"
- Default to "INR" if unsure

Extraction rules:
- Extract each individual line item (e.g. "Paneer Butter Masala", "Coke", "Garlic Naan") as separate entries
- If quantity × price shown, compute the line total (e.g. 2 × 80 = 160)
- Include taxes (GST, VAT, service tax) as a separate item: {"description": "GST/Tax", "amount": X, "currency": "INR", "category": "Other"}
- Include service charge as a separate item if shown
- If ONLY a grand total is visible (no itemization), return one entry with the total and a description like "Restaurant bill" or "Grocery bill"
- Skip zero-amount lines, discounts/negative items, and tip lines
- Parse amounts exactly — no rounding. 149.00 stays 149.0
- If the image is too blurry to read, return []

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
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024}
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

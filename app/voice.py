import os
import httpx
from groq import AsyncGroq

_groq = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"
_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")


async def download_audio(media_id: str) -> bytes | None:
    """Fetch audio bytes for a WhatsApp media ID."""
    headers = {"Authorization": f"Bearer {_ACCESS_TOKEN}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: resolve media ID → download URL
        meta = await client.get(f"{GRAPH_API_BASE}/{media_id}", headers=headers)
        if meta.status_code != 200:
            return None
        url = meta.json().get("url")
        if not url:
            return None

        # Step 2: download the actual audio bytes
        audio = await client.get(url, headers=headers)
        if audio.status_code != 200:
            return None
        return audio.content


async def transcribe(audio_bytes: bytes) -> str | None:
    """Transcribe audio bytes using Groq Whisper. Returns plain text or None."""
    try:
        result = await _groq.audio.transcriptions.create(
            file=("voice.ogg", audio_bytes),
            model="whisper-large-v3-turbo",
        )
        return result.text.strip() or None
    except Exception:
        return None

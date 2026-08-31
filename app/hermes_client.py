import httpx

from .config import Settings


async def transcribe_audio(audio_bytes: bytes, filename: str, settings: Settings) -> str:
    """Transcribe M4A audio via an OpenAI-compatible Whisper endpoint."""
    if not settings.whisper_base_url:
        raise RuntimeError("WHISPER_BASE_URL is not configured")

    headers: dict[str, str] = {}
    if settings.whisper_api_key:
        headers["Authorization"] = f"Bearer {settings.whisper_api_key}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{settings.whisper_base_url}/v1/audio/transcriptions",
            headers=headers,
            files={"file": (filename, audio_bytes, "audio/mp4")},
            data={"model": settings.whisper_model},
        )
        response.raise_for_status()
        return str(response.json()["text"])


async def call_hermes(transcription: str, settings: Settings) -> str:
    """Send a transcription to the Hermes API and return the assistant reply."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.hermes_api_key:
        headers["Authorization"] = f"Bearer {settings.hermes_api_key}"

    payload = {
        "model": settings.hermes_model,
        "messages": [
            {"role": "system", "content": settings.hermes_system_prompt},
            {"role": "user", "content": transcription},
        ],
        "max_tokens": settings.hermes_max_tokens,
        "temperature": settings.hermes_temperature,
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=settings.hermes_timeout_seconds) as client:
        response = await client.post(
            f"{settings.hermes_base_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return str(data["choices"][0]["message"]["content"])

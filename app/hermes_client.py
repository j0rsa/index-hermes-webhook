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


async def schedule_hermes_job(
    note: dict[str, object], deliver: str, settings: Settings
) -> str:
    """Schedule a one-time Hermes job and trigger it immediately. Returns the job ID."""
    import json

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.hermes_api_key:
        headers["Authorization"] = f"Bearer {settings.hermes_api_key}"

    ring_payload = json.dumps(note, ensure_ascii=False)
    prompt = (
        f"<instructions>\n{settings.hermes_system_prompt}\n</instructions>"
        f"<ring_payload>\n{ring_payload}\n</ring_payload>"
    )

    job_name = f"ring-{note.get('ring_id', 'webhook')}"

    async with httpx.AsyncClient(timeout=settings.hermes_timeout_seconds) as client:
        create_resp = await client.post(
            f"{settings.hermes_base_url}/api/jobs",
            headers=headers,
            json={"name": job_name, "prompt": prompt, "schedule": "in 1m", "deliver": deliver},
        )
        create_resp.raise_for_status()
        job_id = str(create_resp.json()["job"]["id"])

        run_resp = await client.post(
            f"{settings.hermes_base_url}/api/jobs/{job_id}/run",
            headers=headers,
        )
        run_resp.raise_for_status()

    return job_id

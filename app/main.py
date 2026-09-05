import logging
import sys
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated, AsyncIterator

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .config import Settings
from .hermes_client import schedule_hermes_job, transcribe_audio

logger = logging.getLogger(__name__)


class _HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "GET /health" not in record.getMessage()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("uvicorn.access").addFilter(_HealthCheckFilter())


@lru_cache
def get_settings() -> Settings:
    return Settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    _setup_logging(settings.log_level)
    logger.info(
        "index-hermes-webhook starting — hermes_base_url=%s model=%s",
        settings.hermes_base_url,
        settings.hermes_model,
    )
    yield


app = FastAPI(
    title="Index Hermes Webhook",
    version="1.0.0",
    description="Bridges Pebble Index 01 audio webhooks to the Hermes API.",
    lifespan=lifespan,
)


def _verify_auth(
    authorization: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.webhook_auth_token:
        return
    if authorization != f"Bearer {settings.webhook_auth_token}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def _resolve_deliver(
    x_channel_id: str | None, x_telegram_id: str | None
) -> str:
    if x_channel_id:
        return x_channel_id
    if x_telegram_id:
        return f"telegram:{x_telegram_id}"
    return "telegram"


async def _process(
    transcription: str | None,
    audio_bytes: bytes | None,
    audio_filename: str,
    x_ring_id: str | None,
    recorded_at: int | None,
    client: str | None,
    is_test: bool,
    deliver: str,
    settings: Settings,
) -> None:
    text: str
    if transcription and transcription.strip():
        text = transcription.strip()
        logger.debug("transcription: %s", text)
    elif audio_bytes is not None:
        try:
            text = await transcribe_audio(audio_bytes, audio_filename, settings)
        except RuntimeError:
            logger.error("audio-only mode requires WHISPER_BASE_URL to be configured")
            return
        except httpx.HTTPError as exc:
            logger.error("Whisper transcription error: %s", exc)
            return
        logger.debug("transcription (from audio): %s", text)
    else:
        logger.error("background task found no transcription or audio — nothing to process")
        return

    note: dict[str, object] = {"transcription": text}
    if x_ring_id is not None:
        note["ring_id"] = x_ring_id
    if recorded_at is not None:
        note["recorded_at"] = recorded_at
    if client is not None:
        note["client"] = client
    if is_test:
        note["test"] = True

    logger.debug("scheduling Hermes job: deliver=%s note=%s", deliver, note)

    try:
        job_id = await schedule_hermes_job(note, deliver, settings)
    except httpx.TimeoutException as exc:
        logger.error("Hermes timeout: %s", exc)
        return
    except httpx.HTTPStatusError as exc:
        logger.error("Hermes HTTP error %s: %s", exc.response.status_code, exc.response.text)
        return
    except httpx.HTTPError as exc:
        logger.error("Hermes connection error: %s", exc)
        return

    logger.info("Hermes job scheduled job_id=%s deliver=%s", job_id, deliver)


@app.post("/webhook", status_code=202)
async def handle_webhook(
    background_tasks: BackgroundTasks,
    recorded_at: Annotated[int | None, Form(alias="recordedAt")] = None,
    client: Annotated[str | None, Form()] = None,
    transcription: Annotated[str | None, Form()] = None,
    test: Annotated[str | None, Form()] = None,
    audio: Annotated[UploadFile | None, File()] = None,
    x_ring_id: Annotated[str | None, Header()] = None,
    x_channel_id: Annotated[str | None, Header()] = None,
    x_telegram_id: Annotated[str | None, Header()] = None,
    _: None = Depends(_verify_auth),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    if not (transcription and transcription.strip()) and audio is None:
        raise HTTPException(status_code=400, detail="No transcription or audio provided")

    # Read audio bytes eagerly — UploadFile is tied to the request body and
    # will not be readable after the response is sent.
    audio_bytes: bytes | None = None
    audio_filename = "recording.m4a"
    if audio is not None:
        audio_bytes = await audio.read()
        audio_filename = audio.filename or "recording.m4a"

    is_test = test is not None and test.lower() == "true"
    deliver = _resolve_deliver(x_channel_id, x_telegram_id)

    logger.info(
        "webhook accepted ring_id=%s client=%s recorded_at=%s is_test=%s "
        "has_transcription=%s has_audio=%s deliver=%s",
        x_ring_id,
        client,
        recorded_at,
        is_test,
        transcription is not None,
        audio_bytes is not None,
        deliver,
    )

    background_tasks.add_task(
        _process,
        transcription,
        audio_bytes,
        audio_filename,
        x_ring_id,
        recorded_at,
        client,
        is_test,
        deliver,
        settings,
    )

    return JSONResponse({"status": "accepted"}, status_code=202)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

import logging
import sys
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated, AsyncIterator

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .config import Settings
from .hermes_client import call_hermes, transcribe_audio

logger = logging.getLogger(__name__)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


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


@app.post("/webhook")
async def handle_webhook(
    recorded_at: Annotated[int | None, Form(alias="recordedAt")] = None,
    client: Annotated[str | None, Form()] = None,
    transcription: Annotated[str | None, Form()] = None,
    audio: Annotated[UploadFile | None, File()] = None,
    _: None = Depends(_verify_auth),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    logger.info(
        "webhook received client=%s recorded_at=%s has_transcription=%s has_audio=%s",
        client,
        recorded_at,
        transcription is not None,
        audio is not None,
    )

    text: str
    if transcription and transcription.strip():
        text = transcription.strip()
        logger.debug("transcription: %s", text)
    elif audio is not None:
        audio_bytes = await audio.read()
        try:
            text = await transcribe_audio(
                audio_bytes, audio.filename or "recording.m4a", settings
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=422,
                detail="Audio-only mode requires WHISPER_BASE_URL to be configured",
            ) from exc
        except httpx.HTTPError as exc:
            logger.error("Whisper transcription error: %s", exc)
            raise HTTPException(status_code=502, detail=f"Whisper error: {exc}") from exc
        logger.debug("transcription (from audio): %s", text)
    else:
        raise HTTPException(status_code=400, detail="No transcription or audio provided")

    note: dict[str, object] = {"transcription": text}
    if recorded_at is not None:
        note["recorded_at"] = recorded_at
    if client is not None:
        note["client"] = client

    logger.debug("forwarding to Hermes: %s", note)

    try:
        reply = await call_hermes(note, settings)
    except httpx.TimeoutException as exc:
        logger.error("Hermes timeout: %s", exc)
        raise HTTPException(status_code=504, detail="Hermes API timed out") from exc
    except httpx.HTTPStatusError as exc:
        logger.error("Hermes HTTP error %s: %s", exc.response.status_code, exc.response.text)
        raise HTTPException(
            status_code=502, detail=f"Hermes returned {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        logger.error("Hermes connection error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Hermes connection error: {exc}") from exc

    logger.info("Hermes replied (%d chars)", len(reply))
    return JSONResponse({"response": reply, "transcription": text})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

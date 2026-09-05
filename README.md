# index-hermes-webhook

A stateless FastAPI bridge that receives audio webhooks from the **Pebble Index 01** wearable and forwards them to a **Hermes** (or any OpenAI-compatible) LLM server — so every voice note you record on the ring gets processed by your own AI, running on your own hardware.

```
┌─────────────────┐    multipart/form-data     ┌──────────────────────┐
│ Pebble Index 01 │ ── POST /webhook ─────────►│ index-hermes-webhook │
│    (the ring)   │ ◄─ 202 Accepted ───────────│   (this service)     │
└─────────────────┘                            └────────┬──────┬──────┘
                                                background│task  │
                                              ┌───────────┘      │
                                     if audio only               │ POST /api/jobs
                                              │                  │ POST /api/jobs/{id}/run
                                              ▼                  ▼
                                   ┌──────────────────┐  ┌──────────────────────┐
                                   │   Whisper STT    │  │   Hermes API server  │
                                   │  /audio/trans.   │  │   (job scheduling)   │
                                   └──────────────────┘  └──────────────────────┘
```

---

## How it works

1. You speak into your Pebble Index 01. The ring transcribes your voice and POSTs the result to a configured webhook URL.
2. This service validates the request and immediately returns **202 Accepted** — the connection is never held open.
3. A background task takes over: if only raw audio was received it calls Whisper to transcribe it first, then schedules a one-time **Hermes job** with the note payload.
4. Hermes runs the job and delivers the response to the configured channel (Telegram by default).

The service is **completely stateless** — no database, no file writes, no disk. Every request lives and dies in memory.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Pebble Index 01** | Firmware with webhook support (Advanced Features → Webhook) |
| **Hermes API server** | Any OpenAI-compatible server; [Nous Hermes Agent](https://hermes-agent.nousresearch.com/) runs on port `8642` by default |
| **Docker** (recommended) | Or Python 3.11+ with [uv](https://docs.astral.sh/uv/) |
| **Public HTTPS URL** | The Pebble app requires HTTPS. Use [ngrok](https://ngrok.com/), [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/), or a reverse proxy. |

---

## Quick start

### 1. Clone and configure

```bash
git clone https://github.com/<your-org>/index-hermes-webhook
cd index-hermes-webhook
cp .env.example .env
```

Edit `.env` — at minimum set:

```dotenv
HERMES_BASE_URL=http://your-hermes-host:8642
HERMES_SYSTEM_PROMPT=You are a personal assistant. Summarize the voice note concisely.
```

### 2. Run with Docker Compose

```bash
docker compose up --build
```

The service starts on `http://localhost:8000`. Verify it:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### 3. Expose it publicly

Use your preferred tunnelling tool, for example:

```bash
ngrok http 8000
# → https://abc123.ngrok-free.app
```

### 4. Configure your Pebble Index 01

In the **Pebble** mobile app:

1. Open **Index** → **Advanced Features**
2. Enable **Webhook** and paste your public URL: `https://abc123.ngrok-free.app/webhook`
3. Set **Mode** to **Both** (sends transcription + audio) or **Text only** (transcription only — cheaper/faster)
4. Under **Custom Headers**, add:
   ```
   X-Ring-ID: <a name you choose, e.g. "living-room-ring">
   ```
   This is how Hermes knows which ring sent the note. If you have only one ring, any static name works.
5. *(Optional)* To secure the endpoint, also add:
   ```
   Authorization: Bearer <your-WEBHOOK_AUTH_TOKEN>
   ```
   — and set the same value as `WEBHOOK_AUTH_TOKEN` in your `.env`

---

## Configuration reference

All settings are environment variables. Copy `.env.example` to `.env` for local development; pass them directly in Docker / Kubernetes for production.

### Hermes API

| Variable | Default | Description |
|---|---|---|
| `HERMES_BASE_URL` | `http://localhost:8642` | Base URL of the Hermes (OpenAI-compatible) server |
| `HERMES_API_KEY` | *(empty)* | Bearer token for the Hermes server (`API_SERVER_KEY` in Hermes config) |
| `HERMES_MODEL` | `hermes-agent` | Model name sent in requests (ignored by Hermes unless `direct_model_requests: true`) |
| `HERMES_SYSTEM_PROMPT` | *See `.env.example`* | **The predefined prompt** — injected as the `system` message before every transcription |
| `HERMES_MAX_TOKENS` | `1024` | Maximum tokens in the LLM response |
| `HERMES_TEMPERATURE` | `0.7` | Sampling temperature |
| `HERMES_TIMEOUT_SECONDS` | `30.0` | HTTP timeout for Hermes calls |

### Whisper STT *(optional)*

Only needed when the Pebble is configured to send **audio only** (no transcription). Leave blank if using Text or Both mode.

| Variable | Default | Description |
|---|---|---|
| `WHISPER_BASE_URL` | *(empty)* | OpenAI-compatible Whisper endpoint (e.g. `http://localhost:8080`) |
| `WHISPER_API_KEY` | *(empty)* | Bearer token for the Whisper server |
| `WHISPER_MODEL` | `whisper-1` | Whisper model name |

### Security

| Variable | Default | Description |
|---|---|---|
| `WEBHOOK_AUTH_TOKEN` | *(empty)* | If set, the webhook endpoint requires `Authorization: Bearer <token>`. Configure the same token as a Custom Header in the Pebble app. |

### App

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `PORT` | `8000` | Port the server binds to inside the container |

---

## Webhook payload

### Incoming (from Pebble)

The Pebble sends a `multipart/form-data` POST with the following fields:

| Field | Type | When present |
|---|---|---|
| `transcription` | `string` | Text and Both modes |
| `audio` | `audio/mp4` (M4A) | Audio and Both modes |
| `recordedAt` | Unix ms timestamp | Always |
| `client` | `string` — always `"ring"` | Always |
| `test` | `"true"` | Only on test events sent from the Pebble app |

In addition, the service reads these **custom HTTP headers**:

| Header | Purpose |
|---|---|
| `X-Ring-ID` | Identifies which ring sent the webhook. Set this as a Custom Header in the Pebble app (see [Setup](#4-configure-your-pebble-index-01)). |
| `X-Channel-ID` | Explicit Hermes delivery channel (e.g. `telegram:12345678`). Takes precedence over `X-Telegram-ID`. |
| `X-Telegram-ID` | Telegram user/chat ID. If set (and `X-Channel-ID` is absent), the job is delivered to `telegram:<id>`. Falls back to `telegram` when both headers are absent. |

This service prioritises `transcription` if present. If only `audio` is received, it is forwarded to the configured Whisper endpoint. If neither is present, HTTP 400 is returned.

### Forwarded to Hermes (job prompt)

Each webhook call creates a **new** one-time Hermes job named `ring-<ring_id>` (e.g. `ring-my-ring`). The name is the same for every note from the same ring, which makes jobs identifiable in Hermes logs and UI, but each call is an independent ephemeral job — not an update to an existing one.

The job's `prompt` field is a tagged message:

```
<instructions>
{HERMES_SYSTEM_PROMPT}
</instructions><ring_payload>
{"transcription": "Buy oat milk on the way home", "ring_id": "my-ring", ...}
</ring_payload>
```

All non-audio fields plus `X-Ring-ID` are included in `ring_payload`. The `audio` binary is always dropped. Fields are omitted when absent.

> **Note:** `test: true` is included when the Pebble sends a test event so your prompt can instruct Hermes to ignore or acknowledge it differently.

Your `HERMES_SYSTEM_PROMPT` should tell the model how to interpret these fields — for example:

```
You are a personal assistant processing voice notes from a smart ring.
"ring_id" identifies the specific ring that recorded the note.
"recorded_at" is a Unix millisecond timestamp of when the note was recorded.
If "test" is true, acknowledge the test but do not act on the content.
```

### Hermes agent tip

If your Hermes agent wraps cron-job responses in extra formatting, add this to its agent config to keep replies natural:

```yaml
cron:
  wrap_response: false
```

---

## Docker

### Pull from GHCR

```bash
docker pull ghcr.io/<your-org>/index-hermes-webhook:latest

docker run -d \
  -p 8000:8000 \
  -e HERMES_BASE_URL=http://your-host:8642 \
  -e HERMES_SYSTEM_PROMPT="Summarize this voice note." \
  -e WEBHOOK_AUTH_TOKEN=supersecret \
  ghcr.io/<your-org>/index-hermes-webhook:latest
```

### Build locally

```bash
docker build -t index-hermes-webhook .
```

Multi-arch (amd64 + arm64):

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t index-hermes-webhook .
```

---

## CI/CD

GitHub Actions workflow at `.github/workflows/docker.yml` automatically:

- **Builds** on every pull request (no push)
- **Builds and pushes** to `ghcr.io/<owner>/<repo>` on every push to `main`
- **Tags** releases by semver when you push a `v*` tag
- Uses **layer caching** via GitHub Actions Cache for fast rebuilds
- Produces **multi-arch images** (`linux/amd64` + `linux/arm64`)

No secrets to configure — the workflow uses `GITHUB_TOKEN` (automatically provided by GitHub).

To release a new version:

```bash
git tag v1.2.3
git push origin v1.2.3
```

---

## Development

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create venv and install deps
uv sync

# Run dev server with auto-reload
uv run uvicorn app.main:app --reload

# Run tests
uv run pytest
```

### Sending a test webhook locally

```bash
curl -X POST http://localhost:8000/webhook \
  -F "transcription=Buy oat milk on the way home" \
  -F "recordedAt=1700000000000" \
  -F "client=ring"
```

---

## Architecture decisions

| Decision | Rationale |
|---|---|
| **FastAPI** | Async, typed, zero-boilerplate multipart form handling via `UploadFile` + `Form` |
| **httpx** (async) | Non-blocking calls to Hermes and Whisper; respects the FastAPI async model |
| **pydantic-settings** | Single source of truth for config; reads ENV vars and `.env` files automatically |
| **No database / no disk writes** | The Pebble ring can store notes itself; this service is a pure processing pipeline |
| **Whisper as optional path** | Most users configure the ring in Text/Both mode — adding a mandatory STT dependency would complicate deployment for the common case |
| **GHCR over Docker Hub** | Free for public repos, auth is `GITHUB_TOKEN` — zero extra secrets to manage |

---

## License

MIT

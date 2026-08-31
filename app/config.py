from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Hermes API (OpenAI-compatible, port 8642 by default)
    hermes_base_url: str = Field(default="http://localhost:8642")
    hermes_api_key: str = Field(default="")
    hermes_model: str = Field(default="hermes-agent")
    hermes_system_prompt: str = Field(
        default="You are a helpful assistant. Process the following voice note and respond concisely."
    )
    hermes_max_tokens: int = Field(default=1024)
    hermes_temperature: float = Field(default=0.7)
    hermes_timeout_seconds: float = Field(default=30.0)

    # Whisper STT (optional — required only when device sends audio-only, no transcription)
    whisper_base_url: str = Field(default="")
    whisper_api_key: str = Field(default="")
    whisper_model: str = Field(default="whisper-1")

    # Webhook security — if set, incoming request must carry Authorization: Bearer <token>
    webhook_auth_token: str = Field(default="")

    log_level: str = Field(default="INFO")

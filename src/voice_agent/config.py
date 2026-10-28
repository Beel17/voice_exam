"""All service settings, as one typed object.

Values come from a TOML file (written at centre set-up) and can be overridden by
environment variables such as ``VOICE_AGENT_MODE=server`` or
``VOICE_AGENT_LIMITS__MAX_UTTERANCE_CHARS=200``. PC mode and server mode differ only here.
"""

import os
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

CONFIG_ENV_VAR = "VOICE_AGENT_CONFIG"
DEFAULT_CONFIG_FILE = Path("config/centre.toml")

Mode = Literal["pc", "server"]

# A device token is never stored in plain text; set-up writes its SHA-256 hex digest.
TokenDigest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class LimitSettings(BaseModel):
    """Caps on what one session can make the service do."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Real commands are a few words; a cap keeps hostile or garbled input cheap to reject.
    max_utterance_chars: int = Field(default=300, ge=20, le=500)
    # Messages per connection per 10 s: above a fast keyboard user, below a stuck key.
    max_messages_per_10s: int = Field(default=60, ge=5, le=10_000)


class LlmSettings(BaseModel):
    """The language model, run as a llama.cpp server child process on loopback."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    server_binary: Path = Path("models/llama.cpp/llama-server")
    model_file: Path = Path("models/qwen2.5-1.5b-instruct-q4_k_m.gguf")
    port: int = Field(default=8766, ge=1, le=65535)
    threads: int = Field(default=2, ge=1, le=64)
    # Sentences the model can work on at once; more slots need more cores and memory.
    slots: int = Field(default=1, ge=1, le=16)
    context_per_slot: int = Field(default=2048, ge=512, le=8192)
    timeout_s: float = Field(default=2.0, gt=0, le=10)
    max_waiting: int = Field(default=4, ge=0, le=64)
    max_calls_per_minute: int = Field(default=20, ge=1, le=600)


class AdapterSettings(BaseModel):
    """How the service reaches the exam platform."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # "fake" serves the bundled sample exams; "http" and "sql" arrive with phase 7.
    kind: Literal["fake"] = "fake"
    timeout_s: float = Field(default=2.0, gt=0, le=30)
    # Fake platform only: extra sample-exam candidates for load tests (LOAD00001...).
    load_candidates: int = Field(default=0, ge=0, le=10_000)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VOICE_AGENT_",
        env_nested_delimiter="__",
        extra="forbid",
        frozen=True,
    )

    mode: Mode = "pc"
    # None means: loopback only in PC mode (the candidate is on this machine), all
    # interfaces on the centre LAN in server mode.
    host: str | None = None
    port: int = Field(default=8765, ge=1, le=65535)
    data_dir: Path = Path("data")
    models_dir: Path = Path("models")
    device_token_sha256: tuple[TokenDigest, ...] = Field(default=(), max_length=1000)
    # For the centre dashboard and the platform pulling audit logs; separate from devices.
    admin_token_sha256: tuple[TokenDigest, ...] = Field(default=(), max_length=100)
    time_warnings_min: tuple[int, ...] = (15, 5)
    adapter: AdapterSettings = AdapterSettings()
    limits: LimitSettings = LimitSettings()
    llm: LlmSettings = LlmSettings()

    @property
    def bind_host(self) -> str:
        if self.host is not None:
            return self.host
        return "127.0.0.1" if self.mode == "pc" else "0.0.0.0"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Environment beats the file so an invigilator can override one value without
        # editing the centre's configuration; no .env or secrets-dir lookups.
        return (env_settings, init_settings)


def load_settings(config_file: Path | None = None) -> Settings:
    """Load settings from ``config_file``, ``$VOICE_AGENT_CONFIG`` or the default path.

    A missing default file means built-in defaults; a path that was named explicitly
    must exist, so a typo fails at start-up rather than silently running unconfigured.
    """
    named = config_file or (Path(p) if (p := os.environ.get(CONFIG_ENV_VAR)) else None)
    path = named or DEFAULT_CONFIG_FILE
    if named is None and not path.exists():
        return Settings()
    with path.open("rb") as f:
        data = tomllib.load(f)
    return Settings(**data)

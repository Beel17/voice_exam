from pathlib import Path

import pytest
from pydantic import ValidationError

from voice_agent.config import CONFIG_ENV_VAR, Settings, load_settings

DIGEST = "a" * 64


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("VOICE_AGENT_MODE", "VOICE_AGENT_PORT", "VOICE_AGENT_HOST", CONFIG_ENV_VAR):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("VOICE_AGENT_LIMITS__MAX_UTTERANCE_CHARS", raising=False)
    # Isolate from any real config/centre.toml in the working directory.
    monkeypatch.chdir(tmp_path)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "centre.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_without_a_config_file() -> None:
    settings = load_settings()
    assert settings.mode == "pc"
    assert settings.bind_host == "127.0.0.1"
    assert settings.device_token_sha256 == ()


def test_server_mode_binds_to_the_lan_unless_host_given() -> None:
    assert Settings(mode="server").bind_host == "0.0.0.0"
    assert Settings(mode="server", host="10.0.0.5").bind_host == "10.0.0.5"


def test_file_values_are_loaded(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        f'mode = "server"\nport = 9000\ndevice_token_sha256 = ["{DIGEST}"]\n'
        "[limits]\nmax_utterance_chars = 120\n",
    )
    settings = load_settings(path)
    assert settings.mode == "server"
    assert settings.port == 9000
    assert settings.device_token_sha256 == (DIGEST,)
    assert settings.limits.max_utterance_chars == 120


def test_environment_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write(tmp_path, 'mode = "pc"\n[limits]\nmax_utterance_chars = 120\n')
    monkeypatch.setenv("VOICE_AGENT_MODE", "server")
    monkeypatch.setenv("VOICE_AGENT_LIMITS__MAX_UTTERANCE_CHARS", "80")
    settings = load_settings(path)
    assert settings.mode == "server"
    assert settings.limits.max_utterance_chars == 80


def test_config_path_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONFIG_ENV_VAR, str(write(tmp_path, "port = 9100\n")))
    assert load_settings().port == 9100


def test_named_config_file_must_exist(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "typo.toml")


@pytest.mark.parametrize(
    "text",
    [
        'mode = "cloud"\n',
        "port = 0\n",
        "unknown_key = 1\n",
        'device_token_sha256 = ["not-a-digest"]\n',
        f'device_token_sha256 = ["{DIGEST.upper()}"]\n',
        "[limits]\nmax_utterance_chars = 100000\n",
    ],
)
def test_invalid_config_fails_at_start_up(tmp_path: Path, text: str) -> None:
    with pytest.raises(ValidationError):
        load_settings(write(tmp_path, text))


def test_settings_are_immutable() -> None:
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.mode = "server"  # type: ignore[misc]

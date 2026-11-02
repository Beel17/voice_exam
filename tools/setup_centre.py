"""Write this machine's configuration and issue its device token, once.

    python -m tools.setup_centre --data "C:\\ProgramData\\VoiceExam"

Run by the installer, from the program folder, with the bundled Python. Everything that
changes lives in the data folder, apart from the program: the configuration (from
config/centre.example.toml, with the data folder made absolute and a fresh device token),
DEVICE-TOKEN.txt (the token itself, for whoever sets up the exam PC's browser; only its
SHA-256 goes in the configuration), sessions and logs. An existing configuration is kept
(a reinstall or an upgrade keeps the centre's settings and token). Standard library only:
it runs before anything else is set up.
"""

import argparse
import hashlib
import secrets
from pathlib import Path

PROGRAM = Path(__file__).parents[1]


def write_config(example: Path, config: Path, token_file: Path, data_dir: str = "data") -> bool:
    """``config``, from ``example`` with a new device token, unless it exists. True if
    written."""
    if config.exists():
        return False
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    text = example.read_text(encoding="utf-8")
    for old, new in (
        ("device_token_sha256 = []", f'device_token_sha256 = ["{digest}"]'),
        ('data_dir = "data"', f'data_dir = "{data_dir}"'),
    ):
        if old not in text:
            raise SystemExit(f"centre.example.toml has changed: no {old!r} to fill in")
        text = text.replace(old, new)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    config.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(token + "\n", encoding="ascii")
    config.write_text(text, encoding="utf-8")  # last: its presence means set-up is done
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data", type=Path, required=True, help="where sessions and logs go")
    args = parser.parse_args()
    data: Path = args.data.resolve()
    written = write_config(
        PROGRAM / "config" / "centre.example.toml",
        data / "centre.toml",
        data / "DEVICE-TOKEN.txt",
        (data / "data").as_posix(),
    )
    print("configuration written" if written else "configuration kept")


if __name__ == "__main__":
    main()

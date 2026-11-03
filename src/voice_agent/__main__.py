"""``python -m voice_agent``: run the service with the centre's configuration."""

import logging

import uvicorn

from voice_agent.config import Settings, load_settings
from voice_agent.main import create_app

log = logging.getLogger("voice_agent")


def tls_files(settings: Settings) -> dict[str, str]:
    """uvicorn's HTTPS arguments, or none. A named file that is missing stops the start
    with a plain message, rather than a server that silently falls back to HTTP."""
    tls = settings.tls
    if tls.cert_file is None or tls.key_file is None:
        if settings.mode == "server":
            log.warning(
                "server mode without HTTPS: candidates' browsers will not allow the "
                "microphone, so audio mode works only on this machine (see [tls])"
            )
        return {}
    for name, path in (("cert_file", tls.cert_file), ("key_file", tls.key_file)):
        if not path.is_file():
            raise SystemExit(f"tls.{name} not found: {path}")
    return {"ssl_certfile": str(tls.cert_file), "ssl_keyfile": str(tls.key_file)}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.port,
        ws_max_size=65536,
        **tls_files(settings),  # type: ignore[arg-type]
    )


if __name__ == "__main__":
    main()

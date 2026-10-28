"""``python -m voice_agent``: run the service with the centre's configuration."""

import logging

import uvicorn

from voice_agent.config import load_settings
from voice_agent.main import create_app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = load_settings()
    uvicorn.run(
        create_app(settings), host=settings.bind_host, port=settings.port, ws_max_size=65536
    )


if __name__ == "__main__":
    main()

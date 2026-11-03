"""Open the exam app in the default browser as soon as the service is ready.

    python -m tools.open_client

Started alongside the service by the test bundle's start scripts, so whoever is helping
the tester never opens the page before the service can answer it.
"""

import time
import webbrowser

import httpx

from voice_agent.config import load_settings

WAIT_S = 300  # a slow PC loading the language model for the first time


def main() -> None:
    url = f"http://127.0.0.1:{load_settings().port}"
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/readyz", timeout=2, trust_env=False).status_code == 200:
                webbrowser.open(f"{url}/exam/")
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit("the service did not become ready; see its window")


if __name__ == "__main__":
    main()

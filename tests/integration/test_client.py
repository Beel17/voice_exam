"""The voice client: served by the service, and its audio helpers' own tests (node)."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_api import client, clock, platform  # noqa: F401

ROOT = Path(__file__).parents[2]


def test_the_client_is_served(client: TestClient) -> None:  # noqa: F811
    assert client.get("/", follow_redirects=False).headers["location"] == "/client/"
    page = client.get("/client/")
    assert page.status_code == 200 and "Voice exam" in page.text
    for name in ("app.js", "audio.js", "capture-worklet.js"):
        script = client.get(f"/client/{name}")
        assert script.status_code == 200
        # Browsers refuse to run a module that is not served as JavaScript.
        assert script.headers["content-type"].startswith("text/javascript")
    assert client.get("/client/../main.py").status_code == 404


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_clients_audio_helpers() -> None:
    result = subprocess.run(
        ["node", "--test", "tests/js/*.test.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_page_has_each_element_the_script_looks_up_once() -> None:
    client_dir = ROOT / "src" / "voice_agent" / "client"
    ids = re.findall(r'\bid="([^"]+)"', (client_dir / "index.html").read_text(encoding="utf-8"))
    assert len(ids) == len(set(ids)), "duplicate ids: getElementById finds only the first"
    script = (client_dir / "app.js").read_text(encoding="utf-8")
    looked_up = set(re.findall(r'\$\("([^"]+)"\)', script))
    looked_up |= set(re.findall(r'"([a-z-]+)": "[a-z]+"', script))  # the sign-in fields
    assert looked_up - {"device"} <= set(ids), looked_up - set(ids)

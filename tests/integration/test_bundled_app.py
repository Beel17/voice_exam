"""The exam app bundled inside a secure browser: the engine lets that browser's pages call
it, and only that browser's."""

import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tools.make_browser_app import write_zip

from tests.integration.test_api import TOKEN, make_settings
from voice_agent.adapters.fake_adapter import FakeAdapter
from voice_agent.main import create_app

BROWSER = "app://exam-browser"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = make_settings(tmp_path, allowed_origins=(BROWSER,))
    with TestClient(create_app(settings, adapter=FakeAdapter.with_bundled_fixtures())) as c:
        yield c


def test_the_browsers_pages_may_read_from_the_engine(client: TestClient) -> None:
    cue = client.get("/v1/cues/saved.wav", headers={"Origin": BROWSER})
    assert cue.headers["access-control-allow-origin"] == BROWSER
    # The photo needs the device token: the browser asks first whether it may send it.
    asked = client.options(
        "/v1/sessions/s/photo",
        headers={
            "Origin": BROWSER,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert asked.status_code == 200
    assert asked.headers["access-control-allow-origin"] == BROWSER
    assert "authorization" in asked.headers["access-control-allow-headers"].lower()
    no_session = client.get(
        "/v1/sessions/s/photo", headers={"Origin": BROWSER, "Authorization": f"Bearer {TOKEN}"}
    )
    assert no_session.status_code == 404  # allowed to ask; still no such session


def test_other_pages_may_not(client: TestClient) -> None:
    cue = client.get("/v1/cues/saved.wav", headers={"Origin": "https://elsewhere.example"})
    assert "access-control-allow-origin" not in cue.headers


def test_by_default_no_other_origin_may(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path), adapter=FakeAdapter.with_bundled_fixtures())
    with TestClient(app) as c:
        cue = c.get("/v1/cues/saved.wav", headers={"Origin": BROWSER})
    assert "access-control-allow-origin" not in cue.headers


def test_the_zip_for_the_browser_team(tmp_path: Path) -> None:
    app = tmp_path / "built"
    (app / "assets").mkdir(parents=True)
    for name in ("index.html", "engine-config.js", "capture-worklet.js", "assets/app.js"):
        (app / name).write_text("x", encoding="utf-8")
    with zipfile.ZipFile(write_zip(tmp_path / "exam-app.zip", app)) as z:
        names = set(z.namelist())
        guide = z.read("exam-app/BUNDLING.txt").decode()
    assert {"exam-app/index.html", "exam-app/engine-config.js", "exam-app/assets/app.js"} <= names
    assert "window.EXAM_ENGINE" in guide and "allowed_origins" in guide


def test_the_app_is_built_with_relative_paths() -> None:
    root = Path(__file__).parents[2]
    config = (root / "exam-app" / "vite.config.ts").read_text(encoding="utf-8")
    assert 'base: "./"' in config  # so the same files work bundled, from any folder
    page = (root / "exam-app" / "index.html").read_text(encoding="utf-8")
    assert page.index('src="./engine-config.js"') < page.index('src="/src/main.tsx"')
    default = (root / "exam-app" / "public" / "engine-config.js").read_text(encoding="utf-8")
    assert 'window.EXAM_ENGINE = "";' in default  # served by the engine: its own address

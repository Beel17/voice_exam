"""Sync against the stand-in cloud: packages down, results up, and nothing twice."""

import json
from pathlib import Path

import httpx
import pytest

from voice_agent.examstore.__main__ import sample_package
from voice_agent.examstore.adapter import LocalAdapter
from voice_agent.examstore.package import write_package
from voice_agent.examstore.store import ExamStore
from voice_agent.sync.client import SyncClient, SyncError, check_url
from voice_agent.sync.standin import create_app

TOKEN = "centre-token-for-tests"


class Cloud:
    def __init__(self, tmp_path: Path) -> None:
        self.packages = tmp_path / "cloud" / "packages"
        self.results = tmp_path / "cloud" / "results"
        self.packages.mkdir(parents=True)
        (self.packages / "practice.zip").write_bytes(write_package(sample_package()))
        self.app = create_app(self.packages, self.results, "C1", TOKEN)
        self.store = ExamStore(tmp_path / "centre" / "exams.db")

    def client(
        self, transport: httpx.AsyncBaseTransport | None = None, token: str = TOKEN
    ) -> SyncClient:
        return SyncClient(
            self.store, "http://localhost", "C1", token,
            transport=transport or httpx.ASGITransport(app=self.app),
        )  # fmt: skip

    def received(self) -> list[dict[str, object]]:
        log = self.results / "results.jsonl"
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return [json.loads(line) for line in lines]


@pytest.fixture
def cloud(tmp_path: Path) -> Cloud:
    return Cloud(tmp_path)


async def test_pull_imports_the_centres_packages_once(cloud: Cloud) -> None:
    client = cloud.client()
    first = await client.pull()
    assert first.imported == ("practice-utme-sample-1",) and not first.refused
    again = await client.pull()
    assert again.imported == () and again.already_held == ("practice-utme-sample-1",)
    exams = cloud.store.exams()
    assert [e.exam_id for e in exams] == ["exam-sample-60"]
    assert not exams[0].is_open  # opening it is the centre's decision
    await client.aclose()


async def test_a_package_damaged_in_transfer_is_refused(cloud: Cloud) -> None:
    inner = httpx.ASGITransport(app=cloud.app)

    class Corrupting(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            response = await inner.handle_async_request(request)
            if "/v1/packages/" in request.url.path:
                body = bytearray(await response.aread())
                body[100] ^= 0xFF
                return httpx.Response(200, content=bytes(body))
            return response

    report = await cloud.client(Corrupting()).pull()
    assert report.imported == () and "checksum" in report.refused[0]
    assert cloud.store.exams() == []


async def answered(cloud: Cloud, count: int) -> str:
    """A candidate who answered ``count`` questions and submitted."""
    cloud.store.set_open("exam-sample-60", True)
    platform = LocalAdapter(cloud.store)
    session = await platform.verify_candidate("20260001PR", "")
    for n in range(1, count + 1):
        await platform.save_answer(session.candidate_id, n, "A", f"s:{n}")
    await platform.submit(session.candidate_id, "s:submit")
    return session.candidate_id


async def test_push_uploads_every_result_once_in_order(cloud: Cloud) -> None:
    client = cloud.client()
    await client.pull()
    candidate = await answered(cloud, 3)
    assert await client.push() == 4
    assert await client.push() == 0  # nothing left
    received = cloud.received()
    assert [op["operation_id"] for op in received] == ["s:1", "s:2", "s:3", "s:submit"]
    # Against the paper's own numbers, never the candidate's shuffled view.
    version = cloud.store.candidate(candidate).version
    assert [op["question"] for op in received[:3]] == [version.paper_number(n) for n in (1, 2, 3)]
    assert cloud.store.upload_counts() == (0, 4)
    await client.aclose()


async def test_a_lost_reply_is_retried_without_counting_twice(cloud: Cloud) -> None:
    await cloud.client().pull()
    await answered(cloud, 2)
    inner = httpx.ASGITransport(app=cloud.app)

    class LosesReply(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            await inner.handle_async_request(request)  # the cloud applies it...
            raise httpx.ReadTimeout("reply lost", request=request)  # ...the reply is lost

    with pytest.raises(SyncError, match="could not be reached"):
        await cloud.client(LosesReply()).push()
    assert cloud.store.upload_counts() == (3, 0)  # the centre still thinks they are waiting
    assert await cloud.client().push() == 3  # retried...
    assert len(cloud.received()) == 3  # ...and the cloud holds each once


async def test_a_wrong_token_is_refused(cloud: Cloud) -> None:
    with pytest.raises(SyncError, match="token"):
        await cloud.client(token="someone-elses").pull()


def test_the_cloud_address_must_be_https() -> None:
    assert check_url("https://cloud.example.org/") == "https://cloud.example.org"
    assert check_url("http://localhost:8800") == "http://localhost:8800"
    with pytest.raises(SyncError, match="https"):
        check_url("http://cloud.example.org")


def test_the_published_api_description_is_current() -> None:
    from voice_agent.sync.standin import openapi_json

    published = Path(__file__).parents[2] / "spec" / "sync.openapi.json"
    assert published.read_text(encoding="utf-8") == openapi_json(), (
        "spec is stale: uv run python -m voice_agent.sync.standin --spec spec/sync.openapi.json"
    )

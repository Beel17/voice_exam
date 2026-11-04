"""Sync against the stand-in cloud: packages down, results up, and nothing twice."""

import json
from pathlib import Path

import httpx
import pytest

from voice_agent.adapters.base import AdapterRejected, AdapterUnavailable
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


# --- Candidates from any centre: looked up at sign-in -------------------------------------


def other_centres_paper(cloud: Cloud, centre: str = "C2", paper_id: str = "other") -> None:
    """A second paper, with its own candidates, assigned to another centre."""
    paper = sample_package()
    paper = paper.model_copy(
        update={
            "package_id": f"{paper_id}-paper-1",
            "exam_id": f"exam-{paper_id}",
            "outline": paper.outline.model_copy(update={"exam_id": f"exam-{paper_id}"}),
            "candidates": tuple(
                c.model_copy(
                    update={
                        "candidate_id": f"{centre}{paper_id}-{c.candidate_id}",
                        "exam_number": f"{centre}{paper_id}{k}",
                    }
                )
                for k, c in enumerate(paper.candidates, 1)
            ),
        }
    )
    (cloud.packages / centre).mkdir(exist_ok=True)
    (cloud.packages / centre / f"{paper_id}.zip").write_bytes(write_package(paper))


async def test_results_go_back_to_the_centre_each_paper_was_pulled_for(cloud: Cloud) -> None:
    other_centres_paper(cloud)
    client = cloud.client()
    await client.pull()
    assert (await client.pull("C2")).imported == ("other-paper-1",)
    await answered(cloud, 1)  # C1's paper: 2 operations
    cloud.store.set_open("exam-other", True)
    platform = LocalAdapter(cloud.store)
    session = await platform.verify_candidate("C2other1", "")
    await platform.save_answer(session.candidate_id, 1, "B", "o:1")
    assert await client.push() == 3
    by_centre = {(op["centre"], op["operation_id"]) for op in cloud.received()}
    assert by_centre == {("C1", "s:1"), ("C1", "s:submit"), ("C2", "o:1")}
    await client.aclose()


async def test_the_platform_says_who_a_candidate_is(cloud: Cloud) -> None:
    other_centres_paper(cloud)
    client = cloud.client()
    by_number = await client.look_up("C2other1")
    by_id = await client.look_up("C2other-practice-01")
    assert by_number == by_id and by_id is not None
    assert (by_id.candidate_id, by_id.centre_id) == ("C2other-practice-01", "C2")
    assert await client.look_up("NOBODY") is None
    await client.aclose()


async def test_a_candidate_the_platform_knows_is_signed_in_from_any_centre(cloud: Cloud) -> None:
    from voice_agent.sync.lookup import CandidateLookup

    other_centres_paper(cloud)
    platform = LocalAdapter(cloud.store, admit=CandidateLookup(cloud.client(), cloud.store))
    # Nothing held yet: the paper comes down for the candidate's centre, closed as it is.
    session = await platform.verify_candidate("C2other3", "")
    assert session.candidate_id == "C2other-practice-03"
    assert [(e.exam_id, e.is_open) for e in cloud.store.exams()] == [("exam-other", False)]
    # By candidate ID too, as a browser that knows who is signing in sends it.
    assert (
        await platform.verify_candidate("C2other-practice-04", "")
    ).candidate_id == "C2other-practice-04"
    with pytest.raises(AdapterRejected):
        await platform.verify_candidate("NOBODY", "")


async def test_many_sign_ins_at_once_pull_their_centres_papers_once(cloud: Cloud) -> None:
    import asyncio

    from voice_agent.sync.lookup import CandidateLookup

    other_centres_paper(cloud)
    inner = httpx.ASGITransport(app=cloud.app)
    downloads = []

    class Counting(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "/v1/packages/" in request.url.path:
                downloads.append(request.url.path)
            return await inner.handle_async_request(request)

    platform = LocalAdapter(
        cloud.store, admit=CandidateLookup(cloud.client(Counting()), cloud.store)
    )
    sessions = await asyncio.gather(
        *(platform.verify_candidate(f"C2other{k}", "") for k in range(1, 7))
    )
    assert len({s.candidate_id for s in sessions}) == 6
    assert downloads == ["/v1/packages/other-paper-1"]


async def test_statuses_the_centre_refuses_keep_a_candidate_out(cloud: Cloud) -> None:
    from voice_agent.sync.lookup import CandidateLookup

    other_centres_paper(cloud)
    lookup = CandidateLookup(cloud.client(), cloud.store, refuse_statuses=["in_progress"])
    with pytest.raises(AdapterRejected):
        await LocalAdapter(cloud.store, admit=lookup).verify_candidate("C2other1", "")
    assert cloud.store.exams() == []  # nothing pulled for someone kept out


async def test_a_platform_that_cannot_be_reached_is_said_plainly(cloud: Cloud) -> None:
    from voice_agent.sync.lookup import CandidateLookup

    class Down(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

    lookup = CandidateLookup(cloud.client(Down()), cloud.store)
    with pytest.raises(AdapterUnavailable):
        await LocalAdapter(cloud.store, admit=lookup).verify_candidate("C2other1", "")


async def test_the_centre_comes_from_the_field_the_centre_names() -> None:
    from voice_agent.sync.models import CandidateRecord

    record = CandidateRecord.model_validate_json(
        '{"candidate_id": "c1", "centre_id": "a1b2", "centre_code": "ABI-03", '
        '"centre_name": "Main Hall", "status": "IN_PROGRESS"}'
    )
    assert (record.centre_id, record.centre_code) == ("a1b2", "ABI-03")  # others ignored


async def test_only_the_candidates_own_paper_comes_down(cloud: Cloud) -> None:
    from voice_agent.sync.lookup import CandidateLookup

    other_centres_paper(cloud)
    other_centres_paper(cloud, paper_id="second")
    platform = LocalAdapter(cloud.store, admit=CandidateLookup(cloud.client(), cloud.store))
    await platform.verify_candidate("C2second2", "")
    assert [e.exam_id for e in cloud.store.exams()] == ["exam-second"]


async def test_without_the_paper_call_the_centres_papers_all_come_down(cloud: Cloud) -> None:
    from voice_agent.sync.lookup import CandidateLookup

    other_centres_paper(cloud)
    other_centres_paper(cloud, paper_id="second")
    inner = httpx.ASGITransport(app=cloud.app)

    class OlderPlatform(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/package"):
                return httpx.Response(404, json={"detail": "Not Found"})
            return await inner.handle_async_request(request)

    lookup = CandidateLookup(cloud.client(OlderPlatform()), cloud.store)
    session = await LocalAdapter(cloud.store, admit=lookup).verify_candidate("C2second2", "")
    assert session.candidate_id == "C2second-practice-02"
    assert sorted(e.exam_id for e in cloud.store.exams()) == ["exam-other", "exam-second"]

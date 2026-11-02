"""What a platform implementation needs to provide for the conformance suite.

The suite talks only to the ``ExamDataAdapter`` protocol. It runs against the fake adapter
and the centre's exam store (the local platform); in phase 7 the same tests run through
the HTTP adapter against the platform's test instance, which must provide the candidates
named in the profile, reset before each run (the submission test consumes
``submit_reg_no``).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from voice_agent.adapters.base import ExamDataAdapter
from voice_agent.adapters.fake_adapter import FakeAdapter
from voice_agent.examstore.__main__ import sample_package
from voice_agent.examstore.adapter import LocalAdapter
from voice_agent.examstore.package import read_package, write_package
from voice_agent.examstore.store import ExamStore


class ConformanceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    exam_code: str
    reg_no: str
    other_reg_no: str
    submit_reg_no: str
    unregistered_reg_no: str


PROFILES = {
    "fake": ConformanceProfile(
        exam_code="UTME-SAMPLE",
        reg_no="10000001AA",
        other_reg_no="10000002AA",
        submit_reg_no="10000003AA",
        unregistered_reg_no="99999999ZZ",
    ),
    # The centre's exam store, loaded with the practice package and opened.
    "local": ConformanceProfile(
        exam_code="UTME-SAMPLE",
        reg_no="20260001PR",
        other_reg_no="20260002PR",
        submit_reg_no="20260003PR",
        unregistered_reg_no="99999999ZZ",
    ),
}


@pytest.fixture(params=sorted(PROFILES))
def platform(request: pytest.FixtureRequest) -> str:
    kind: str = request.param
    return kind


@pytest.fixture
def profile(platform: str) -> ConformanceProfile:
    return PROFILES[platform]


@pytest.fixture
def adapter(platform: str, tmp_path: Path) -> Iterator[ExamDataAdapter]:
    if platform == "fake":
        yield FakeAdapter.with_bundled_fixtures()
        return
    store = ExamStore(tmp_path / "exams.db")
    store.import_package(read_package(write_package(sample_package())))
    store.set_open("exam-sample-60", True)
    yield LocalAdapter(store)
    store.close()

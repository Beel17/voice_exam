"""What a platform implementation needs to provide for the conformance suite.

The suite talks only to the ``ExamDataAdapter`` protocol. Today it runs against the fake
adapter; in phase 7 the same tests run through the HTTP adapter against the platform's
test instance, which must provide the candidates named in the profile, reset before each
run (the submission test consumes ``submit_reg_no``).
"""

import pytest
from pydantic import BaseModel, ConfigDict

from voice_agent.adapters.base import ExamDataAdapter
from voice_agent.adapters.fake_adapter import FakeAdapter


class ConformanceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    exam_code: str
    reg_no: str
    other_reg_no: str
    submit_reg_no: str
    unregistered_reg_no: str


@pytest.fixture
def profile() -> ConformanceProfile:
    return ConformanceProfile(
        exam_code="UTME-SAMPLE",
        reg_no="10000001AA",
        other_reg_no="10000002AA",
        submit_reg_no="10000003AA",
        unregistered_reg_no="99999999ZZ",
    )


@pytest.fixture
def adapter() -> ExamDataAdapter:
    return FakeAdapter.with_bundled_fixtures()

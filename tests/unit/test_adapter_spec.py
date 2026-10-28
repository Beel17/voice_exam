from pathlib import Path
from typing import Any

from voice_agent.adapters.base import RejectReason
from voice_agent.adapters.spec import REJECT_STATUS, build_openapi, render

SPEC_FILE = Path(__file__).parents[2] / "spec" / "adapter.openapi.json"


def test_committed_spec_matches_the_code() -> None:
    assert SPEC_FILE.read_text(encoding="utf-8") == render(), (
        "spec is stale: uv run python -m voice_agent.adapters.spec spec/adapter.openapi.json"
    )


def test_every_reject_reason_has_a_status() -> None:
    assert set(REJECT_STATUS) == set(RejectReason)


def _refs(node: Any) -> set[str]:
    if isinstance(node, dict):
        own = {node["$ref"]} if "$ref" in node else set()
        return own | {r for v in node.values() for r in _refs(v)}
    if isinstance(node, list):
        return {r for v in node for r in _refs(v)}
    return set()


def test_every_reference_resolves() -> None:
    spec = build_openapi()
    schemas = spec["components"]["schemas"]
    for ref in _refs(spec):
        assert ref.removeprefix("#/components/schemas/") in schemas, ref


def test_spec_covers_the_seven_adapter_calls() -> None:
    operations = [(m, p) for p, ops in build_openapi()["paths"].items() for m in ops]
    assert len(operations) == 7


def test_question_schema_cannot_carry_an_answer_key() -> None:
    schemas = build_openapi()["components"]["schemas"]
    for name in ("Question", "Option"):
        assert schemas[name]["additionalProperties"] is False
    assert set(schemas["Question"]["properties"]) == {"number", "text", "spoken_text", "options"}
    assert set(schemas["Option"]["properties"]) == {"letter", "text", "spoken_text"}

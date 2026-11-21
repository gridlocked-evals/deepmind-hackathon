from copy import deepcopy
import json
from pathlib import Path

from pydantic import ValidationError
import pytest

from backend.contracts import ObservationBatch, VerticalSlice
from scripts.export_contracts import generated_files

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/contracts"
FIXTURE = json.loads((FIXTURES / "vertical-slice.json").read_text())
INVALID = json.loads((FIXTURES / "invalid-cases.json").read_text())


def changed_fixture(changes):
    value = deepcopy(FIXTURE)
    for change in changes:
        node = value
        for key in change["path"][:-1]:
            node = node[int(key)] if isinstance(node, list) else node[key]
        key = change["path"][-1]
        node[int(key) if isinstance(node, list) else key] = change["value"]
    return value


def test_vertical_slice_round_trips_through_backend_schema():
    parsed = VerticalSlice.model_validate(FIXTURE)
    assert VerticalSlice.model_validate_json(parsed.model_dump_json()) == parsed
    assert parsed.observations.trace.modelId == "gemini-4-hydrogen"
    assert parsed.edit.trace.modelId == "gemini-4-carbon"
    assert parsed.session.replaySemantics == "stochastic_no_exact_reset"


@pytest.mark.parametrize("case", INVALID, ids=[item["name"] for item in INVALID])
def test_shared_malformed_contract_cases_fail(case):
    with pytest.raises(ValidationError):
        VerticalSlice.model_validate(changed_fixture(case["changes"]))


def test_snapshot_accepts_visible_facts_but_not_temporal_claims():
    batch = deepcopy(FIXTURE["observations"])
    batch["capture"].update(modality="snapshot", durationSeconds=0, allowedInterval={"startSeconds": 0, "endSeconds": 0})
    for observation in batch["observations"]:
        for ref in observation["evidence"]:
            ref.update(timeSeconds=0, frameIndex=0)
    ObservationBatch.model_validate(batch)
    batch["observations"][0]["category"] = "event"
    with pytest.raises(ValidationError, match="temporal events"):
        ObservationBatch.model_validate(batch)


def test_generated_frontend_contracts_are_current():
    for path, content in generated_files().items():
        assert path.read_text() == content, f"Run python scripts/export_contracts.py; stale: {path.name}"

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.contracts import SCHEMA_VERSION, VerticalSlice
from backend.main import app

FIXTURE = json.loads((Path(__file__).resolve().parent.parent / "contracts/fixtures/vertical_slice.v1.json").read_text())


def mutated(change):
    data = copy.deepcopy(FIXTURE)
    change(data)
    return data


def rejects(change, message):
    with pytest.raises(ValidationError, match=message):
        VerticalSlice.model_validate(mutated(change))


def test_vertical_slice_fixture_is_valid_and_round_trips():
    slice_ = VerticalSlice.model_validate(FIXTURE)
    assert slice_.schemaVersion == SCHEMA_VERSION
    assert slice_.worldSession.deterministicReplay is False
    again = VerticalSlice.model_validate(json.loads(slice_.model_dump_json()))
    assert again == slice_


def test_schema_endpoint_publishes_every_contract():
    with TestClient(app) as client:
        body = client.get("/api/contracts/element119").json()
    assert body["schemaVersion"] == SCHEMA_VERSION
    assert {"ObservationBatch", "StreetEdit", "ReferenceImage", "GateDecision", "WorldSession",
            "NavigationEvent", "EvaluationResult", "Capture", "VerticalSlice"} <= set(body["contracts"])


@pytest.mark.parametrize("path,value", [
    (("capture", "id"), "capture-1"),
    (("observationBatch", "id"), "obs_x"),
    (("capture", "cameraId"), "../etc/passwd"),
    (("capture", "mediaSha256"), "sha256:XYZ"),
])
def test_malformed_ids_and_hashes_fail(path, value):
    def change(d):
        d[path[0]][path[1]] = value
    rejects(change, "String should match pattern")


def test_invalid_image_regions_fail():
    rejects(lambda d: d["observationBatch"]["observations"][0].update(region={"kind": "box", "x": 0.9, "y": 0.1, "w": 0.2, "h": 0.1}),
            "outside the image")
    rejects(lambda d: d["observationBatch"]["anchors"][0].update(region={"kind": "polygon", "points": [[0, 0], [0.5, 0.5], [1, 1]]}),
            "zero area")
    rejects(lambda d: d["streetEdit"]["operations"][0].update(region={"kind": "box", "x": -0.1, "y": 0, "w": 0.2, "h": 0.2}),
            "greater than or equal to 0")


def test_contradictory_or_violated_permissions_fail():
    rejects(lambda d: d["streetEdit"]["permissions"]["forbidden"].append("planter"), "contradictory permissions: planter")
    rejects(lambda d: d["streetEdit"]["operations"][0].update(target="building_facade"), "forbidden class building_facade")
    rejects(lambda d: d["streetEdit"]["operations"][0].update(target="plaza"), "plaza, which is not allowed")


def test_out_of_range_time_references_fail():
    rejects(lambda d: d["observationBatch"]["observations"][0]["time"].update(endS=31.0), "outside the capture")
    rejects(lambda d: d["observationBatch"]["observations"][0]["time"].update(startS=10, endS=5), "ends before it starts")
    rejects(lambda d: d["capture"].update(durationS=121), "less than or equal to 120")
    rejects(lambda d: d["navigationEvents"][1].update(sessionTimeS=0.5), "non-decreasing")


def test_snapshot_evidence_cannot_support_motion():
    def change(d):
        d["capture"].update(sourceKind="snapshot", durationS=0)
        d["observationBatch"]["motionSupported"] = False
        for o in d["observationBatch"]["observations"]:
            o["time"] = {"startS": 0, "endS": 0}
    rejects(change, "movement observations require motion")


def test_genie_sessions_cannot_carry_simulator_identity_or_replay_claims():
    rejects(lambda d: d["worldSession"].update(seed=42), "Extra inputs are not permitted")
    rejects(lambda d: d["worldSession"].update(branchId="b1"), "Extra inputs are not permitted")
    rejects(lambda d: d["worldSession"].update(deterministicReplay=True), "Input should be False")


def test_gates_are_operational_and_never_count_unknown_as_accept():
    rejects(lambda d: d["referenceGate"]["checks"][0].update(answer="unknown"), "expected unknown")
    def unknown_but_allowed(d):
        d["referenceGate"]["checks"][0]["answer"] = "unknown"
        d["referenceGate"]["outcome"] = "unknown"
    rejects(unknown_but_allowed, "inconsistent with a unknown reference_image gate")
    def blocked(d):
        d["referenceGate"]["checks"][0]["answer"] = "fail"
        d["referenceGate"].update(outcome="reject", effect="block_world_creation")
    rejects(blocked, "requires an accepted reference gate")


def test_required_model_roles_are_enforced():
    rejects(lambda d: d["streetEdit"]["modelCall"].update(service="hydrogen"), "compiled by Carbon")
    rejects(lambda d: d["observationBatch"]["modelCall"].update(service="carbon"), "must come from Hydrogen")
    rejects(lambda d: d["navigationEvents"][1].update(modelCall=None), "need a Hydrogen model call")


def test_links_hashes_and_shape_are_checked():
    rejects(lambda d: d["streetEdit"]["operations"][0].update(anchorIds=["anc_missing-1"]), "unknown anchors")
    rejects(lambda d: d["observationBatch"]["modelCall"].update(inputHashes=["sha256:" + "0" * 64]), "original capture media")
    rejects(lambda d: d["referenceImage"].update(width=1024, height=768), "exactly 16:9")
    rejects(lambda d: d["evaluation"].update(unknown=0), "unknown outcomes are never dropped")

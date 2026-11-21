import asyncio
import json

import pytest

from backend.artifacts import ArtifactError, ArtifactStore
from backend.edits import EditBundle, EditRequestError, compile_edit
from backend.gateway import GatewayError
from backend.test_pipeline_helpers import FakeProvider, batch, carbon_ok, client, gateway, stored_frame


def compile_with(tmp_path, outputs, request="Turn the curb lane into a protected bike lane with planters.", **kw):
    provider = FakeProvider()
    provider.carbon = list(outputs)
    store = ArtifactStore(tmp_path / "pipeline")

    async def run():
        async with client(provider) as http:
            gw = gateway(tmp_path, http)
            frame = stored_frame(store)
            results = []
            for _ in outputs:
                results.append(await compile_edit(gw, store, batch=batch(), preset_name=kw.get("preset", "protected_bike_lane"),
                                                  mayor_request=request, frame=frame,
                                                  capture_media_sha256=frame["captureMediaSha256"],
                                                  allowed=kw.get("allowed"), forbidden=kw.get("forbidden")))
            return results
    return asyncio.run(run()), provider, store


def test_compiled_edit_has_real_carbon_trace_and_stage_artifacts(tmp_path):
    [bundle], provider, store = compile_with(tmp_path, [carbon_ok()])
    edit = bundle.streetEdit
    assert edit.status == "compiled" and len(edit.operations) == 2
    assert edit.modelCall.service == "carbon" and edit.modelCall.modelId == "gemini-4-carbon"
    assert edit.modelCall.costUsd and edit.modelCall.inputTokens == 100
    assert bundle.sourceFrame["frameSha256"] in edit.modelCall.inputHashes
    assert bundle.captureMediaSha256 in edit.modelCall.inputHashes
    assert all(op.provenance == "inserted" for op in edit.operations)
    assert any(u.field == "metricGeometry" for u in edit.unknowns)
    assert any(u.field == "curbLineBehindVan" for u in edit.unknowns)
    assert bundle.imageInstructions and bundle.worldPrompt and bundle.preservedAnchorIds == ["anc_fixture-xwalk"]
    assert bundle.crossingAnchorIds == ["anc_fixture-xwalk"]
    assert set(bundle.operationResults.values()) == {"bike_lane", "planter"}
    # Persisted, immutable, and Carbon actually received evidence and the original frame.
    assert EditBundle.model_validate(store.get("edits", edit.id)) == bundle
    sent = provider.requests[0][1]
    assert sent["response_format"]["mime_type"] == "application/json"
    schema = json.dumps(sent["response_format"]["schema"])
    assert "maxLength" not in schema and "maxItems" not in schema and '"description"' in schema  # property kept
    assert "anc_fixture-curb" in sent["input"][0]["text"] and sent["input"][1]["type"] == "image"
    # Hydrogen observations are not rewritten by the edit.
    assert batch().anchors[0].semanticClass == "parking_lane"


def test_different_requests_yield_materially_different_edits(tmp_path):
    paint = carbon_ok(operations=[carbon_ok()["operations"][0]],
                      imageInstructions="Repaint the right curb lane green with bike symbols; no planters.")
    [a], _, _ = compile_with(tmp_path / "a", [carbon_ok()])
    [b], provider, _ = compile_with(tmp_path / "b", [paint], request="Paint-only bike lane, no planters.")
    assert "Paint-only bike lane" in provider.requests[0][1]["input"][0]["text"]
    assert len(a.streetEdit.operations) != len(b.streetEdit.operations)
    assert a.imageInstructions != b.imageInstructions


@pytest.mark.parametrize("change,needle", [
    ({"preservedAnchorIds": []}, "crossings"),
    ({"laneUse": {"allowed": ["cyclist", "general_traffic"], "forbidden": ["general_traffic"]}}, "contradictory lane use"),
    ({"laneUse": {"allowed": ["cyclist"], "forbidden": []}}, "must forbid general_traffic"),
    ({"imageInstructions": "Add a 1.8 m wide green lane."}, "metric"),
    ({"worldPrompt": "A street that is now safer for everyone."}, "safety"),
    ({"operations": [{**carbon_ok()["operations"][0], "anchorIds": ["anc_missing"]}]}, "unknown anchors"),
    ({"operations": [{**carbon_ok()["operations"][0], "points": [[0.0, 0.0], [0.2, 0.0], [0.1, 0.2]]}]}, "unsupported"),
    ({"operations": [{**carbon_ok()["operations"][0], "becomes": "bus_lane"}]}, "must turn"),
    ({"operations": [{**carbon_ok()["operations"][0], "target": "sidewalk"}]}, "does not allow"),
    ({"operations": [{**carbon_ok()["operations"][1]}]}, "never creates a bike_lane"),
    ({"operations": [{**carbon_ok()["operations"][0], "evidenceObservationIds": ["ob_ghost"]}]}, "unknown observations"),
])
def test_validator_rejects_bad_carbon_output_visibly(tmp_path, change, needle):
    [bundle], _, store = compile_with(tmp_path, [carbon_ok(**change)])
    edit = bundle.streetEdit
    assert edit.status == "rejected" and edit.operations == []
    assert edit.rejectionReason.startswith("validation:") and needle in edit.rejectionReason
    assert bundle.carbonOutput and bundle.validationErrors and bundle.imageInstructions is None
    assert store.exists("edits", edit.id)


def test_ambiguous_and_conflicting_requests_pass_through_as_clarification_or_rejection(tmp_path):
    clarify = {"status": "needs_clarification", "reason": "Which side of the street should get the bike lane?"}
    reject = {"status": "rejected", "reason": "Removing the crosswalk is outside the bike-lane preset."}
    [a, b], _, _ = compile_with(tmp_path, [clarify, reject], request="Bike lane. Also remove the crosswalk?")
    assert a.streetEdit.status == "needs_clarification" and "Which side" in a.streetEdit.rejectionReason
    assert b.streetEdit.status == "rejected" and not b.validationErrors
    assert a.imageInstructions is None and b.worldPrompt is None


def test_contradictory_permissions_are_rejected_before_any_model_call(tmp_path):
    for kw in ({"allowed": ["bike_lane"], "forbidden": ["bike_lane"]}, {"allowed": ["bus_lane"]},
               {"preset": "skyway"}):
        with pytest.raises(EditRequestError):
            compile_with(tmp_path / str(len(kw)), [carbon_ok()], **kw)


def test_busway_and_plaza_have_their_own_semantics(tmp_path):
    bus = carbon_ok(operations=[{**carbon_ok()["operations"][0], "becomes": "bus_lane"}],
                    laneUse={"allowed": ["bus", "emergency"], "forbidden": ["general_traffic"]},
                    imageInstructions="Paint the curb lane red with BUS ONLY markings.")
    [ok], _, _ = compile_with(tmp_path / "bus", [bus], preset="busway")
    assert ok.streetEdit.status == "compiled"
    plaza = carbon_ok(operations=[{**carbon_ok()["operations"][0], "becomes": "plaza"}],
                      laneUse={"allowed": ["pedestrian"], "forbidden": ["general_traffic"]})
    [bad], _, _ = compile_with(tmp_path / "plaza", [plaza], preset="plaza")
    assert bad.streetEdit.status == "rejected" and "must forbid" in bad.streetEdit.rejectionReason


def test_carbon_failure_surfaces_as_gateway_error_without_an_edit(tmp_path):
    with pytest.raises(GatewayError):
        compile_with(tmp_path, [500])
    assert ArtifactStore(tmp_path / "pipeline").list("edits") == []


def test_artifacts_are_immutable(tmp_path):
    store = ArtifactStore(tmp_path)
    store.put("edits", "edit_abcd", {"a": 1})
    store.put("edits", "edit_abcd", {"a": 1})
    with pytest.raises(ArtifactError):
        store.put("edits", "edit_abcd", {"a": 2})
    store.claim("bindings", "claim_abcd", {"x": 1})
    with pytest.raises(ArtifactError):
        store.claim("bindings", "claim_abcd", {"x": 1})

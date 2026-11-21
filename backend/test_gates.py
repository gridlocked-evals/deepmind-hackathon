from datetime import datetime, timezone
import os

import httpx
import pytest

from backend.artifacts import ArtifactError
from backend.contracts import ModelCall, Permissions, SessionBudget, WorldSession
from backend.edits import EditBundle
from backend.gates import (authorize_world, bind_world, check_world_session, claim_world, gate_reference,
                           load_thresholds, run_gated_reference)
from backend.references import MAX_ATTEMPTS, generate_reference
from backend.test_pipeline_helpers import FakeProvider, answers, png, run, setup


def world(ref_id, gate_id, world_id="world_test-0001"):
    now = datetime.now(timezone.utc)
    return WorldSession(id=world_id, referenceImageId=ref_id, gateDecisionId=gate_id, status="requested",
                        budget=SessionBudget(maxSeconds=15, maxUsd=1, spentSeconds=0, spentUsd=0),
                        modelCall=ModelCall(id="call_world-0001", service="genie", modelId="genie-4",
                                            promptId="p", configId="c", inputHashes=["sha256:" + "b" * 64],
                                            requestedAt=now, outcome="ok"))


def test_accept_allows_exactly_one_hash_bound_world(tmp_path):
    provider = FakeProvider()
    store, bundle = setup(tmp_path, provider)
    provider.images, provider.decisions = [png()], [answers()]
    result = run(tmp_path, provider, lambda gw: run_gated_reference(gw, store, bundle, style="photoreal"))
    assert result["state"] == "accept" and len(result["history"]) == 1
    gate = result["history"][0]["gate"]
    decision = gate["gateDecision"]
    assert decision["outcome"] == "accept" and decision["effect"] == "allow_world_creation"
    assert gate["thresholds"]["version"] == "reference_gate.v1" and gate["thresholds"]["sha256"]
    assert {c["question"] for c in decision["checks"]} >= {"deterministic:format_dimensions", "deterministic:permissions"}
    assert any("crosswalk" in c["question"] for c in decision["checks"])  # evidence has a crossing
    ref_id, gate_id = result["referenceImageId"], result["gateDecisionId"]
    ref = store.get("references", ref_id)["referenceImage"]
    assert ref["imageSha256"] in decision["modelCall"]["inputHashes"]
    sent = [b for m, b in provider.requests if m == "gemini-decision-1"][0]
    assert [p["type"] for p in sent["input"]] == ["text", "image", "text", "image"]

    auth = claim_world(store, ref_id, gate_id)
    assert auth["imageSha256"] == ref["imageSha256"] and auth["worldPrompt"] == bundle.worldPrompt
    with pytest.raises(ArtifactError):
        claim_world(store, ref_id, gate_id)  # one session per accepted gate
    session = world(ref_id, gate_id)
    with pytest.raises(ArtifactError):
        bind_world(store, session, "sha256:" + "0" * 64)  # different bytes than the gate judged
    bind_world(store, session, auth["imageSha256"])
    assert check_world_session(store, session)["worldSessionId"] == session.id
    with pytest.raises(ArtifactError):
        check_world_session(store, world(ref_id, gate_id, "world_other-0001"))


def test_reject_drives_bounded_re_edit_with_feedback(tmp_path):
    provider = FakeProvider()
    store, bundle = setup(tmp_path, provider)
    provider.images = [png(color=(1, 1, 1)), png(color=(2, 2, 2))]
    provider.decisions = [answers(requested_change=(0.1, 0.9)), answers()]
    result = run(tmp_path, provider, lambda gw: run_gated_reference(gw, store, bundle, style="photoreal"))
    assert result["state"] == "accept" and len(result["history"]) == 2
    first = result["history"][0]["gate"]
    assert first["state"] == "re_edit" and first["gateDecision"]["effect"] == "block_world_creation"
    assert "requested change" in first["feedback"]
    second_prompt = [b for m, b in provider.requests if m == "gemini-nano-banana-2.1"][1]["input"][0]["text"]
    assert "REJECTED BY THE REVIEW GATE" in second_prompt and first["feedback"] in second_prompt
    with pytest.raises(ArtifactError):  # the rejected image can never start a world
        authorize_world(store, result["history"][0]["attempt"]["id"], first["gateDecision"]["id"])


def test_retry_exhaustion_rejects_and_keeps_every_attempt(tmp_path):
    provider = FakeProvider()
    store, bundle = setup(tmp_path, provider)
    provider.images = [png(color=(i, i, i)) for i in range(5)]
    provider.decisions = [answers(unambiguous=(0.05, 0.95))]
    result = run(tmp_path, provider, lambda gw: run_gated_reference(gw, store, bundle, style="cel_shaded"))
    assert result["state"] == "rejected" and len(result["history"]) == MAX_ATTEMPTS
    assert [h["gate"]["state"] for h in result["history"]] == ["re_edit", "re_edit", "rejected"]
    assert provider.count("gemini-decision-1") == MAX_ATTEMPTS


@pytest.mark.parametrize("script,reason", [
    ([answers(anchors_visible=(0.9, 0.55))], "confidence"),
    ([answers(viewpoint_preserved=(0.75, 0.75))], "between"),
])
def test_low_confidence_or_uncertain_answers_block_without_resampling(tmp_path, script, reason):
    provider = FakeProvider()
    store, bundle = setup(tmp_path, provider)
    provider.images, provider.decisions = [png()], script
    result = run(tmp_path, provider, lambda gw: run_gated_reference(gw, store, bundle, style="photoreal"))
    assert result["state"] == "insufficient_evidence" and len(result["history"]) == 1
    decision = result["history"][0]["gate"]["gateDecision"]
    assert decision["outcome"] == "unknown" and decision["effect"] == "block_world_creation"
    assert any(reason in c["rationale"] for c in decision["checks"])
    again = run(tmp_path, provider, lambda gw: gate_reference(gw, store, bundle, result["referenceImageId"]))
    assert again.gateDecision.id == decision["id"] and provider.count("gemini-decision-1") == 1
    with pytest.raises(ArtifactError):
        authorize_world(store, result["referenceImageId"], decision["id"])


def test_judge_timeout_is_insufficient_evidence(tmp_path):
    provider = FakeProvider()
    store, bundle = setup(tmp_path, provider)
    provider.images, provider.decisions = [png()], [httpx.ReadTimeout("slow")]
    result = run(tmp_path, provider, lambda gw: run_gated_reference(gw, store, bundle, style="photoreal"))
    gate = result["history"][0]["gate"]
    assert result["state"] == "insufficient_evidence" and gate["judgeError"]["code"] == "timeout"
    assert gate["gateDecision"]["modelCall"]["outcome"] == "timeout"
    assert gate["answers"] is None


def test_deterministic_failure_rejects_without_re_edit(tmp_path):
    provider = FakeProvider()
    store, bundle = setup(tmp_path, provider)
    provider.images, provider.decisions = [png()], [answers(requested_change=(0.1, 0.9))]
    rec = run(tmp_path, provider, lambda gw: generate_reference(gw, store, bundle, style="photoreal"))
    blob = store.root / "blobs" / rec.referenceImage.imageSha256.split(":")[1]
    os.chmod(blob, 0o644)
    blob.write_bytes(png((1280, 720), color=(0, 0, 0)))  # tampered after generation
    gate = run(tmp_path, provider, lambda gw: gate_reference(gw, store, bundle, rec.id))
    assert gate.state == "rejected" and gate.deterministic[0]["answer"] == "fail"
    assert gate.gateDecision.modelCall.outcome == "blocked" and provider.count("gemini-decision-1") == 0


@pytest.mark.parametrize("permission", ["forbidden", "unlisted", "missing_result"])
def test_gate_rejects_legacy_edit_with_unpermitted_or_missing_destination(tmp_path, permission):
    provider = FakeProvider()
    store, bundle = setup(tmp_path, provider)
    data = bundle.model_dump(mode="json")
    if permission == "missing_result":
        for operation in bundle.streetEdit.operations:
            if operation.op == "modify":
                del data["operationResults"][operation.id]
    else:
        permissions = bundle.streetEdit.permissions
        data["streetEdit"]["permissions"] = Permissions(
            allowed=[kind for kind in permissions.allowed if kind != "bike_lane"],
            forbidden=[*permissions.forbidden, *(["bike_lane"] if permission == "forbidden" else [])]
        ).model_dump(mode="json")
    # These old bundles still satisfy StreetEdit's source-target contract; the gate must recheck results.
    legacy = EditBundle.model_validate(data)
    provider.images, provider.decisions = [png()], [answers()]
    reference = run(tmp_path, provider, lambda gw: generate_reference(gw, store, legacy, style="photoreal"))
    gate = run(tmp_path, provider, lambda gw: gate_reference(gw, store, legacy, reference.id))
    assert gate.state == "rejected" and gate.gateDecision.effect == "block_world_creation"
    assert next(c for c in gate.deterministic if c["name"] == "permissions")["answer"] == "fail"
    assert gate.gateDecision.modelCall.outcome == "blocked" and provider.count("gemini-decision-1") == 0


def test_thresholds_file_is_versioned_and_valid():
    t = load_thresholds()
    assert t["version"] == "reference_gate.v1" and t["sha256"].startswith("sha256:")
    assert set(t["questions"]) == {"viewpoint_preserved", "anchors_visible", "requested_change",
                                   "crossing_continuity", "unambiguous"}

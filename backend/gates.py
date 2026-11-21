"""Operational Decisions gate for reference images (issue #8).

One ``gemini-decision-1`` call per reference image asks atomic visual questions
about the original frame, the edited reference and the typed edit. Frozen
thresholds turn its typed answers into pass/fail/unknown, combined with
deterministic format, coordinate, permission and provenance checks. The gate
decides whether a Genie world may be created:

    accept               -> allow_world_creation (bound by hash to one session)
    re_edit              -> reject this image; at most two feedback-driven re-edits
    rejected             -> reject; deterministic failure or re-edits exhausted
    insufficient_evidence-> unknown/low confidence/timeout; block, never re-sample

Probabilities are visual judgments by a model. They are not crash
probabilities, safety ratings or proof of geographic identity.
"""
from __future__ import annotations

import asyncio
import base64
from io import BytesIO
import json
from pathlib import Path
from uuid import uuid4

from PIL import Image
from pydantic import BaseModel, ConfigDict

from .artifacts import ArtifactError, ArtifactStore, sha256, utcnow
from .contracts import GATE_EFFECTS, GateCheck, GateDecision, GateSubject, ModelCall, ReferenceImage, WorldSession
from .edits import EditBundle
from .gateway import CallContext, GatewayError, ModelGateway, Service
from .gateway.contracts import MODELS
from .modelcalls import from_error, from_result
from .references import MAX_ATTEMPTS, TARGET_SIZE, attempts, generate_reference, validate_transport

PROMPT_ID = "decisions.reference-gate.v1"
THRESHOLDS_PATH = Path(__file__).resolve().parent / "thresholds" / "reference_gate.v1.json"
REPAIRABLE = {"anchors_visible", "requested_change", "crossing_continuity", "unambiguous", "viewpoint_preserved"}
FEEDBACK = {
    "viewpoint_preserved": "keep the original camera position, angle and framing",
    "anchors_visible": "keep the preserved landmarks visible in their original positions",
    "requested_change": "make the requested change clearly visible in the indicated region",
    "crossing_continuity": "keep every crosswalk present and continuous",
    "unambiguous": "remove ambiguous or contradictory features (mixed markings, merged objects, garbled text)",
}


class GateError(ArtifactError):
    pass


def load_thresholds(path: Path = THRESHOLDS_PATH) -> dict:
    raw = path.read_bytes()
    data = json.loads(raw)
    for name, t in data["questions"].items():
        if not 0 <= t["failAt"] < t["passAt"] <= 1 or not 0 <= t["minConfidence"] <= 1:
            raise ValueError(f"invalid thresholds for {name}")
    return {**data, "sha256": sha256(raw)}


class GateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    gateDecision: GateDecision
    state: str  # accept | re_edit | rejected | insufficient_evidence
    referenceImageId: str
    streetEditId: str
    attempt: int
    feedback: str | None
    thresholds: dict
    answers: dict | None
    deterministic: list[dict]
    judgeError: dict | None = None
    disclaimer: str = ("Gate probabilities are model visual judgments. They are not crash probabilities, "
                       "safety ratings or proof that the image depicts the real location.")


# ---------------------------------------------------------------- questions

def build_questions(bundle: EditBundle) -> dict:
    labels, has_crossings = bundle.anchorLabels, bool(bundle.crossingAnchorIds)
    ops = "; ".join(f"{op.op} {op.target}: {op.description}" for op in bundle.streetEdit.operations)
    preserved = ", ".join(labels.get(a, a) for a in bundle.preservedAnchorIds) or "the buildings and sidewalks"
    q = {
        "viewpoint_preserved": "Compared with the SOURCE image, does the EDITED image keep the same camera position,"
                               " angle and framing?",
        "anchors_visible": f"Are these landmarks from the SOURCE image still visible in approximately the same"
                           f" positions in the EDITED image: {preserved}?",
        "requested_change": f"Does the EDITED image clearly show this requested change: {ops}?",
        "unambiguous": "Is the EDITED image free of ambiguous or contradictory street features, such as mixed or"
                       " overlapping lane markings, objects merged into each other, duplicated vehicles, or garbled text?",
    }
    if has_crossings:
        q["crossing_continuity"] = ("Are all crosswalks visible in the SOURCE image still present and continuous"
                                    " (not erased, broken or covered) in the EDITED image?")
    return {name: {"type": "noul", "instructions": text} for name, text in q.items()}


def classify(answer: dict, t: dict) -> tuple[str, float, str]:
    p, conf = answer["noul"], answer["confidence"]
    if conf < t["minConfidence"]:
        return "unknown", p, f"judge confidence {conf:.2f} below {t['minConfidence']}"
    if p >= t["passAt"]:
        return "pass", p, f"P(yes)={p:.2f} >= {t['passAt']}"
    if p <= t["failAt"]:
        return "fail", p, f"P(yes)={p:.2f} <= {t['failAt']}"
    return "unknown", p, f"P(yes)={p:.2f} between {t['failAt']} and {t['passAt']}"


def deterministic_checks(store: ArtifactStore, bundle: EditBundle, record: dict) -> list[dict]:
    ref = ReferenceImage.model_validate(record["referenceImage"])
    edit = bundle.streetEdit
    checks = []

    def add(name, ok, why):
        checks.append({"name": name, "answer": "pass" if ok else "fail", "rationale": why})

    try:
        data = store.get_blob(ref.imageSha256)
        with Image.open(BytesIO(data)) as image:
            size = image.size
        validate_transport(data, record["genieTransport"]["mimeType"])
        add("format_dimensions", size == TARGET_SIZE, f"decoded {size[0]}x{size[1]}, hash verified, Genie transport valid")
    except (ArtifactError, OSError, KeyError, TypeError) as exc:
        add("format_dimensions", False, getattr(exc, "detail", {}).get("error", "image bytes failed verification"))
    add("provenance", ref.streetEditId == edit.id and ref.captureId == bundle.captureId
        and bool(bundle.sourceFrame) and bundle.sourceFrame["frameSha256"] in ref.modelCall.inputHashes,
        "reference links to this edit, capture and original frame hash")
    inside = all(0 <= x <= 1 and 0 <= y <= 1 for op in edit.operations if op.region.kind == "polygon"
                 for x, y in op.region.points)
    add("coordinates", inside and bool(edit.operations), "all operation regions are normalized image coordinates")
    allowed = all(op.target in edit.permissions.allowed and op.target not in edit.permissions.forbidden
                  for op in edit.operations)
    add("permissions", edit.status == "compiled" and allowed, "every operation target is permitted by the edit")
    return checks


# ---------------------------------------------------------------- gate

def _gate_id(reference_id: str) -> str:
    return "gate_" + reference_id.removeprefix("ref_")


_locks: dict[str, asyncio.Lock] = {}


async def gate_reference(gateway: ModelGateway, store: ArtifactStore, bundle: EditBundle, reference_id: str, *,
                         thresholds: dict | None = None,
                         purpose: str = "development") -> GateRecord:
    """Judge one reference exactly once. A second call returns the stored decision."""
    async with _locks.setdefault(reference_id, asyncio.Lock()):
        gate_id = _gate_id(reference_id)
        if store.exists("gates", gate_id):
            return GateRecord.model_validate(store.get("gates", gate_id))
        record = store.get("references", reference_id)
        if record["status"] != "generated":
            raise GateError("Only generated references can be gated.", status=409, referenceStatus=record["status"])
        if record["streetEditId"] != bundle.streetEdit.id:
            raise GateError("Reference belongs to another edit.", status=409)
        return await _judge(gateway, store, bundle, record, gate_id, thresholds or load_thresholds(), purpose)


async def _judge(gateway, store, bundle, record, gate_id, thresholds, purpose) -> GateRecord:
    ref = ReferenceImage.model_validate(record["referenceImage"])
    det = deterministic_checks(store, bundle, record)
    frame = bundle.sourceFrame
    questions = build_questions(bundle)
    domain = [ref.imageSha256, frame["frameSha256"], sha256(bundle.streetEdit.model_dump_json().encode())]
    if any(c["answer"] == "fail" for c in det):
        # Never pay a judge for an image that already failed in code.
        now = utcnow()
        call = ModelCall(id=f"call_{uuid4().hex}", service="decisions", modelId=MODELS[Service.DECISIONS],
                         promptId=PROMPT_ID, configId=gateway.config.config_hash[:16], inputHashes=domain,
                         requestedAt=now, completedAt=now, latencyMs=0, outcome="blocked")
        checks = [GateCheck(question=f"deterministic:{c['name']}", answer=c["answer"], rationale=c["rationale"])
                  for c in det]
        decision = GateDecision(id=gate_id, subject=GateSubject(kind="reference_image", id=ref.id), modelCall=call,
                                checks=checks, outcome="reject", effect=GATE_EFFECTS["reference_image"]["reject"])
        gate = GateRecord(gateDecision=decision, state="rejected", referenceImageId=ref.id,
                          streetEditId=ref.streetEditId, attempt=ref.attempt, feedback=None,
                          thresholds={"version": thresholds["version"], "status": thresholds["status"],
                                      "sha256": thresholds["sha256"]},
                          answers=None, deterministic=det, judgeError={"code": "not_called",
                          "message": "Deterministic checks failed; the judge was not called."})
        store.put("gates", gate_id, gate)
        return gate
    ref_bytes = store.get_blob(ref.imageSha256)
    frame_bytes = store.get_blob(frame["frameSha256"])
    edit_json = bundle.streetEdit.model_dump_json(include={"id", "mayorRequest", "operations", "permissions"})
    input_value = [
        {"type": "text", "text": "SOURCE image: the original street-camera frame."},
        {"type": "image", "mime_type": frame["mimeType"], "data": base64.b64encode(frame_bytes).decode()},
        {"type": "text", "text": "EDITED image: a generated proposal of the same street after this typed edit: "
                                 + edit_json},
        {"type": "image", "mime_type": "image/png", "data": base64.b64encode(ref_bytes).decode()},
    ]
    context = CallContext(episode_id=bundle.captureId, prompt_id=PROMPT_ID, capture_id=bundle.captureId,
                          source_id=bundle.sourceId or bundle.captureId, purpose=purpose)
    requested = utcnow()
    answers, judge_error = None, None
    try:
        result = await gateway.judge(stage="image_gate", input_value=input_value, questions=questions, context=context)
        call = from_result(gateway, Service.DECISIONS, result, prompt_id=PROMPT_ID, domain_hashes=domain,
                           requested_at=requested, completed_at=utcnow())
        answers = result.output
    except GatewayError as error:
        call = from_error(gateway, Service.DECISIONS, error, prompt_id=PROMPT_ID, domain_hashes=domain,
                          requested_at=requested, completed_at=utcnow())
        judge_error = error.public()
    checks = [GateCheck(question=f"deterministic:{c['name']}", answer=c["answer"], rationale=c["rationale"])
              for c in det]
    failed_visual = []
    for name, question in questions.items():
        if answers is None:
            checks.append(GateCheck(question=question["instructions"], answer="unknown",
                                    rationale=f"{name}: judge unavailable ({judge_error['code']})"))
            continue
        answer, p, why = classify(answers[name], thresholds["questions"][name])
        if answer == "fail":
            failed_visual.append(name)
        checks.append(GateCheck(question=question["instructions"], answer=answer, score=p, rationale=f"{name}: {why}"))
    outcome = "reject" if any(c.answer == "fail" for c in checks) else \
        "unknown" if any(c.answer == "unknown" for c in checks) else "accept"
    decision = GateDecision(id=gate_id, subject=GateSubject(kind="reference_image", id=ref.id), modelCall=call,
                            checks=checks, outcome=outcome, effect=GATE_EFFECTS["reference_image"][outcome])
    det_failed = any(c["answer"] == "fail" for c in det)
    remaining = MAX_ATTEMPTS - len(attempts(store, ref.streetEditId, ref.style))
    feedback = None
    if outcome == "accept":
        state = "accept"
    elif outcome == "unknown":
        state = "insufficient_evidence"
    elif not det_failed and set(failed_visual) <= REPAIRABLE and remaining > 0:
        state = "re_edit"
        feedback = "; ".join(FEEDBACK[n] for n in failed_visual)
    else:
        state = "rejected"
    gate = GateRecord(gateDecision=decision, state=state, referenceImageId=ref.id, streetEditId=ref.streetEditId,
                      attempt=ref.attempt, feedback=feedback,
                      thresholds={"version": thresholds["version"], "status": thresholds["status"],
                                  "sha256": thresholds["sha256"]},
                      answers=answers, deterministic=det, judgeError=judge_error)
    store.put("gates", gate_id, gate)
    return gate


async def run_gated_reference(gateway: ModelGateway, store: ArtifactStore, bundle: EditBundle, *, style: str,
                              allow_experimental: bool = False,
                              purpose: str = "development") -> dict:
    """Generate -> gate -> (bounded re-edit) until accept, reject, unknown or exhaustion.

    Returns every attempt and gate, including failed and rejected ones.
    """
    history = []
    feedback = None
    while True:
        try:
            attempt = await generate_reference(gateway, store, bundle, style=style, feedback=feedback,
                                               allow_experimental=allow_experimental, purpose=purpose)
        except ArtifactError as exc:
            if exc.status == 409 and history:  # attempts exhausted
                return {"state": "rejected", "reason": "attempts exhausted", "history": history}
            raise
        entry = {"attempt": attempt.model_dump(mode="json"), "gate": None}
        history.append(entry)
        if attempt.status != "generated":
            # A failed or malformed image is surfaced; never replaced by another camera or cached image.
            if attempt.attempt >= MAX_ATTEMPTS:
                return {"state": "rejected", "reason": f"image generation {attempt.status}", "history": history}
            continue
        gate = await gate_reference(gateway, store, bundle, attempt.id, purpose=purpose)
        entry["gate"] = gate.model_dump(mode="json")
        if gate.state != "re_edit":
            return {"state": gate.state, "referenceImageId": attempt.id, "gateDecisionId": gate.gateDecision.id,
                    "history": history}
        feedback = gate.feedback


# ---------------------------------------------------------------- world binding

def authorize_world(store: ArtifactStore, reference_id: str, gate_id: str) -> dict:
    """Everything Genie creation (#9) needs, only for an accepted, hash-verified, unbound gate."""
    if not store.exists("gates", gate_id):
        raise GateError("Unknown gate decision.", status=404, gateDecisionId=gate_id)
    gate = GateRecord.model_validate(store.get("gates", gate_id))
    decision = gate.gateDecision
    if decision.subject.kind != "reference_image" or decision.subject.id != reference_id:
        raise GateError("This gate did not judge that reference image.", status=409)
    if decision.outcome != "accept" or decision.effect != "allow_world_creation":
        raise GateError("World creation is blocked: the reference gate did not accept this image.", status=409,
                        outcome=decision.outcome, state=gate.state)
    if store.exists("bindings", "claim_" + gate_id.removeprefix("gate_")):
        raise GateError("This accepted gate has already been used for a world session.", status=409)
    record = store.get("references", reference_id)
    ref = ReferenceImage.model_validate(record["referenceImage"])
    if ref.imageSha256 not in decision.modelCall.inputHashes:
        raise GateError("The gate did not hash this image.", status=409)
    data = store.get_blob(ref.imageSha256)  # raises if bytes changed
    validate_transport(data, record["genieTransport"]["mimeType"])
    bundle = EditBundle.model_validate(store.get("edits", ref.streetEditId))
    return {"referenceImageId": ref.id, "gateDecisionId": gate_id, "imageSha256": ref.imageSha256,
            "worldPrompt": bundle.worldPrompt,
            "referenceImage": {"mime_type": record["genieTransport"]["mimeType"],
                               "data": base64.b64encode(data).decode()}}


def claim_world(store: ArtifactStore, reference_id: str, gate_id: str) -> dict:
    """Authorize and atomically claim an accepted gate. Call immediately before creating the Genie session.

    Each accepted gate admits exactly one session, so a concurrent second creation fails here.
    """
    auth = authorize_world(store, reference_id, gate_id)
    try:
        store.claim("bindings", "claim_" + gate_id.removeprefix("gate_"),
                    {"gateDecisionId": gate_id, "referenceImageId": reference_id,
                     "imageSha256": auth["imageSha256"], "claimedAt": utcnow().isoformat()})
    except ArtifactError:
        raise GateError("This accepted gate has already been used for a world session.", status=409) from None
    return auth


def bind_world(store: ArtifactStore, world: WorldSession, image_sha256: str) -> dict:
    """Record which session the claimed gate created. Write-once."""
    return bind_world_ids(store, gate_id=world.gateDecisionId, reference_id=world.referenceImageId,
                          world_session_id=world.id, image_sha256=image_sha256)


def bind_world_ids(store: ArtifactStore, *, gate_id: str, reference_id: str, world_session_id: str,
                   image_sha256: str) -> dict:
    suffix = gate_id.removeprefix("gate_")
    if not store.exists("bindings", "claim_" + suffix):
        raise GateError("World session was created without claiming an accepted gate.", status=409)
    claim = store.get("bindings", "claim_" + suffix)
    if claim["referenceImageId"] != reference_id or claim["imageSha256"] != image_sha256:
        raise GateError("The session was created from different image bytes than the gate accepted.", status=409)
    binding = {"id": "bind_" + suffix, "gateDecisionId": gate_id,
               "referenceImageId": reference_id, "imageSha256": image_sha256,
               "worldSessionId": world_session_id, "boundAt": utcnow().isoformat()}
    return store.put("bindings", binding["id"], binding)


def check_world_session(store: ArtifactStore, world: WorldSession) -> dict:
    binding_id = "bind_" + world.gateDecisionId.removeprefix("gate_")
    if not store.exists("bindings", binding_id):
        raise GateError("World session has no accepted-gate binding.", status=409)
    binding = store.get("bindings", binding_id)
    if binding["worldSessionId"] != world.id or binding["referenceImageId"] != world.referenceImageId:
        raise GateError("World session does not match its gate binding.", status=409)
    return binding


# ---------------------------------------------------------------- Genie runtime handoff (#9)

def gate_artifact(store: ArtifactStore, gate_id: str) -> bytes:
    """The exact stored gate record bytes; their hash is what the world runtime pins."""
    return store._path("gates", gate_id).read_bytes()


def publish_accepted(store: ArtifactStore, references, reference_id: str, gate_id: str) -> dict:
    """Hand an accepted, hash-verified, unclaimed gate to the Genie runtime.

    Publishing spends nothing and does not claim the gate: ``WorldManager.create`` claims it
    when the session is actually created, so a gate still admits exactly one session.
    """
    from .worlds.contracts import AcceptedReference  # worlds depends on the gateway, not on gates

    auth = authorize_world(store, reference_id, gate_id)
    reference = ReferenceImage.model_validate(store.get("references", reference_id)["referenceImage"])
    capture_id = reference.captureId
    bundle = EditBundle.model_validate(store.get("edits", reference.streetEditId))
    source_id = bundle.sourceId or capture_id
    artifact = gate_artifact(store, gate_id)
    record = AcceptedReference(
        episode_id=capture_id, source_id=source_id, capture_id=capture_id, reference_id=reference_id,
        reference_sha256=auth["imageSha256"].removeprefix("sha256:"),
        mime_type=auth["referenceImage"]["mime_type"], gate_decision_id=gate_id,
        gate_artifact_sha256=sha256(artifact).removeprefix("sha256:"), decision="accept")
    fingerprint = references.publish(record, base64.b64decode(auth["referenceImage"]["data"]), artifact)
    return {"acceptedReference": fingerprint, "worldPrompt": auth["worldPrompt"],
            "imageSha256": auth["imageSha256"], "gateDecisionId": gate_id, "referenceImageId": reference_id,
            "context": {"episode_id": capture_id, "source_id": source_id, "capture_id": capture_id}}

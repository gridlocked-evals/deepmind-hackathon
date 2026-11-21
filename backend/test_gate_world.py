"""End to end #8 -> #9: only an accepted gate creates a Genie session, bound by hash to exactly one session."""
import asyncio
import base64

import httpx
import pytest

from backend.artifacts import ArtifactError
from backend.gateway import CallContext, GatewayError
from backend.gateway.contracts import canonical, digest
from backend.gates import claim_world, gate_artifact, publish_accepted, run_gated_reference
from backend.test_pipeline_helpers import FakeProvider, answers, png, run, setup
from backend.test_worlds import finish, harness, start
from backend.worlds.contracts import AcceptedReference, WorldRequest


def gated(tmp_path, decision):
    provider = FakeProvider()
    store, bundle = setup(tmp_path / "pipe", provider)
    provider.images, provider.decisions = [png()], [decision]
    result = run(tmp_path / "pipe", provider, lambda gw: run_gated_reference(gw, store, bundle, style="photoreal"))
    attempt = result["history"][0]
    return store, attempt["attempt"]["id"], attempt["gate"]["gateDecision"]["id"], result["state"]


def request_for(handoff):
    return WorldRequest(accepted_reference=handoff["acceptedReference"], prompt=handoff["worldPrompt"], seconds=2,
                        context=CallContext(**handoff["context"], purpose="demo", prompt_id="world-v1"))


def test_accepted_gate_creates_one_session_from_the_judged_bytes(tmp_path):
    store, ref_id, gate_id, state = gated(tmp_path, answers())
    assert state == "accept"

    async def go():
        async with harness(tmp_path / "world") as (manager, stream, requests):
            manager.gates = store
            handoff = publish_accepted(store, manager.references, ref_id, gate_id)
            session = manager.create(request_for(handoff))
            binding = store.get("bindings", "bind_" + gate_id.removeprefix("gate_"))
            assert binding["worldSessionId"] == "world_" + session.id
            assert binding["imageSha256"] == handoff["imageSha256"]
            # A second session from the same accepted gate is refused before any request.
            with pytest.raises(GatewayError):
                manager.create(request_for(handoff))
            with pytest.raises(ArtifactError):
                publish_accepted(store, manager.references, ref_id, gate_id)
            await start(session)
            sent = base64.b64decode(stream.sent[0]["reference_image"]["data"])
            assert "sha256:" + digest(sent) == handoff["imageSha256"]
            assert session.provenance["gate_decision_id"] == gate_id
            await session.stop()
            await finish(session)
    asyncio.run(go())


@pytest.mark.parametrize("decision, expected", [
    (answers(requested_change=(0.1, 0.9), anchors_visible=(0.1, 0.9)), "rejected"),
    (answers(requested_change=(0.5, 0.2)), "insufficient_evidence"),
    (httpx.ReadTimeout("slow"), "insufficient_evidence"),
])
def test_rejected_or_unknown_gate_never_reaches_genie(tmp_path, decision, expected):
    store, ref_id, gate_id, _ = gated(tmp_path, decision)
    assert store.get("gates", gate_id)["state"] in (expected, "re_edit")

    async def go():
        async with harness(tmp_path / "world") as (manager, _, requests):
            manager.gates = store
            with pytest.raises(ArtifactError) as refused:
                publish_accepted(store, manager.references, ref_id, gate_id)
            assert refused.value.status == 409
            # Even a hand-forged "accept" handoff for this gate is refused at creation.
            image = store.get_blob(store.get("references", ref_id)["referenceImage"]["imageSha256"])
            artifact = gate_artifact(store, gate_id)
            capture = store.get("references", ref_id)["referenceImage"]["captureId"]
            record = AcceptedReference(episode_id=capture, source_id=capture, capture_id=capture,
                                       reference_id=ref_id, reference_sha256=digest(image), mime_type="image/png",
                                       gate_decision_id=gate_id, gate_artifact_sha256=digest(artifact),
                                       decision="accept")
            fingerprint = manager.references.publish(record, image, artifact)
            with pytest.raises(GatewayError):
                manager.create(request_for({"acceptedReference": fingerprint, "worldPrompt": "street",
                                            "context": {"episode_id": capture, "source_id": capture,
                                                        "capture_id": capture}}))
            assert not requests and not manager.sessions
    asyncio.run(go())


def test_gate_claimed_elsewhere_or_changed_blocks_creation(tmp_path):
    store, ref_id, gate_id, _ = gated(tmp_path, answers())

    async def go():
        async with harness(tmp_path / "world") as (manager, _, requests):
            manager.gates = store
            handoff = publish_accepted(store, manager.references, ref_id, gate_id)
            claim_world(store, ref_id, gate_id)  # another worker created a world from this gate first
            with pytest.raises(GatewayError):
                manager.create(request_for(handoff))
            # Losing the race after the pre-check also stops the session before it can reach Genie.
            manager._check_gate = lambda record: None
            with pytest.raises(GatewayError):
                manager.create(request_for(handoff))
            assert not manager.sessions and not requests
    asyncio.run(go())


def test_world_api_publishes_only_with_operator_token(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.worlds.api import router

    store, ref_id, gate_id, _ = gated(tmp_path, answers())
    monkeypatch.setenv("WORLD_API_TOKEN", "t" * 32)

    class Worlds:
        from backend.worlds.evidence import ReferenceStore
        references = ReferenceStore(tmp_path / "world")

    app = FastAPI()
    app.include_router(router)
    app.state.artifacts, app.state.worlds = store, Worlds()
    body = {"referenceImageId": ref_id, "gateDecisionId": gate_id}
    with TestClient(app) as client:
        assert client.post("/api/worlds/accepted-references", json=body).status_code == 401
        ok = client.post("/api/worlds/accepted-references", json=body, headers={"Authorization": "Bearer " + "t" * 32})
        assert ok.status_code == 200 and len(ok.json()["acceptedReference"]) == 64
        missing = client.post("/api/worlds/accepted-references", headers={"Authorization": "Bearer " + "t" * 32},
                              json={"referenceImageId": ref_id, "gateDecisionId": "gate_missing-0001"})
        assert missing.status_code == 404

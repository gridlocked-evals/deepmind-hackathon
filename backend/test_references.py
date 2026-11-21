import asyncio
import base64
from io import BytesIO
import json

import httpx
import pytest
from PIL import Image

from backend.artifacts import ArtifactStore
from backend.references import (MAX_ATTEMPTS, ReferenceError, generate_reference, genie_payload, normalize,
                                validate_transport)
from backend.test_pipeline_helpers import FakeProvider, carbon_ok, gateway, png, run, setup


def test_normalization_produces_exact_16_9_and_transport_checks():
    data, raw, how = normalize(png((1376, 768)))
    assert raw["width"] == 1376 and how["crop"] == [8, 1, 1360, 765]
    assert Image.open(BytesIO(data)).size == (1280, 720)
    assert validate_transport(data, "image/png")["bytes"] == len(data)
    with pytest.raises(ReferenceError):
        validate_transport(png((1376, 768)), "image/png")
    with pytest.raises(ReferenceError):
        validate_transport(data, "image/jpeg")
    with pytest.raises(ReferenceError):
        normalize(png((1024, 1024)))


def test_two_supported_styles_link_to_the_same_source_and_edit(tmp_path):
    provider = FakeProvider()
    store, bundle = setup(tmp_path, provider)
    provider.images = [png(color=(10, 200, 10)), png(color=(200, 10, 10))]

    async def both(gw):
        return [await generate_reference(gw, store, bundle, style=s) for s in ("photoreal", "cel_shaded")]
    photo, cel = run(tmp_path, provider, both)
    for rec in (photo, cel):
        ref = rec.referenceImage
        assert rec.status == "generated" and (ref.width, ref.height) == (1280, 720)
        assert ref.captureId == bundle.captureId and ref.streetEditId == bundle.streetEdit.id
        assert bundle.sourceFrame["frameSha256"] in ref.modelCall.inputHashes
        assert ref.modelCall.service == "nano_banana" and ref.modelCall.costUsd is not None
        assert rec.raw["width"] == 1376 and rec.interpretations
        assert store.get_blob(ref.imageSha256) and store.get_blob(rec.raw["sha256"])
        assert ref.attempt == 1
    assert photo.referenceImage.imageSha256 != cel.referenceImage.imageSha256
    sent = [b for m, b in provider.requests if m == "gemini-nano-banana-2.1"]
    assert sent[0]["input"][1]["type"] == "image" and "Photorealistic" in sent[0]["input"][0]["text"]
    assert "cel-shaded" in sent[1]["input"][0]["text"]
    assert b"data" not in base64.b64decode(genie_payload(store, store.get("references", photo.id))["data"])[:4]
    assert provider.count("gemini-4-genie") == 0  # this stage never creates worlds


def test_experimental_styles_need_explicit_opt_in(tmp_path):
    provider = FakeProvider()
    store, bundle = setup(tmp_path, provider)
    provider.images = [png()]
    with pytest.raises(ReferenceError):
        run(tmp_path, provider, lambda gw: generate_reference(gw, store, bundle, style="claymation"))
    rec = run(tmp_path, provider, lambda gw: generate_reference(gw, store, bundle, style="claymation",
                                                                allow_experimental=True))
    assert rec.styleStatus == "experimental" and rec.status == "generated"


def test_malformed_and_failed_outputs_are_recorded_not_substituted(tmp_path):
    provider = FakeProvider()
    store, bundle = setup(tmp_path, provider)
    provider.images = [png((1024, 1024)), 400, png()]

    async def three(gw):
        return [await generate_reference(gw, store, bundle, style="photoreal") for _ in range(3)]
    square, failed, ok = run(tmp_path, provider, three)
    assert square.status == "malformed" and square.referenceImage is None and "16:9" in square.error["error"]
    assert square.modelCall["outcome"] == "ok" and square.modelCall["costUsd"] is not None
    assert failed.status == "failed" and failed.modelCall["outcome"] == "blocked" and failed.error["code"] == "invalid_input"
    assert ok.status == "generated" and [square.attempt, failed.attempt, ok.attempt] == [1, 2, 3]
    with pytest.raises(ReferenceError) as exhausted:
        run(tmp_path, provider, lambda gw: generate_reference(gw, store, bundle, style="photoreal"))
    assert exhausted.value.status == 409 and len(store.list("references")) == MAX_ATTEMPTS


def test_cancelled_dispatches_are_recorded_and_exhaust_attempts_across_restart(tmp_path):
    store, bundle = setup(tmp_path, FakeProvider())

    async def exercise():
        entered = asyncio.Event()
        dispatched = []

        async def blocked(request):
            dispatched.append(request)
            entered.set()
            await asyncio.Event().wait()

        async with httpx.AsyncClient(transport=httpx.MockTransport(blocked)) as http:
            gw = gateway(tmp_path, http)
            for number in range(1, MAX_ATTEMPTS + 1):
                entered.clear()
                task = asyncio.create_task(generate_reference(gw, store, bundle, style="photoreal"))
                await asyncio.wait_for(entered.wait(), 1)
                task.cancel("private cancellation detail")
                with pytest.raises(asyncio.CancelledError) as cancelled:
                    await task
                assert task.cancelled() and cancelled.value.args == ("private cancellation detail",)
                record = next(r for r in store.list("references") if r["attempt"] == number)
                request_id = cancelled.value.request_id
                ledger = gw.ledger.get(request_id)
                assert record["status"] == "failed" and record["referenceImage"] is None
                assert record["error"]["code"] == "cancelled" and record["error"]["request_id"] == request_id
                assert record["modelCall"]["requestId"] == f"gateway:{request_id}"
                assert record["modelCall"]["outcome"] == "error"
                assert record["modelCall"]["costUsd"] is None and record["modelCall"]["inputTokens"] is None
                assert ledger["outcome"] == "cancelled" and ledger["charged_tokens"] > 0 and ledger["usage"] is None
                assert "private cancellation detail" not in json.dumps(record)

            # The cap must survive new gateway/store instances, not just this process's lock state.
            with pytest.raises(ReferenceError) as exhausted:
                await generate_reference(gateway(tmp_path, http), ArtifactStore(store.root), bundle, style="photoreal")
            assert exhausted.value.status == 409 and len(dispatched) == MAX_ATTEMPTS

    asyncio.run(exercise())


def test_cancel_before_gateway_admission_records_no_invented_request_id(tmp_path):
    store, bundle = setup(tmp_path, FakeProvider())
    dispatched = []

    def unexpected(request):
        dispatched.append(request)
        raise AssertionError("cancelled before admission: no provider request should be made")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
            gw = gateway(tmp_path, http)
            gw.slots = asyncio.Semaphore(0)
            task = asyncio.create_task(generate_reference(gw, store, bundle, style="photoreal"))
            await asyncio.sleep(0)  # let the call reach the unavailable gateway slot
            task.cancel()
            with pytest.raises(asyncio.CancelledError) as cancelled:
                await task
            assert getattr(cancelled.value, "request_id", None) is None
            assert not [r for r in gw.ledger.attempts() if r["stage"] == "reference"]
            [record] = store.list("references")
            assert record["attempt"] == 1 and record["status"] == "failed"
            assert record["error"]["code"] == "cancelled" and record["error"]["request_id"] is None
            assert record["modelCall"]["requestId"] is None and record["modelCall"]["costUsd"] is None

    asyncio.run(exercise())
    assert dispatched == []


def test_unchanged_source_frame_is_malformed(tmp_path):
    provider = FakeProvider()
    store, bundle = setup(tmp_path, provider)
    provider.images = [store.get_blob(bundle.sourceFrame["frameSha256"])]
    rec = run(tmp_path, provider, lambda gw: generate_reference(gw, store, bundle, style="photoreal"))
    assert rec.status == "malformed" and "unchanged" in rec.error["error"]


def test_rejected_edits_cannot_be_rendered(tmp_path):
    provider = FakeProvider()
    store, bundle = setup(tmp_path, provider, carbon_ok(preservedAnchorIds=[]))
    assert bundle.streetEdit.status == "rejected"
    with pytest.raises(ReferenceError):
        run(tmp_path, provider, lambda gw: generate_reference(gw, store, bundle, style="photoreal"))

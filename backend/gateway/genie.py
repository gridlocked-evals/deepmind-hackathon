"""A bounded real Genie probe, separate from JSON inference and the #9 player."""
import asyncio
import base64
import hashlib
from importlib.metadata import PackageNotFoundError, version

from .core import GatewayError, MODELS, _identifier


async def _shielded_cleanup(operation):
    """Finish remote cleanup even when the probe deadline/caller cancels."""
    task = asyncio.create_task(operation)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=5)
    except asyncio.CancelledError:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        raise
    except BaseException:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise


async def smoke_genie(config, payload, record, persist, client_factory=None):
    prompt = payload.get("world_prompt")
    if (set(payload) - {"world_prompt", "reference_image"} or not isinstance(prompt, dict)
            or not isinstance(prompt.get("text"), str) or not prompt["text"].strip()):
        raise GatewayError("invalid_input")
    if client_factory is None:
        try:
            from google import genai
            major, minor, *_ = map(int, version("google-genai").split("."))
            if (major, minor) < (3, 2):
                raise GatewayError("sdk_unavailable")
            client_factory = genai.Client
        except (ImportError, PackageNotFoundError, ValueError):
            raise GatewayError("sdk_unavailable") from None
    client = client_factory(api_key=config.api_key, http_options={"api_version": "v1beta", "timeout": int(config.timeout_seconds * 1000)})
    interactions = getattr(client.aio, "interactions", None)
    if interactions is None or not hasattr(interactions, "connect"):
        await client.aio.aclose()
        raise GatewayError("sdk_unavailable")
    result = {"completed": False, "frameSha256": None, "usage": {}}
    interaction_id = None
    try:
        try:
            async with asyncio.timeout(config.timeout_seconds):
                interaction = await interactions.create(model=MODELS["genie"], api_version="v1beta")
                if not _identifier(interaction.id) or config.api_key in interaction.id:
                    raise GatewayError("invalid_output")
                interaction_id = interaction.id
                record["providerRequestId"] = interaction_id
                persist(record)  # Keep remote identity even if connect/seeding fails.
                async with interactions.connect(interaction_id, api_version="v1beta") as session:
                    receiver = session.receive().__aiter__()
                    try:
                        await session.send({"event_type": "world_setup", **payload})
                        async for event in receiver:
                            if event.event_type == "error":
                                raise GatewayError("unavailable")
                            if event.event_type == "step.delta" and getattr(event.delta, "type", None) == "video" and event.delta.data:
                                frame = base64.b64decode(event.delta.data, validate=True)
                                if len(frame) < 3 or not frame.startswith(b"\xff\xd8\xff"):
                                    raise GatewayError("invalid_output")
                                result["frameSha256"] = hashlib.sha256(frame).hexdigest()
                                break
                            if event.event_type == "interaction.completed":
                                result["completed"] = True
                                raise GatewayError("invalid_output")
                    finally:
                        if not result["completed"]:
                            async def terminate():
                                await session.complete()
                                async for event in receiver:
                                    if event.event_type == "interaction.completed":
                                        result["completed"] = True
                                        usage = getattr(event, "usage", None)
                                        if usage is not None:
                                            result["usage"] = usage.model_dump() if hasattr(usage, "model_dump") else usage
                                        return
                                raise GatewayError("termination_unconfirmed")
                            await _shielded_cleanup(terminate())
            return result, interaction_id
        except BaseException:
            # Includes connect failures, deadlines and caller cancellation. SDK 3.6
            # exposes the REST interactions.cancel(id), independent of the socket.
            if interaction_id and not result["completed"]:
                async def cancel_remote():
                    cancelled = await interactions.cancel(interaction_id, api_version="v1beta")
                    if getattr(cancelled, "status", None) not in {"cancelled", "completed"}:
                        raise GatewayError("termination_unconfirmed")
                    record["remoteTermination"] = cancelled.status
                    persist(record)
                try:
                    await _shielded_cleanup(cancel_remote())
                except BaseException:
                    record["remoteTermination"] = "unconfirmed"
                    persist(record)
                    raise GatewayError("termination_unconfirmed") from None
            raise
    finally:
        await client.aio.aclose()

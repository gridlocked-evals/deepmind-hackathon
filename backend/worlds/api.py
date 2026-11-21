"""Authenticated creation and session-scoped browser relay; no project key leaves Python."""
import asyncio
import json
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

from ..gateway.contracts import GatewayError
from .contracts import WorldRequest

router = APIRouter(prefix="/api/worlds")
ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"}


def authorize(request: Request):
    configured = os.getenv("WORLD_API_TOKEN", "")
    supplied = request.headers.get("authorization", "")
    if len(configured) < 32:
        raise HTTPException(503, "World creation requires a backend WORLD_API_TOKEN of at least 32 characters.")
    if not secrets.compare_digest(supplied, "Bearer " + configured):
        raise HTTPException(401, "World API authentication required.")
    origin = request.headers.get("origin")
    if origin and origin not in ORIGINS:
        raise HTTPException(403, "Origin is not allowed.")


@router.post("", dependencies=[Depends(authorize)])
async def create_world(body: WorldRequest, request: Request):
    try:
        session = request.app.state.worlds.create(body)
    except GatewayError as exc:
        raise HTTPException(409, exc.public()) from None
    return {**session.status(), "connect_tokens": dict(session.tokens),
            "connect_within_seconds": session.limits.attach_seconds,
            "websocket_path": f"/api/worlds/{session.id}/stream"}


@router.get("/{session_id}", dependencies=[Depends(authorize)])
async def world_status(session_id: str, request: Request):
    session = request.app.state.worlds.sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Unknown active session; finished evidence is retained on disk.")
    return session.status()


@router.post("/{session_id}/stop", dependencies=[Depends(authorize)])
async def stop_world(session_id: str, request: Request):
    session = request.app.state.worlds.sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Unknown active session.")
    await session.stop("operator_stop")
    return session.status()


@router.websocket("/{session_id}/stream")
async def stream_world(websocket: WebSocket, session_id: str):
    # Reject cross-site browser connections even with a leaked session capability.
    origin = websocket.headers.get("origin")
    session = websocket.app.state.worlds.sessions.get(session_id)
    if not session or (origin and origin not in ORIGINS):
        await websocket.close(code=1008)
        return
    role = None
    authenticated = False
    sender = None
    await websocket.accept()
    try:
        # Tokens stay out of URLs/access logs and are consumed on first use.
        async with asyncio.timeout(5):
            first = await websocket.receive_text()
        if len(first) > 1024:
            raise ValueError()
        auth = json.loads(first)
        if not isinstance(auth, dict) or set(auth) != {"role", "token"} or not isinstance(auth["token"], str):
            raise ValueError()
        role = auth["role"]
        if role not in ("human", "defender", "adversary"):
            raise ValueError()
        await session.attach(role, auth["token"])
        authenticated = True
        await websocket.send_json({"kind": "status", **session.status()})

        async def send_frames():
            while True:
                item = await session.queues[role].get()
                async with asyncio.timeout(session.limits.idle_seconds):
                    await websocket.send_json(item)
                if item["kind"] == "status" and session.finished.is_set():
                    return

        async def commands():
            while not session.finished.is_set():
                raw = await websocket.receive_text()
                if len(raw) > 8192:
                    raise ValueError()
                await session.command(json.loads(raw), role)

        sender = asyncio.create_task(send_frames())
        receiver = asyncio.create_task(commands())
        try:
            done, _ = await asyncio.wait((sender, receiver), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        finally:
            sender.cancel()
            receiver.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    except (WebSocketDisconnect, GatewayError, ValueError, TimeoutError):
        pass
    finally:
        if authenticated:
            await session.disconnected(role)
        try:
            await websocket.close()
        except RuntimeError:
            pass

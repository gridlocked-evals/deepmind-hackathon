"""Bounded HLS transport. Clients choose camera IDs, never upstream URLs."""
import asyncio
from collections import OrderedDict
import re
import secrets
import time
from urllib.parse import unquote, urljoin, urlsplit

import anyio
import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from .feeds import HLS_SOURCES, SOURCE_HOST, record_fetch

MAX_PLAYLIST = 1024 * 1024
MAX_MEDIA = 16 * 1024 * 1024
MAX_TOKENS = 4096
TOKEN_TTL = 900
UPSTREAM_CLOSE_SECONDS = 1
_tokens: OrderedDict[str, tuple[str, str, float]] = OrderedDict()
_by_url: dict[tuple[str, str], str] = {}
_slots = asyncio.Semaphore(8)
_uri_attribute = re.compile(r'URI="([^"]*)"')


class _ClosingStreamingResponse(StreamingResponse):
    def __init__(self, content, *, finalize, **kwargs):
        super().__init__(content, **kwargs)
        self.finalize = finalize

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Headers can fail, or disconnect can cancel streaming before the
            # body generator is entered. Its finally alone cannot own cleanup.
            await self.finalize()


def validate_url(camera_id: str, url: str) -> str:
    source = HLS_SOURCES.get(camera_id)
    if not source:
        raise HTTPException(404, "Unknown camera source.")
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname == SOURCE_HOST
                 and parsed.port in (None, 443) and not parsed.username
                 and not parsed.password and not parsed.fragment)
    except ValueError:
        valid = False
        parsed = None
    if not valid or parsed is None:
        raise HTTPException(502, "The camera supplied an unsupported media location.")
    path = parsed.path
    for _ in range(3):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    if (not path.startswith(source["_path"]) or "\\" in path or "%" in path
            or any(part in {".", ".."} for part in path.split("/"))
            or any(ord(char) < 32 for char in url)):
        raise HTTPException(502, "The camera supplied an unsupported media path.")
    return url


def resource_token(camera_id: str, url: str) -> str:
    validate_url(camera_id, url)
    now = time.monotonic()
    existing = _by_url.get((camera_id, url))
    if existing and existing in _tokens:
        _tokens[existing] = (camera_id, url, now + TOKEN_TTL)
        _tokens.move_to_end(existing)
        return existing
    while len(_tokens) >= MAX_TOKENS:
        old, (old_camera, old_url, _) = _tokens.popitem(last=False)
        if _by_url.get((old_camera, old_url)) == old:
            _by_url.pop((old_camera, old_url), None)
    token = secrets.token_urlsafe(24)
    _tokens[token] = (camera_id, url, now + TOKEN_TTL)
    _by_url[(camera_id, url)] = token
    return token


def resolve_token(camera_id: str, token: str) -> str:
    entry = _tokens.get(token)
    now = time.monotonic()
    if not entry or entry[0] != camera_id or entry[2] <= now:
        raise HTTPException(404, "This media reference expired. Reconnect to the camera.")
    _tokens[token] = (entry[0], entry[1], now + TOKEN_TTL)
    _tokens.move_to_end(token)
    return validate_url(camera_id, entry[1])


def rewrite_playlist(camera_id: str, upstream_url: str, content: str, prefix: str = "") -> str:
    if not content.lstrip("\ufeff \r\n").startswith("#EXTM3U"):
        raise HTTPException(502, "The camera did not return an HLS playlist.")

    def replace_uri(uri: str) -> str:
        target = validate_url(camera_id, urljoin(upstream_url, uri.strip()))
        token = resource_token(camera_id, target)
        return f"{prefix}/api/feeds/{camera_id}/resource/{token}"

    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            line = replace_uri(stripped)
        elif "URI=" in line:
            # HLS attributes use quoted URI values; reject an unhandled location.
            line, count = _uri_attribute.subn(lambda match: f'URI="{replace_uri(match[1])}"', line)
            if not count:
                raise HTTPException(502, "The camera supplied an unsupported playlist attribute.")
        lines.append(line)
    return "\n".join(lines) + "\n"


def range_header(value: str | None) -> dict[str, str]:
    if value is None:
        return {}
    if not re.fullmatch(r"bytes=(?:\d+-\d*|-\d+)", value):
        raise HTTPException(416, "Only a single byte range is supported.")
    start, end = value[6:].split("-")
    if (start and end and (int(end) < int(start) or int(end) - int(start) >= MAX_MEDIA)) or (not start and int(end) > MAX_MEDIA):
        raise HTTPException(416, "The requested media range is too large.")
    return {"Range": value}


async def open_upstream(client: httpx.AsyncClient, camera_id: str, url: str, headers: dict) -> tuple[httpx.Response, str]:
    for redirect in range(3):
        validate_url(camera_id, url)
        response = await client.send(client.build_request("GET", url, headers=headers), stream=True, follow_redirects=False)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location")
            await response.aclose()
            if redirect == 2 or not location:
                raise HTTPException(502, "The camera redirected too many times.")
            url = validate_url(camera_id, urljoin(url, location))
            continue
        if response.status_code not in {200, 206}:
            status = response.status_code
            await response.aclose()
            raise HTTPException(502, f"The camera source returned HTTP {status}.")
        return response, url
    raise HTTPException(502, "The camera could not be reached.")


async def proxy_media(request: Request, camera_id: str, url: str) -> Response:
    validate_url(camera_id, url)
    headers = {"Accept-Encoding": "identity", **range_header(request.headers.get("range"))}
    slots = _slots
    try:
        await asyncio.wait_for(slots.acquire(), timeout=1)
    except TimeoutError as exc:
        raise HTTPException(503, "Camera transport is busy. Try again.") from exc
    response: httpx.Response | None = None
    streaming = False
    finalized = False

    async def finalize():
        nonlocal finalized
        if finalized:
            return
        finalized = True
        try:
            if response is not None:
                # Close in a separate task and wait it out, but never hold a slot indefinitely.
                # The shield stops AnyIO scope cancellation (Starlette's disconnect listener), which is
                # re-delivered on every await and would spin this loop; the catch handles a direct
                # Task.cancel(), which a shield does not stop and which is delivered once.
                close = asyncio.ensure_future(response.aclose())
                loop = asyncio.get_running_loop()
                deadline = loop.time() + UPSTREAM_CLOSE_SECONDS
                cancelled = None
                with anyio.CancelScope(shield=True):
                    while not close.done() and (left := deadline - loop.time()) > 0:
                        try:
                            await asyncio.wait([close], timeout=left)
                        except asyncio.CancelledError as exc:
                            cancelled = exc
                if not close.done():
                    # Hard deadline: unlike wait_for, never wait for the close to honour cancellation.
                    close.cancel()
                    close.add_done_callback(lambda task: task.cancelled() or task.exception())
                if cancelled is not None:
                    raise cancelled
                if close.done() and not close.cancelled():
                    close.result()
        finally:
            slots.release()

    try:
        response, final_url = await open_upstream(request.app.state.http, camera_id, url, headers)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        playlist = urlsplit(final_url).path.endswith(".m3u8") or content_type in {"application/vnd.apple.mpegurl", "application/x-mpegurl"}
        limit = MAX_PLAYLIST if playlist else MAX_MEDIA
        try:
            length = int(response.headers.get("content-length", "0"))
        except ValueError:
            length = 0
        if length < 0 or length > limit:
            raise HTTPException(502, "The camera response exceeded the media limit.")
        if playlist:
            if response.status_code != 200:
                raise HTTPException(502, "The camera returned an incomplete playlist.")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    raise HTTPException(502, "The camera playlist exceeded the size limit.")
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise HTTPException(502, "The camera playlist was not readable.") from exc
            body = rewrite_playlist(camera_id, final_url, text, request.scope.get("root_path", ""))
            return Response(body, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store"})
        if content_type not in {"video/mp2t", "video/mp4", "audio/mp4", "audio/aac", "application/octet-stream", "binary/octet-stream"}:
            raise HTTPException(502, "The camera returned an unsupported media response.")
        if response.headers.get("content-encoding", "identity").lower() not in {"identity", ""}:
            raise HTTPException(502, "The camera returned unexpectedly encoded media.")
        out_headers = {"Cache-Control": "private, no-store"}
        for name in ("content-range", "accept-ranges", "content-length"):
            if name in response.headers:
                out_headers[name] = response.headers[name]
        upstream = response

        async def chunks():
            count = 0
            try:
                # Encoded media was rejected above. Read its transport stream
                # directly so HTTPX does not close it outside our cleanup bound.
                async for chunk in upstream.stream:
                    count += len(chunk)
                    if count > MAX_MEDIA:
                        raise RuntimeError("Camera media exceeded the byte limit.")
                    if await request.is_disconnected():
                        return
                    yield chunk
                record_fetch(camera_id, True)
            except (httpx.HTTPError, RuntimeError):
                record_fetch(camera_id, False)
                raise
            finally:
                await finalize()

        result = _ClosingStreamingResponse(chunks(), finalize=finalize,
            status_code=response.status_code, media_type=content_type, headers=out_headers)
        streaming = True
        return result
    except HTTPException:
        record_fetch(camera_id, False)
        raise
    except httpx.HTTPError as exc:
        record_fetch(camera_id, False)
        raise HTTPException(502, "The camera could not be reached. Try reconnecting.") from exc
    finally:
        if not streaming:
            await finalize()

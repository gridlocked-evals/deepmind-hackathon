"""Write-once storage for pipeline contracts and content-addressed blobs.

Issues #6-#8 persist every Carbon edit, Nano Banana attempt and Decisions gate
here so later stages (and reviewers) can re-read exactly what was produced.
Records are never overwritten: a second write with different content raises.
Blobs (source frames, generated images) are stored by SHA-256.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from datetime import datetime, timezone

from pydantic import BaseModel

KINDS = ("observations", "edits", "references", "gates", "bindings", "frames",
         "extractions", "inspections", "reviews", "extraction_attempts", "observation_evidence")
_ID = re.compile(r"^[a-z]+_[A-Za-z0-9-]{4,64}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class ArtifactError(Exception):
    def __init__(self, message: str, status: int = 404, **detail):
        super().__init__(message)
        self.status = status
        self.detail = {"error": message, **detail}


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        for kind in KINDS:
            (self.root / kind).mkdir(parents=True, exist_ok=True)
        (self.root / "blobs").mkdir(parents=True, exist_ok=True)

    # -- records --------------------------------------------------------

    def _path(self, kind: str, record_id: str) -> Path:
        if kind not in KINDS or not _ID.match(record_id or ""):
            raise ArtifactError("Unknown artifact.", kind=kind, id=record_id)
        return self.root / kind / f"{record_id}.json"

    def put(self, kind: str, record_id: str, value: BaseModel | dict) -> dict:
        data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        body = json.dumps(data, indent=2, sort_keys=True)
        path = self._path(kind, record_id)
        if path.exists():
            if path.read_text() != body:
                raise ArtifactError("Artifacts are immutable; this id already holds different content.",
                                    status=409, kind=kind, id=record_id)
            return data
        tmp = path.with_suffix(f".tmp-{secrets.token_hex(4)}")
        tmp.write_text(body)
        try:
            os.link(tmp, path)  # atomic and exclusive: concurrent writers cannot both win
        except FileExistsError:
            if path.read_text() != body:
                raise ArtifactError("Artifacts are immutable; this id already holds different content.",
                                    status=409, kind=kind, id=record_id) from None
        finally:
            tmp.unlink()
        path.chmod(0o444)
        return data

    def claim(self, kind: str, record_id: str, value: dict) -> dict:
        """Exclusive create: raises 409 if the id exists, even with identical content."""
        path = self._path(kind, record_id)
        body = json.dumps(value, indent=2, sort_keys=True)
        try:
            with open(path, "x") as handle:
                handle.write(body)
        except FileExistsError:
            raise ArtifactError("Already claimed.", status=409, kind=kind, id=record_id) from None
        path.chmod(0o444)
        return value

    def get(self, kind: str, record_id: str) -> dict:
        path = self._path(kind, record_id)
        if not path.is_file():
            raise ArtifactError("Unknown artifact.", kind=kind, id=record_id)
        return json.loads(path.read_text())

    def exists(self, kind: str, record_id: str) -> bool:
        try:
            return self._path(kind, record_id).is_file()
        except ArtifactError:
            return False

    def list(self, kind: str) -> list[dict]:
        if kind not in KINDS:
            raise ArtifactError("Unknown artifact kind.", kind=kind)
        return [json.loads(p.read_text()) for p in sorted((self.root / kind).glob("*.json"))]

    # -- blobs ----------------------------------------------------------

    def put_blob(self, data: bytes) -> str:
        digest = sha256(data)
        path = self.root / "blobs" / digest.split(":", 1)[1]
        if not path.exists():
            tmp = path.with_suffix(f".tmp-{secrets.token_hex(4)}")
            tmp.write_bytes(data)
            tmp.chmod(0o444)
            os.replace(tmp, path)
        return digest

    def get_blob(self, digest: str) -> bytes:
        if not _SHA.match(digest or ""):
            raise ArtifactError("Unknown blob.", sha256=digest)
        path = self.root / "blobs" / digest.split(":", 1)[1]
        if not path.is_file():
            raise ArtifactError("Unknown blob.", sha256=digest)
        data = path.read_bytes()
        if sha256(data) != digest:
            raise ArtifactError("Blob no longer matches its hash.", status=500, sha256=digest)
        return data

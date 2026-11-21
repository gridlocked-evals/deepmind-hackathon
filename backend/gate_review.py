"""Blinded human checks and threshold calibration for the reference gate (issue #8).

    python -m backend.gate_review export  OUT_DIR   # blinded sheet + separate key
    python -m backend.gate_review score   OUT_DIR   # after humans fill sheet.csv
    python -m backend.gate_review calibrate OUT_DIR [--target-far 0.1] [--write PATH]

Reviewers see only source/edited image pairs and the mayor request; gate
outcomes live in key.json, which they must not open. Every gated attempt is
exported, not just attractive ones. Calibration recomputes outcomes offline
from stored judge answers, so it never calls the judge again.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random

from .artifacts import ArtifactStore
from .gates import THRESHOLDS_PATH, classify, load_thresholds
from .pipeline_api import make_artifacts

LABELS = ("acceptable", "unacceptable", "unsure")


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def export(store: ArtifactStore, out: Path, seed: int = 119) -> int:
    out.mkdir(parents=True, exist_ok=True)
    gates = store.list("gates")
    rng = random.Random(seed)
    rng.shuffle(gates)
    rows, key = [], {}
    for i, gate in enumerate(gates):
        rid = f"r{i + 1:03d}"
        ref = store.get("references", gate["referenceImageId"])
        edit = store.get("edits", gate["streetEditId"])
        frame = edit["sourceFrame"]
        ext = "png" if frame["mimeType"] == "image/png" else "jpg"
        (out / f"{rid}_source.{ext}").write_bytes(store.get_blob(frame["frameSha256"]))
        (out / f"{rid}_edited.png").write_bytes(store.get_blob(ref["referenceImage"]["imageSha256"]))
        ops = "; ".join(f"{o['op']} {o['target']}: {o['description']}" for o in edit["streetEdit"]["operations"])
        rows.append({"review_id": rid, "mayor_request": edit["streetEdit"]["mayorRequest"], "operations": ops,
                     "label": "", "notes": ""})
        key[rid] = {"gateDecisionId": gate["gateDecision"]["id"], "state": gate["state"],
                    "outcome": gate["gateDecision"]["outcome"], "style": ref["style"], "attempt": ref["attempt"]}
    with open(out / "sheet.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["review_id"])
        writer.writeheader()
        writer.writerows(rows)
    (out / "key.json").write_text(json.dumps(key, indent=2))
    (out / "INSTRUCTIONS.txt").write_text(
        "For each review_id compare *_source with *_edited. Label 'acceptable' only if the edited image keeps the\n"
        "camera view, landmarks and crosswalks and clearly shows the requested change without contradictory\n"
        "features. Otherwise 'unacceptable'; 'unsure' if you cannot tell. Do not open key.json.\n")
    return len(rows)


def _labelled(out: Path) -> list[tuple[str, dict, str]]:
    key = json.loads((out / "key.json").read_text())
    with open(out / "sheet.csv", newline="") as handle:
        rows = list(csv.DictReader(handle))
    bad = [r["review_id"] for r in rows if r["label"].strip().lower() not in LABELS]
    if bad:
        raise SystemExit(f"unlabelled or invalid rows: {', '.join(bad)}")
    return [(r["review_id"], key[r["review_id"]], r["label"].strip().lower()) for r in rows]


def metrics(pairs: list[tuple[str, str]]) -> dict:
    """pairs of (gate outcome, human label)."""
    accepted = [h for o, h in pairs if o == "accept"]
    bad = [o for o, h in pairs if h == "unacceptable"]
    good = [o for o, h in pairs if h == "acceptable"]
    fa = sum(1 for h in accepted if h == "unacceptable")
    caught = sum(1 for o in bad if o != "accept")
    return {"n": len(pairs), "gateAccepted": len(accepted),
            "falseAcceptance": {"k": fa, "n": len(accepted), "rate": round(fa / len(accepted), 4) if accepted else None,
                                "wilson95": wilson(fa, len(accepted))},
            "rejectionOrAbstentionCoverage": {"k": caught, "n": len(bad),
                                              "rate": round(caught / len(bad), 4) if bad else None,
                                              "wilson95": wilson(caught, len(bad))},
            "abstentions": sum(1 for o, _ in pairs if o == "unknown"),
            "acceptableAccepted": {"k": sum(1 for o in good if o == "accept"), "n": len(good)},
            "humanUnsure": sum(1 for _, h in pairs if h == "unsure")}


def score(out: Path) -> dict:
    return metrics([(k["outcome"], label) for _, k, label in _labelled(out)])


def _recompute(gate: dict, t: dict) -> str:
    if any(c["answer"] == "fail" for c in gate["deterministic"]) or gate["answers"] is None:
        return gate["gateDecision"]["outcome"]
    answers = [classify(a, t)[0] for a in gate["answers"].values()]
    return "reject" if "fail" in answers else "unknown" if "unknown" in answers else "accept"


def calibrate(store: ArtifactStore, out: Path, target_far: float = 0.1) -> dict:
    gates = {g["gateDecision"]["id"]: g for g in store.list("gates")}
    labelled = [(gates[k["gateDecisionId"]], label) for _, k, label in _labelled(out) if label != "unsure"]
    if not labelled:
        raise SystemExit("no labelled examples: mark at least one example acceptable or unacceptable")
    best = None
    for pass_at in (0.6, 0.7, 0.8, 0.85, 0.9, 0.95):
        for fail_at in (0.1, 0.2, 0.3, 0.4):
            for min_conf in (0.5, 0.6, 0.7, 0.8, 0.9):
                t = {"passAt": pass_at, "failAt": fail_at, "minConfidence": min_conf}
                m = metrics([(_recompute(g, t), label) for g, label in labelled])
                far = m["falseAcceptance"]["rate"] or 0.0
                rank = (far <= target_far, m["acceptableAccepted"]["k"], -far, pass_at)
                if best is None or rank > best[0]:
                    best = (rank, t, m)
    sheet = (out / "sheet.csv").read_bytes()
    return {"thresholds": best[1], "metrics": best[2], "targetFalseAcceptance": target_far,
            "developmentSet": {"sheetSha256": "sha256:" + hashlib.sha256(sheet).hexdigest(),
                               "examples": len(labelled)}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("export", "score", "calibrate"))
    parser.add_argument("out", type=Path)
    parser.add_argument("--target-far", type=float, default=0.1)
    parser.add_argument("--write", type=Path, help="write a frozen thresholds file (calibrate only)")
    args = parser.parse_args()
    store = make_artifacts()
    if args.command == "export":
        print(f"exported {export(store, args.out)} gated references to {args.out}")
    elif args.command == "score":
        print(json.dumps(score(args.out), indent=2))
    else:
        result = calibrate(store, args.out, args.target_far)
        print(json.dumps(result, indent=2))
        if args.write:
            current = load_thresholds(THRESHOLDS_PATH)
            frozen = {"version": args.write.stem, "status": "frozen",
                      "selection": f"Grid search on blinded human labels; false acceptance <= {args.target_far} "
                                   "preferred, then most acceptable images accepted.",
                      "developmentSet": result["developmentSet"],
                      "questions": {q: result["thresholds"] for q in current["questions"]}}
            if args.write.exists():
                raise SystemExit(f"{args.write} exists; frozen thresholds are never overwritten")
            args.write.write_text(json.dumps(frozen, indent=2) + "\n")
            os.chmod(args.write, 0o444)
            print(f"wrote {args.write}; point THRESHOLDS_PATH at it and commit")


if __name__ == "__main__":
    main()

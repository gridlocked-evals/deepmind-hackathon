import csv
import hashlib
import sys

import pytest

from backend import gate_review
from backend.artifacts import ArtifactStore
from backend.gates import load_thresholds, run_gated_reference
from backend.test_pipeline_helpers import FakeProvider, answers, png, run, setup


def review_sheet(tmp_path, labels):
    if labels:
        provider = FakeProvider()
        store, bundle = setup(tmp_path, provider)
        provider.images = [png(color=(i, 0, 0)) for i in range(len(labels))]
        provider.decisions = [answers(requested_change=(0.2, 0.9))] * (len(labels) - 1) + [answers()]
        run(tmp_path, provider, lambda gw: run_gated_reference(gw, store, bundle, style="photoreal"))
    else:
        store = ArtifactStore(tmp_path / "pipeline")
    out = tmp_path / "review"
    assert gate_review.export(store, out) == len(labels)
    if labels:
        with (out / "sheet.csv").open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row, label in zip(rows, labels):
            row["label"] = label
        with (out / "sheet.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return store, out


@pytest.mark.parametrize("labels", [[], ["unsure", "unsure"]], ids=["empty-export", "all-unsure"])
def test_calibration_without_decisive_labels_cannot_freeze_thresholds(tmp_path, monkeypatch, capsys, labels):
    store, out = review_sheet(tmp_path, labels)
    with pytest.raises(SystemExit, match="no labelled examples"):
        gate_review.calibrate(store, out)

    destination = tmp_path / "frozen.json"
    monkeypatch.setenv("PIPELINE_DIR", str(store.root))
    monkeypatch.setattr(sys, "argv", ["gate_review", "calibrate", str(out), "--write", str(destination)])
    with pytest.raises(SystemExit, match="no labelled examples"):
        gate_review.main()
    assert not destination.exists()
    assert capsys.readouterr().out == ""


def test_calibration_with_decisive_label_freezes_only_supported_evidence(tmp_path, monkeypatch, capsys):
    store, out = review_sheet(tmp_path, ["acceptable", "unsure"])
    result = gate_review.calibrate(store, out)
    assert result["metrics"]["n"] == result["developmentSet"]["examples"] == 1
    assert result["developmentSet"]["sheetSha256"] == "sha256:" + hashlib.sha256((out / "sheet.csv").read_bytes()).hexdigest()

    destination = tmp_path / "frozen.json"
    monkeypatch.setenv("PIPELINE_DIR", str(store.root))
    monkeypatch.setattr(sys, "argv", ["gate_review", "calibrate", str(out), "--write", str(destination)])
    gate_review.main()
    frozen = load_thresholds(destination)
    assert frozen["status"] == "frozen" and frozen["developmentSet"] == result["developmentSet"]
    assert frozen["questions"] and all(t == result["thresholds"] for t in frozen["questions"].values())
    assert destination.stat().st_mode & 0o222 == 0
    assert "wrote" in capsys.readouterr().out

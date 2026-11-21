import csv
import json

from fastapi.testclient import TestClient

from backend import gate_review
from backend.gates import run_gated_reference
from backend.main import app
from backend.test_pipeline_helpers import FIXTURE, FakeProvider, answers, png, run, setup


def test_routes_store_evidence_and_report_unconfigured_models(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_DIR", str(tmp_path / "pipeline"))
    monkeypatch.setenv("CAPTURE_DIR", str(tmp_path / "captures"))
    monkeypatch.setattr("backend.gateway.config.backend_key", lambda: None)
    monkeypatch.setattr("backend.gateway.service.backend_key", lambda: None)
    batch = json.loads(FIXTURE.read_text())["observationBatch"]
    with TestClient(app) as http:
        options = http.get("/api/edits/options").json()
        assert options["styles"]["photoreal"] == "supported" and options["styles"]["claymation"] == "experimental"
        assert options["genieTransport"]["width"] == 1280
        assert http.post("/api/observations", json=batch).status_code == 200
        assert http.post("/api/observations", json={**batch, "anchors": "x"}).status_code == 422
        duplicates = {**batch, "anchors": [*batch["anchors"], batch["anchors"][0]]}
        invalid = http.post("/api/observations", json=duplicates)
        assert invalid.status_code == 422 and invalid.json()["error"] == "invalid observation batch"
        assert any("duplicate" in error["msg"] for error in invalid.json()["detail"])
        bad = http.post("/api/edits", json={"observationBatchId": batch["id"], "mayorRequest": "bike lane",
                                            "permissions": {"allowed": ["bike_lane"], "forbidden": ["bike_lane"]}})
        assert bad.status_code == 422 and "contradictory" in bad.json()["error"]
        missing = http.post("/api/edits", json={"observationBatchId": batch["id"], "mayorRequest": "bike lane"})
        assert missing.status_code == 503 and missing.json()["code"] == "not_configured"
        assert http.get("/api/edits/edit_nope").status_code == 404
        assert http.get("/api/gates/gate_nope").status_code == 404


def test_blinded_export_score_and_calibration(tmp_path):
    provider = FakeProvider()
    store, bundle = setup(tmp_path, provider)
    provider.images = [png(color=(i, 0, 0)) for i in range(3)]
    provider.decisions = [answers(requested_change=(0.2, 0.8)), answers(requested_change=(0.82, 0.82)),
                          answers()]
    run(tmp_path, provider, lambda gw: run_gated_reference(gw, store, bundle, style="photoreal"))
    out = tmp_path / "review"
    assert gate_review.export(store, out) == 2
    sheet = list(csv.DictReader(open(out / "sheet.csv")))
    assert "accept" not in json.dumps(sheet) and (out / f"{sheet[0]['review_id']}_edited.png").exists()
    key = json.loads((out / "key.json").read_text())
    for row in sheet:  # the human disagrees with the accepted image
        row["label"] = "unacceptable" if key[row["review_id"]]["outcome"] == "accept" else "acceptable"
    with open(out / "sheet.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(sheet[0]))
        writer.writeheader()
        writer.writerows(sheet)
    scored = gate_review.score(out)
    assert scored["falseAcceptance"]["k"] == 1 and scored["falseAcceptance"]["wilson95"]
    result = gate_review.calibrate(store, out, target_far=0.0)
    assert result["metrics"]["falseAcceptance"]["k"] == 0
    assert result["thresholds"]["passAt"] > 0.82 and result["developmentSet"]["examples"] == 2

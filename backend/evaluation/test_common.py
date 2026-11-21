import pytest

from backend.evaluation.common import seconds_to_frames, wilson_interval, yes_probability


def test_wilson_matches_known_values():
    lo, hi = wilson_interval(8, 10)
    assert round(lo, 3) == 0.490 and round(hi, 3) == 0.943
    assert wilson_interval(0, 0) == (0.0, 1.0)
    assert wilson_interval(0, 5)[0] == 0.0 and wilson_interval(5, 5)[1] == 1.0
    with pytest.raises(ValueError):
        wilson_interval(3, 2)


def test_frame_clock_and_noul():
    assert seconds_to_frames(1.5) == 36
    assert yes_probability({"type": "noul", "noul": .9, "confidence": .8}) == .9
    with pytest.raises(ValueError):
        yes_probability({"type": "choice"})


def test_load_recording_reads_genie_runtime_output(tmp_path):
    from backend.evaluation.common import load_recording
    from backend.worlds.evidence import Recording
    rec = Recording(tmp_path, "sess-1")
    rec.frame(b"a", (2, 2))
    rec.frame(b"b", (2, 2))
    rec.event("steering_sent", text_sha256="x")
    rec.frame(b"c", (2, 2))
    frames, steers = load_recording(tmp_path / "worlds" / "sess-1")
    assert [f.index for f in frames] == [0, 1, 2] and frames[0].session_id == "sess-1"
    assert open(frames[2].path, "rb").read() == b"c"
    assert [s["frame_index"] for s in steers] == [1]

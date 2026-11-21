# Retained evidence media

Capture records and the `element119.v1` contract now come from the unified recorder. See [Capture API](CAPTURE_API.md) and [Observations](OBSERVATIONS.md).

`backend/observation_media.py` decodes only permitted prefix bytes into timestamped frames, while `backend/frames.py` retains content-addressed source frames for the edit/reference pipeline. The earlier parallel `backend/evidence_media.py` implementation has been retired to prevent divergent contracts. Held-out media is never an observation or edit input.

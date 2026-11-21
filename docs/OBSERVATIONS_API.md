# Observation API

The integrated service uses the canonical `element119.v1` contracts and the authenticated `/api/observations/extract` API. See [Observations](OBSERVATIONS.md) for routes, extraction reports, prefix frame review, reinspection and manual annotations.

The earlier `/api/observations` capture-input route and separate `data/observations` format have been superseded. Existing local evidence is retained; old artifacts are not silently converted or treated as new model results. The public arbitrary-batch registration route was removed: only validated backend extraction publishes observations into the edit pipeline.

Genie is optional and is not invoked by extraction, review, Carbon editing or reference generation.

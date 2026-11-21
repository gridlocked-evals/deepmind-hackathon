from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.worlds.evidence import Recording, recorded_results


def test_retained_recording_reports_unjudged_frames_without_runtime(tmp_path):
    session_id = str(uuid4())
    recording = Recording(tmp_path, session_id)
    recording.summary({'session_id': session_id, 'state': 'completed', 'usage': {'total_tokens': 12}})
    recording.frame(b'bytes already validated by runtime', (16, 9))
    recording.event('key_down_sent', key='key_w', owner='human')
    with (recording.path / 'events.jsonl').open('ab') as handle:
        handle.write(b'{"partial":')
    value = recorded_results(tmp_path, session_id)
    assert value['session']['usage']['total_tokens'] == 12
    assert value['mediaProvenance'] == 'generated_recording'
    assert value['frames'][0]['frame_index'] == 0
    assert 'file' not in value['frames'][0]
    assert value['controls'][0]['key'] == 'key_w'
    assert value['temporalJudge']['coveredFrames'] == 0
    assert value['temporalJudge']['uncoveredFrames'] == 1
    assert value['temporalJudge']['status'] == 'not_run'
    with pytest.raises(ValueError):
        recorded_results(tmp_path, '../session')
    (recording.path / 'frames' / '000000.jpg').write_bytes(b'changed')
    with pytest.raises(ValueError, match='hash'):
        recorded_results(tmp_path, session_id)


def test_results_route_requires_operator_and_survives_session_cleanup(monkeypatch):
    token = 'recording-review-token-' + 'x' * 32
    monkeypatch.setenv('WORLD_API_TOKEN', token)
    with TestClient(app) as client:
        session_id = str(uuid4())
        recording = Recording(app.state.models.config.data_dir, session_id)
        recording.summary({'session_id': session_id, 'state': 'completed', 'provenance': {}})
        endpoint = f'/api/worlds/{session_id}/results'
        assert client.get(endpoint).status_code == 401
        client.headers['Authorization'] = 'Bearer ' + token
        assert client.get(f'/api/worlds/{session_id}').status_code == 404
        assert client.get(endpoint).json()['session']['state'] == 'completed'
        assert client.get('/api/worlds/bad-id/results').status_code == 404


def test_configured_preview_origin_still_requires_operator(monkeypatch):
    token = 'preview-operator-' + 'x' * 32
    monkeypatch.setenv('WORLD_API_TOKEN', token)
    monkeypatch.setenv('WORLD_ALLOWED_ORIGINS', 'http://127.0.0.1:8002,*,https://bad.example/path')
    with TestClient(app) as client:
        endpoint = '/api/worlds/bad-id/results'
        assert client.get(endpoint, headers={'Origin': 'http://127.0.0.1:8002'}).status_code == 401
        client.headers['Authorization'] = 'Bearer ' + token
        assert client.get(endpoint, headers={'Origin': 'http://127.0.0.1:8002'}).status_code == 404
        assert client.get(endpoint, headers={'Origin': 'https://bad.example'}).status_code == 403

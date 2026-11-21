from backend.config import load_environment


def test_dotenv_does_not_override_deployment_configuration(tmp_path, monkeypatch):
    config = tmp_path / ".env"
    config.write_text("GEMINI_API_KEY=file-key\nWORKSHOP_API_KEY=workshop-key\n")
    monkeypatch.setenv("GEMINI_API_KEY", "deployment-key")
    monkeypatch.delenv("WORKSHOP_API_KEY", raising=False)
    load_environment(config)
    import os
    assert os.environ["GEMINI_API_KEY"] == "deployment-key"
    assert os.environ["WORKSHOP_API_KEY"] == "workshop-key"

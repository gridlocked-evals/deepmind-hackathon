"""Server-only local configuration. Never import this from the browser bundle."""
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_environment(path: Path | None = None) -> None:
    """Load this checkout's .env without overriding deployment environment values."""
    load_dotenv(dotenv_path=path if path is not None else ROOT / ".env", override=False)

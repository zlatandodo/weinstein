"""Shared configuration for WAT tools.

Centralizes path resolution and .env loading so every tool stays consistent.
Import this at the top of any tool script:

    from config import PROJECT_ROOT, TMP_DIR, get_env, load_env
    load_env()
    api_key = get_env("SOME_API_KEY")
"""
from __future__ import annotations

import os
from pathlib import Path

# Project layout (this file lives in tools/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
WORKFLOWS_DIR = PROJECT_ROOT / "workflows"
TMP_DIR = PROJECT_ROOT / ".tmp"
DATA_DIR = PROJECT_ROOT / "data"      # cached yfinance downloads (regenerable)
CHARTS_DIR = PROJECT_ROOT / "charts"  # generated PNG charts
OUTPUT_DIR = PROJECT_ROOT / "output"  # CSV deliverables
ENV_FILE = PROJECT_ROOT / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    """Load key=value pairs from .env into os.environ.

    Uses python-dotenv when available; otherwise falls back to a minimal parser
    so tools still work before dependencies are installed.
    """
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(dotenv_path=path, override=False)
        return
    except ImportError:
        pass

    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_env(name: str, default: str | None = None, required: bool = False) -> str | None:
    """Read an env var. Raise if required and missing."""
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(
            f"Missing required environment variable '{name}'. Add it to {ENV_FILE}."
        )
    return value


def ensure_tmp() -> Path:
    """Make sure the disposable .tmp directory exists and return its path."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    return TMP_DIR

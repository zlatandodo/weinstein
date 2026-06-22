"""Verify the WAT project is initialized correctly.

Usage:
    python tools/check_setup.py
"""
from __future__ import annotations

import sys

from config import (
    ENV_FILE,
    PROJECT_ROOT,
    TMP_DIR,
    TOOLS_DIR,
    WORKFLOWS_DIR,
    load_env,
)


def main() -> int:
    ok = True
    print(f"Project root: {PROJECT_ROOT}")

    for label, path in [
        ("tools/", TOOLS_DIR),
        ("workflows/", WORKFLOWS_DIR),
        (".tmp/", TMP_DIR),
        (".env", ENV_FILE),
    ]:
        exists = path.exists()
        ok &= exists
        print(f"  [{'OK' if exists else 'MISSING'}] {label}")

    load_env()
    try:
        import dotenv  # noqa: F401

        print("  [OK] python-dotenv installed")
    except ImportError:
        print("  [WARN] python-dotenv not installed (run: pip install -r requirements.txt)")

    print("\nSetup looks good." if ok else "\nSetup incomplete — see MISSING above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

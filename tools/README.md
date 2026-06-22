# Tools

Deterministic Python scripts (Layer 3 of the WAT framework). Each tool does one
job well: API calls, data transforms, file ops, queries.

## Conventions
- Load config/secrets via `config.py` (`load_env()` + `get_env()`), never hardcode.
- Read secrets only from `.env`.
- Write intermediates to `.tmp/` (disposable); final deliverables go to cloud services.
- Expose a CLI (argparse) so the Agent can invoke the tool deterministically.
- Print clear errors and exit non-zero on failure.

## Existing tools
- `config.py` — shared paths + `.env` loading (import, don't run directly).
- `check_setup.py` — verifies the project is initialized correctly.

Run a check anytime with:

    python tools/check_setup.py

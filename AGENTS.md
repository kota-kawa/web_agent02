# Repository Guidelines

## Project Structure & Module Organization
- `browser_use/`: core package with automation modules (`agent/`, `controller/`, `llm/`, `tools/`); keep new code in the nearest feature subpackage.
- `tests/`: top-level suite; prefer `tests/unit/` for fast checks and mirror package paths; `tests/ci/` holds the selection exercised in CI.
- `bin/`: helper scripts for setup, linting, and orchestration; update these when workflows change.
- `docs/`: Mintlify documentation; preview updates from this directory with Mintlify CLI.
- `static/`, `browser_use/screenshots/`, `examples/`: shared assets and runnable samples—reuse before adding files.

## Build, Test, and Development Commands
- `./bin/setup.sh`: bootstraps a `uv` virtualenv and installs all extras for local hacking.
- `uv run browseruse --help`: confirm the CLI entry point from source; useful when testing agent flows.
- `./bin/test.sh`: runs the CI-aligned pytest selection with parallelism.
- `./bin/lint.sh`: executes the pre-commit bundle (Ruff fmt/lint + Pyright); run before opening a PR.
- `uv run pytest tests/unit -m "not slow"`: quick local iteration; drop the selector to exercise the full suite.

## Coding Style & Naming Conventions
- Run formatting through `ruff format`; line length 130, tab indentation, and single-quoted strings are enforced.
- Keep module, class, and function names descriptive and snake_case (classes in PascalCase); align filenames with the primary type.
- Favor type hints; Pyright runs in `basic` mode, so annotate public APIs and tricky async flows.
- Document non-obvious behaviour with short comments or docstrings; avoid restating code.

## Testing Guidelines
- Tests use `pytest` with async fixtures; mirror markers (`unit`, `integration`, `slow`) so CI filters stay accurate.
- Name tests `test_<feature>.py` and functions `test_<scenario>__<expected>` to aid xdist diagnostics.
- Skip remote API scenarios when secrets are missing; follow `browser_use/llm/tests/` patterns.
- Aim for reproducible mocks over live network calls; prefer factories in `tests/fixtures/` when available.

## Commit & Pull Request Guidelines
- Follow the existing Conventional Commit style (`feat:`, `fix:`, `chore:`); write imperative, scoped messages (~60 chars).
- Keep PRs focused; include a changelog-style summary, testing notes (`pytest`, `lint`), and link relevant issues.
- Review generated assets before pushing; attach screenshots or logs when modifying user-visible flows or CLI output.

## Security & Configuration Tips
- Copy `secrets.env.example` to `.env` for local keys; never commit populated secrets.
- Set `BROWSER_USE_LOGGING_LEVEL=debug` while developing to capture agent traces; reset before release.
- Lock down browser automation creds and OAuth tokens; rotate promptly if exposed in logs or tests.

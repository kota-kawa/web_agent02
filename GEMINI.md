# Browser Agent Project Context

## Project Overview
This project, **Browser Agent**, is a comprehensive solution for making websites accessible to AI agents. It includes the core `browser-use` library and a Flask-based web application (`flask_app`) that serves as a controller and UI for the agent. The agent leverages LLMs (specifically optimized for Gemini but supporting others) to interact with web browsers via CDP (Chrome DevTools Protocol).

## Key Directories & Files

- **`browser_use/`**: The core Python library containing the agent logic, browser interaction, DOM manipulation, and LLM integration.
    - `agent/`: Agent service and state management.
    - `browser/`: Browser session and context management.
    - `controller/`: High-level control logic.
    - `dom/`: DOM analysis and interaction.
- **`flask_app/`**: The web application layer providing a UI and API.
    - `app.py`: Main Flask application entry point and API endpoints.
    - `controller.py`: `BrowserAgentController` class managing the agent lifecycle.
    - `templates/` & `static/`: Frontend assets (HTML/CSS/JS).
- **`docker/`**: Docker build context and scripts.
- **`bin/`**: Utility scripts for development (`setup.sh`, `lint.sh`, `test.sh`).
- **`AGENTS.md`**: **CRITICAL**. Detailed playbook and architectural guide. Read this for deep understanding of the system flow.
- **`IMPLEMENTATION_SUMMARY.md`**: Summary of recent implementation of the conversation history check endpoint.

## Architecture Highlights

- **Agent Controller**: The `BrowserAgentController` in `flask_app/controller.py` is the heart of the web app. It manages the `Agent` instance, connects to the browser via CDP, and streams events to the UI using SSE (Server-Sent Events).
- **Event Bus**: Uses `bubus.EventBus` for internal communication.
- **Browser**: Runs in a separate container (or locally) and exposes CDP ports. The agent connects to this browser to perform actions.
- **LLM Integration**: Uses `ChatGoogle` (Gemini) by default for this specific agent setup, with robust prompt engineering handling Japanese context.

## Development Workflow

### Prerequisites
- Python >= 3.11
- `uv` (Python package manager)
- Docker & Docker Compose (for full stack execution)

### Setup
```bash
./bin/setup.sh
```
This initializes the environment using `uv`.

### Running the Application
**Via Docker (Recommended for full stack):**
```bash
docker compose up --build
```
This starts the Flask agent (`port 5005`) and the Browser (`port 4444`/`7900`).

**Local Development:**
1.  Activate environment: `source .venv/bin/activate`
2.  Set env vars (see `secrets.env.example`).
3.  Run Flask:
    ```bash
    export FLASK_APP=flask_app/app.py
    uv run flask run --host 0.0.0.0 --port 5005
    ```

### Testing
Run tests using `pytest`.
```bash
# Run unit tests
uv run pytest tests/unit

# Run specific test
uv run pytest tests/unit/test_conversation_history_endpoint.py
```
*Note: Check `pytest.ini` for markers like `slow`, `integration`, etc.*

### Linting & Formatting
```bash
./bin/lint.sh
```
Uses `ruff`, `pyright`, and `codespell`.

## Conventions & Guidelines

- **Language**: The codebase and documentation heavily use **Japanese** for agent instructions and internal logic documentation. Ensure new prompts or logic align with this if they interact with existing Japanese components.
- **Code Style**: Follow the structure defined in `browser_use` (Service/Views/Prompts separation).
- **Dependency Management**: Use `uv` for all package management.
- **Configuration**: Use `.env` and `secrets.env` for secrets. Never commit keys.
- **Documentation**: Update `AGENTS.md` and `IMPLEMENTATION_SUMMARY.md` when making architectural changes.

## Recent Features
- **Conversation History Endpoint**: A new endpoint `/api/check-conversation-history` analyzes chat logs from other agents to trigger browser actions automatically. See `docs/conversation_history_endpoint.md` for details.

## Critical Instruction / 重要指示
どれだけ時間とコストをかけてもよいので、確実に目標が達成されるようにしてほしい。

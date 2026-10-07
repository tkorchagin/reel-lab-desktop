# Reel Lab Backend

FastAPI application, SQLAlchemy models, Alembic migrations, background jobs, and the
existing deterministic media-analysis core.

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn reel_lab.app:app --host 127.0.0.1 --port 8765
```

Project-level documentation lives in the repository root `README.md`.

"""``python -m app`` entry point - runs the FastAPI server with uvicorn."""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("OA_HOST", "0.0.0.0")
    port = int(os.environ.get("OA_PORT", "8000"))
    reload = os.environ.get("OA_RELOAD", "0") in {"1", "true", "yes"}
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()

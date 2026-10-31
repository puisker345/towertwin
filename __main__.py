"""Entrypoint for `python -m towertwin` starting FastAPI server on port 8000."""

from __future__ import annotations

import uvicorn

from . import config


def main() -> None:
    uvicorn.run(
        "towertwin.app:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()

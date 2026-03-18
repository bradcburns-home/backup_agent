"""Backup Agent entry point.

Uses string import for uvicorn to avoid double-import issues.
"""

from __future__ import annotations

import argparse
import logging
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup Agent MCP Server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--transport", default="streamable-http")
    args = parser.parse_args()

    try:
        from burns_logger import configure_logging, configure_uvicorn
        configure_logging(source="backup-agent")
        configure_uvicorn(source="backup-agent")
    except ImportError:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    import uvicorn
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=args.port,
        workers=1,
        log_level="warning",
    )


from backup_agent.app import create_asgi_app  # noqa: E402

app = create_asgi_app()

if __name__ == "__main__":
    main()

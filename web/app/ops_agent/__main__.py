"""Запуск: python -m app.ops_agent"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("OPS_AGENT_HOST", "0.0.0.0")
    port = int(os.environ.get("OPS_AGENT_PORT", "9100"))
    uvicorn.run(
        "app.ops_agent.app:app",
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()

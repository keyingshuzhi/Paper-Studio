"""PyInstaller entry point for the self-contained Paper Studio backend."""

from __future__ import annotations

import sys


def main() -> int:
    if "--mcp-server" in sys.argv[1:]:
        from agent.mcp_server import main as run_mcp_server

        run_mcp_server()
        return 0

    from agent.webapp import main as run_webapp

    return int(run_webapp())


if __name__ == "__main__":
    raise SystemExit(main())

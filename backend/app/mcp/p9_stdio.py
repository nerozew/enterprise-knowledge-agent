from __future__ import annotations

import logging
import sys

from app.core.config import get_settings
from app.mcp.p9_contract import load_stdio_identity
from app.mcp.p9_server import create_p9_mcp_server

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the P9 stdio server; stdout is reserved for MCP JSON-RPC only."""

    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    settings = get_settings()
    identity = load_stdio_identity(settings)
    logger.info(
        "starting unauthenticated P9 MCP stdio skeleton actor=%s workspace=%s",
        identity.actor_id,
        identity.workspace_id,
    )
    create_p9_mcp_server(settings).run(transport="stdio")


if __name__ == "__main__":
    main()

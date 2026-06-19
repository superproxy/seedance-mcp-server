"""Smoke test that uvx-installed package can serve MCP requests."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
env_path = ROOT / ".env"
if env_path.is_file():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)

sys.path.insert(0, str(ROOT))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    server = StdioServerParameters(
        command="uvx",
        args=["--from", str(ROOT), "--refresh", "seedance-mcp-server"],
        env={**os.environ, "UV_INDEX_URL": "https://pypi.org/simple"},
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("uvx-served tools:", names)
            assert "create_video_task" in names

            r = await session.call_tool("list_video_tasks", {"page_size": 1})
            text = "\n".join(c.text for c in r.content if getattr(c, "text", None))
            payload = json.loads(text)
            print("list ok :", payload["success"])
            print("total   :", payload["raw"].get("total"))
            assert payload["success"]
    print(">>> uvx smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""End-to-end MCP stdio test: spawn the server as a subprocess and drive it
through the MCP client SDK."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env
env_path = ROOT / ".env"
if env_path.is_file():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _txt(result) -> str:
    """Extract concatenated text from an MCP CallToolResult / ReadResourceResult."""
    parts = []
    contents = getattr(result, "content", None) or getattr(result, "contents", None) or []
    for c in contents:
        text = getattr(c, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


async def main() -> int:
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "seedance_mcp_server.py")],
        env=os.environ.copy(),
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("=== [1] list_tools ===")
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("tools:", names)
            assert "text_to_video" in names
            assert "create_video_task" in names

            print("\n=== [2] read resource config://settings ===")
            res = await session.read_resource("config://settings")
            text = _txt(res)
            print(text[:300])
            assert "api_key_set" in text

            print("\n=== [3] call_tool list_video_tasks (read-only) ===")
            r = await session.call_tool(
                "list_video_tasks", {"page_num": 1, "page_size": 2}
            )
            payload = json.loads(_txt(r))
            print("success:", payload["success"])
            print("total  :", payload["raw"].get("total"))
            assert payload["success"] is True

            print("\n=== [4] call_tool create_video_task (real) ===")
            r = await session.call_tool(
                "create_video_task",
                {
                    "prompt": "A small turtle slowly walks on a sunlit pebble path.",
                    "duration": 5,
                    "ratio": "16:9",
                    "resolution": "480p",
                    "generate_audio": False,
                    "watermark": False,
                },
            )
            payload = json.loads(_txt(r))
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            assert payload["success"], payload
            tid = payload["task_id"]

            print("\n=== [5] call_tool get_video_task ===")
            r = await session.call_tool("get_video_task", {"task_id": tid})
            payload = json.loads(_txt(r))
            print("status:", payload.get("status"))
            assert payload["success"]

            print("\n=== [6] negative: invalid duration via MCP ===")
            r = await session.call_tool(
                "create_video_task",
                {"prompt": "x", "duration": 3, "ratio": "16:9", "resolution": "480p"},
            )
            payload = json.loads(_txt(r))
            print("success:", payload["success"])
            print("error  :", payload.get("error", "")[:200])
            assert payload["success"] is False
            assert "InvalidParameter" in payload["error"]

    print("\n>>> all MCP checks OK, task_id =", tid)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

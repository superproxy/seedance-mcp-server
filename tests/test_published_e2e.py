"""Validate the published 2.3.1 package via uvx -> MCP stdio.

Covers: list_tools, both resources, list_video_tasks, create_video_task,
get_video_task, encode_image_to_base64, cancel_video_task on the just-created
task, and a negative case (invalid duration)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
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


def _txt(result) -> str:
    parts = []
    contents = (
        getattr(result, "content", None)
        or getattr(result, "contents", None)
        or []
    )
    for c in contents:
        text = getattr(c, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _section(name: str) -> None:
    print(f"\n=== {name} ===")


async def main() -> int:
    server = StdioServerParameters(
        command="uvx",
        args=["--refresh", "seedance-mcp-server==2.3.1"],
        env={**os.environ, "UV_INDEX_URL": "https://pypi.org/simple"},
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.serverInfo.name, init.serverInfo.version)

            _section("[1] list_tools")
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("tools:", names)
            expected = {
                "text_to_image", "text_to_video", "image_to_video",
                "create_video_task", "get_video_task", "list_video_tasks",
                "cancel_video_task", "encode_image_to_base64",
            }
            assert expected.issubset(set(names)), f"missing: {expected - set(names)}"

            _section("[2] resources / list + read")
            res_list = await session.list_resources()
            uris = [str(r.uri) for r in res_list.resources]
            print("resources:", uris)
            for uri in ("config://settings", "config://models"):
                r = await session.read_resource(uri)
                body = _txt(r)
                print(f"  {uri}: {body[:120]}...")
                assert "base_url" in body or "text_to_video" in body

            _section("[3] list_video_tasks")
            r = await session.call_tool(
                "list_video_tasks", {"page_num": 1, "page_size": 3}
            )
            payload = json.loads(_txt(r))
            assert payload["success"] is True, payload
            print("total =", payload["raw"].get("total"))

            _section("[4] encode_image_to_base64 (local)")
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(b"\x89PNG\r\n\x1a\nfake-bytes")
                tmp_img = f.name
            try:
                r = await session.call_tool(
                    "encode_image_to_base64", {"image_path": tmp_img}
                )
                payload = json.loads(_txt(r))
                assert payload["success"] is True, payload
                print("mime:", payload["mime_type"])
                print("data_url prefix:", payload["data_url"][:40])
            finally:
                os.unlink(tmp_img)

            _section("[5] create_video_task (real, minimal cost)")
            r = await session.call_tool(
                "create_video_task",
                {
                    "prompt": "A toy boat drifts gently on calm blue water at sunset.",
                    "duration": 5,
                    "ratio": "16:9",
                    "resolution": "480p",
                    "generate_audio": False,
                    "watermark": False,
                },
            )
            payload = json.loads(_txt(r))
            print(json.dumps(payload, ensure_ascii=False))
            assert payload["success"] is True, payload
            tid = payload["task_id"]

            _section("[6] get_video_task")
            r = await session.call_tool("get_video_task", {"task_id": tid})
            payload = json.loads(_txt(r))
            print("status:", payload.get("status"))
            assert payload["success"] is True

            _section("[7] negative: duration=3 (model rejects)")
            r = await session.call_tool(
                "create_video_task",
                {"prompt": "x", "duration": 3, "ratio": "16:9", "resolution": "480p"},
            )
            payload = json.loads(_txt(r))
            print("success:", payload["success"])
            print("error  :", payload.get("error", "")[:160])
            assert payload["success"] is False
            assert "InvalidParameter" in payload["error"]

            _section("[8] cancel_video_task on the task we just created")
            r = await session.call_tool("cancel_video_task", {"task_id": tid})
            payload = json.loads(_txt(r))
            print(json.dumps(payload, ensure_ascii=False))
            # 不强断言 success -- 任务可能已 succeeded/running 服务端拒绝；
            # 但工具自身必须返回结构化结果而非崩溃
            assert isinstance(payload, dict)
            assert "task_id" in payload

    print("\n>>> ALL MCP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

#!/usr/bin/env python3
"""Doubao Ark generation driver for environments where the MCP client cannot
load seedance-mcp-server directly.

The script starts the server over stdio, calls exactly one MCP tool, prints
the JSON result, and can download image/video output when requested.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
REPO_ROOT = SKILL_DIR.parent.parent

VIDEO_TERMINAL_STATUSES = {"succeeded", "failed", "canceled", "cancelled"}
IMAGE_TOOLS = {"image_to_image", "text_to_image"}


def load_env_file(path: Optional[str]) -> None:
    if not path:
        return
    env_path = Path(path).expanduser().resolve()
    if not env_path.is_file():
        raise FileNotFoundError(f"env file not found: {env_path}")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def server_params(use_uvx: bool):
    from mcp import StdioServerParameters

    env = dict(os.environ)
    if use_uvx:
        uvx = shutil.which("uvx") or "uvx"
        return StdioServerParameters(
            command=uvx, args=["seedance-mcp-server"], env=env
        )

    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return StdioServerParameters(
            command=str(venv_python),
            args=["-m", "seedance_mcp_server"],
            cwd=str(REPO_ROOT),
            env=env,
        )

    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "seedance_mcp_server"],
        cwd=str(REPO_ROOT),
        env=env,
    )


def content_to_json(result: Any) -> dict[str, Any]:
    text = "".join(getattr(c, "text", "") or "" for c in (result.content or []))
    if not text:
        return {"success": False, "error": "MCP server returned empty content"}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"success": False, "raw": text}


async def call_tool(tool: str, arguments: dict[str, Any], use_uvx: bool) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(server_params(use_uvx)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
            return content_to_json(result)


async def poll_video_task(
    task_id: str, interval: int, use_uvx: bool
) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(server_params(use_uvx)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            while True:
                result = await session.call_tool(
                    "get_video_task", {"task_id": task_id}
                )
                data = content_to_json(result)
                status = data.get("status")
                print(f"status: {status}", file=sys.stderr)
                if status in VIDEO_TERMINAL_STATUSES:
                    return data
                await asyncio.sleep(interval)


def run_async(coro):
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return {"success": False, "error": "interrupted"}


def download(url: Optional[str], out: str) -> None:
    if not url:
        raise ValueError("server response did not include a downloadable URL")
    import requests

    response = requests.get(url, timeout=180)
    response.raise_for_status()
    out_path = Path(out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(response.content)
    print(f"saved: {out_path} ({len(response.content)} bytes)", file=sys.stderr)


def tool_arguments(args: argparse.Namespace) -> dict[str, Any]:
    skip = {"env_file", "uvx", "out", "poll", "interval", "tool"}
    return {
        key: value
        for key, value in vars(args).items()
        if key not in skip and value is not None
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Doubao MCP fallback driver")
    parser.add_argument("--env-file", help="path to an ARK_* environment file")
    parser.add_argument(
        "--uvx", action="store_true", help="run seedance-mcp-server via uvx"
    )
    sub = parser.add_subparsers(dest="tool", required=True)

    image_to_image = sub.add_parser("image_to_image")
    image_to_image.add_argument("--prompt", required=True)
    image_to_image.add_argument("--image-url")
    image_to_image.add_argument("--image-path")
    image_to_image.add_argument("--image-base64")
    image_to_image.add_argument("--image-mime")
    image_to_image.add_argument("--negative-prompt")
    image_to_image.add_argument("--size", default="1024x1024")
    image_to_image.add_argument("--model")
    image_to_image.add_argument("--seed", type=int)
    image_to_image.add_argument("--response-format", default="url")
    image_to_image.add_argument("--out", help="download returned image to this path")

    text_to_image = sub.add_parser("text_to_image")
    text_to_image.add_argument("--prompt", required=True)
    text_to_image.add_argument("--negative-prompt")
    text_to_image.add_argument("--size", default="1024x1024")
    text_to_image.add_argument("--model")
    text_to_image.add_argument("--seed", type=int)
    text_to_image.add_argument("--response-format", default="url")
    text_to_image.add_argument("--out")

    create_task = sub.add_parser("create_video_task")
    create_task.add_argument("--prompt", required=True)
    create_task.add_argument("--image-url")
    create_task.add_argument("--image-path")
    create_task.add_argument("--image-base64")
    create_task.add_argument("--image-mime")
    create_task.add_argument("--negative-prompt")
    create_task.add_argument("--ratio", default="adaptive")
    create_task.add_argument("--duration", type=int, default=5)
    create_task.add_argument("--resolution", default="720p")
    create_task.add_argument("--model")
    create_task.add_argument("--seed", type=int)
    create_task.add_argument("--audio", dest="generate_audio", action="store_true")
    create_task.add_argument("--no-audio", dest="generate_audio", action="store_false")
    create_task.set_defaults(generate_audio=False)
    create_task.add_argument("--watermark", action="store_true")

    get_task = sub.add_parser("get_video_task")
    get_task.add_argument("--task-id", required=True)
    get_task.add_argument("--poll", action="store_true")
    get_task.add_argument("--interval", type=int, default=10)
    get_task.add_argument("--out", help="download the finished mp4 to this path")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_env_file(args.env_file)

    if args.tool in IMAGE_TOOLS or args.tool == "create_video_task":
        result = run_async(
            call_tool(args.tool, tool_arguments(args), args.uvx)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.out and result.get("success"):
            download(result.get("image_url"), args.out)
        return 0 if result.get("success") else 1

    if args.tool == "get_video_task":
        if args.poll:
            result = run_async(
                poll_video_task(args.task_id, args.interval, args.uvx)
            )
        else:
            result = run_async(
                call_tool(
                    "get_video_task", {"task_id": args.task_id}, args.uvx
                )
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.out and result.get("status") == "succeeded":
            download((result.get("content") or {}).get("video_url"), args.out)
        return 0 if result.get("status") == "succeeded" else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())

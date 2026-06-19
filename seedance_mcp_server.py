# seedance_mcp_server.py
"""Seedance MCP Server: text-to-image / text-to-video / image-to-video.

All configuration is read on demand via ``get_api_key()`` / ``get_base_url()`` /
``_resolve_model()``. There are no module-level mutable globals on purpose so
that environment changes always take effect without re-importing.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests
from openai import OpenAI
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("seedance_mcp_server")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())

mcp = FastMCP("AI Generation Server")

DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

DEFAULT_TEXT_TO_IMAGE_MODEL = "doubao-seedream-3-0-t2i-250415"
DEFAULT_IMAGE_TO_VIDEO_MODEL = "doubao-seedance-2-0-fast-260128"
DEFAULT_TEXT_TO_VIDEO_MODEL = "doubao-seedance-2-0-fast-260128"

DEFAULT_HTTP_TIMEOUT = 60
DEFAULT_TASK_POLL_INTERVAL = 5
DEFAULT_TASK_POLL_MAX_RETRIES = 60  # 5s * 60 = 5min, suitable for sync MCP calls
ASYNC_TASK_POLL_MAX_RETRIES = 360   # 30min, exposed via param for power users


# ---------------------------------------------------------------------------
# config accessors
# ---------------------------------------------------------------------------

def get_api_key() -> Optional[str]:
    return os.getenv("DOUBAO_API_KEY")


def get_base_url() -> str:
    return os.getenv("DOUBAO_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _resolve_model(explicit: Optional[str], default: str) -> str:
    if explicit:
        return explicit
    env_value = os.getenv("DOUBAO_MODEL")
    if env_value:
        return env_value
    return default


def _require_api_key() -> str:
    api_key = get_api_key()
    if not api_key:
        raise ValueError("API key is required (set DOUBAO_API_KEY)")
    return api_key


def _auth_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_require_api_key()}",
        "Content-Type": "application/json",
    }


def initialize_client() -> OpenAI:
    return OpenAI(api_key=_require_api_key(), base_url=get_base_url())


# ---------------------------------------------------------------------------
# HTTP wrapper
# ---------------------------------------------------------------------------

def _doubao_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: int = DEFAULT_HTTP_TIMEOUT,
) -> Dict[str, Any]:
    """HTTP call into Doubao with uniform error envelope.

    The returned dict always carries an ``_ok`` boolean. On success the parsed
    JSON body is merged in (when it's a dict); otherwise we wrap the raw value
    so callers don't need to special-case list responses.
    """
    url = f"{get_base_url()}{path}"
    try:
        resp = requests.request(
            method,
            url,
            headers=_auth_headers(),
            params=params,
            json=json_body,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning("doubao request failed: %s %s -> %s", method, path, exc)
        return {"_ok": False, "status_code": 0, "error": f"network error: {exc}"}
    except ValueError as exc:
        # _require_api_key() raises ValueError when key missing
        return {"_ok": False, "status_code": 0, "error": str(exc)}

    if resp.status_code >= 400:
        return {
            "_ok": False,
            "status_code": resp.status_code,
            "error": resp.text,
        }

    if not resp.content:
        return {"_ok": True, "_status_code": resp.status_code}

    try:
        data = resp.json()
    except ValueError:
        return {"_ok": True, "_status_code": resp.status_code, "raw": resp.text}

    if isinstance(data, dict):
        out = dict(data)
        out["_ok"] = True
        out["_status_code"] = resp.status_code
        return out
    return {"_ok": True, "_status_code": resp.status_code, "data": data}


# ---------------------------------------------------------------------------
# image helpers
# ---------------------------------------------------------------------------

def _resolve_image_to_url(
    image_url: Optional[str],
    image_base64: Optional[str],
    image_path: Optional[str],
    mime_type: Optional[str] = None,
) -> str:
    """Normalize one of (url / base64 / local path) into a data URL string."""
    sources = [s for s in (image_url, image_base64, image_path) if s]
    if len(sources) != 1:
        raise ValueError(
            "image_url / image_base64 / image_path 必须且只能提供其中一个"
        )

    if image_url:
        return image_url

    if image_path:
        path = Path(os.path.expanduser(image_path)).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"图片不存在: {path}")
        guessed = mime_type or mimetypes.guess_type(str(path))[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
        return f"data:{guessed};base64,{encoded}"

    # image_base64
    assert image_base64 is not None
    if image_base64.startswith("data:"):
        return image_base64
    mime = mime_type or "image/jpeg"
    return f"data:{mime};base64,{image_base64}"


def _normalize_image_inputs(
    image_url: Optional[str],
    image_base64: Optional[str],
    image_path: Optional[str],
    image_mime: Optional[str],
) -> Optional[str]:
    if not any((image_url, image_base64, image_path)):
        return None
    return _resolve_image_to_url(image_url, image_base64, image_path, image_mime)


def _build_reference_part(role: str, kind: str, url: str) -> Dict[str, Any]:
    if kind not in {"image_url", "video_url", "audio_url"}:
        raise ValueError(f"未知的参考资源类型: {kind}")
    return {"type": kind, kind: {"url": url}, "role": role}


# ---------------------------------------------------------------------------
# video task helpers
# ---------------------------------------------------------------------------

def _build_video_payload(
    *,
    model: str,
    prompt: str,
    ratio: Optional[str],
    duration: Optional[Union[int, str]],
    resolution: Optional[str],
    seed: Optional[int],
    fps: Optional[int],
    camerafixed: Optional[bool],
    generate_audio: Optional[bool],
    watermark: Optional[bool],
    negative_prompt: Optional[str],
    first_frame: Optional[str],
    last_frame: Optional[str],
    reference_images: Optional[List[str]],
    reference_videos: Optional[List[str]],
    reference_audios: Optional[List[str]],
) -> Dict[str, Any]:
    """Build the Doubao /contents/generations/tasks payload.

    Note: parameters such as ratio/duration/resolution/seed/fps/camerafixed/
    generate_audio/watermark/negative_prompt are sent as **top-level fields**.
    They are no longer duplicated as ``--flag`` suffixes inside ``prompt``.
    """
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    if first_frame:
        content.append(_build_reference_part("first_frame", "image_url", first_frame))
    if last_frame:
        content.append(_build_reference_part("last_frame", "image_url", last_frame))
    for url in reference_images or []:
        content.append(_build_reference_part("reference_image", "image_url", url))
    for url in reference_videos or []:
        content.append(_build_reference_part("reference_video", "video_url", url))
    for url in reference_audios or []:
        content.append(_build_reference_part("reference_audio", "audio_url", url))

    payload: Dict[str, Any] = {"model": model, "content": content}

    if ratio:
        payload["ratio"] = ratio
    if duration is not None:
        try:
            payload["duration"] = int(duration)
        except (TypeError, ValueError):
            payload["duration"] = duration
    if resolution:
        payload["resolution"] = resolution
    if seed is not None:
        payload["seed"] = seed
    if fps is not None:
        payload["fps"] = fps
    if camerafixed is not None:
        payload["camerafixed"] = camerafixed
    if generate_audio is not None:
        payload["generate_audio"] = generate_audio
    if watermark is not None:
        payload["watermark"] = watermark
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt

    return payload


def _extract_task_id(data: Dict[str, Any]) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    for key in ("id", "task_id"):
        value = data.get(key)
        if value:
            return value
    nested = data.get("data")
    if isinstance(nested, dict):
        for key in ("id", "task_id"):
            value = nested.get(key)
            if value:
                return value
    return None


def _create_video_task_raw(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _doubao_request("POST", "/contents/generations/tasks", json_body=payload)


def _get_video_task_raw(task_id: str) -> Dict[str, Any]:
    return _doubao_request("GET", f"/contents/generations/tasks/{task_id}")


def _wait_video_task(
    task_id: str,
    poll_interval: int = DEFAULT_TASK_POLL_INTERVAL,
    max_retries: int = DEFAULT_TASK_POLL_MAX_RETRIES,
) -> Dict[str, Any]:
    """Poll a task. Queries first, then sleeps between attempts."""
    last_status: Optional[str] = None
    for attempt in range(max_retries):
        data = _get_video_task_raw(task_id)
        if not data.get("_ok"):
            return {
                "success": False,
                "error": f"查询任务失败: {data.get('error')}",
                "task_id": task_id,
            }
        status = data.get("status")
        last_status = status
        if status == "succeeded":
            content = data.get("content", {}) or {}
            return {
                "success": True,
                "task_id": task_id,
                "status": status,
                "video_url": content.get("video_url"),
                "content": content,
                "usage": data.get("usage"),
                "message": "视频生成成功",
            }
        if status in ("failed", "canceled", "cancelled"):
            return {
                "success": False,
                "task_id": task_id,
                "status": status,
                "error": data.get("error") or f"任务{status}",
            }
        if attempt < max_retries - 1:
            time.sleep(poll_interval)
    return {
        "success": False,
        "task_id": task_id,
        "status": last_status,
        "error": (
            f"轮询超时（>{poll_interval * max_retries}s）"
            "；可改用 create_video_task + get_video_task 异步轮询"
        ),
    }


def _run_sync_video_task(
    payload: Dict[str, Any],
    poll_interval: int,
    poll_max_retries: int,
) -> Dict[str, Any]:
    created = _create_video_task_raw(payload)
    if not created.get("_ok"):
        return {
            "success": False,
            "error": f"创建任务失败: {created.get('error')}",
        }
    task_id = _extract_task_id(created)
    if not task_id:
        return {"success": False, "error": "未获取到任务ID", "raw": _strip_meta(created)}
    logger.info("created video task %s", task_id)
    return _wait_video_task(task_id, poll_interval, poll_max_retries)


def _strip_meta(data: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in data.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# tools - text to image
# ---------------------------------------------------------------------------

@mcp.tool()
def text_to_image(
    prompt: str,
    size: str = "1024x1024",
    model: Optional[str] = None,
    seed: Optional[int] = None,
    guidance_scale: Optional[float] = None,
    watermark: bool = False,
    response_format: str = "url",
    n: int = 1,
) -> Dict[str, Any]:
    """文生图：调用 /images/generations。

    Args:
        prompt: 图片描述提示词
        size: 图片尺寸，如 "1024x1024"
        model: 模型 id；默认按 DOUBAO_MODEL 或内置 doubao-seedream-3-0-t2i-250415
        seed: 随机种子，便于复现
        guidance_scale: 提示词强度 (1~10)，越大越贴合 prompt
        watermark: 是否添加水印
        response_format: "url" 或 "b64_json"
        n: 生成张数
    """
    try:
        client = initialize_client()
        effective_model = _resolve_model(model, DEFAULT_TEXT_TO_IMAGE_MODEL)

        params: Dict[str, Any] = {
            "model": effective_model,
            "prompt": prompt,
            "size": size,
            "response_format": response_format,
            "n": n,
        }
        if seed is not None:
            params["seed"] = seed
        if guidance_scale is not None:
            params["guidance_scale"] = guidance_scale
        if watermark:
            params["extra_body"] = {"watermark": True}

        response = client.images.generate(**params)
        if not response.data:
            return {"success": False, "error": "未返回图片数据"}

        images: List[Dict[str, Any]] = [
            {
                "url": getattr(item, "url", None),
                "b64_json": getattr(item, "b64_json", None),
            }
            for item in response.data
        ]
        primary = images[0]
        return {
            "success": True,
            "model": effective_model,
            "image_url": primary.get("url"),
            "image_b64": primary.get("b64_json"),
            "images": images,
            "message": "图片生成成功",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("text_to_image failed")
        return {"success": False, "error": f"生成图片时出错: {e}"}


# ---------------------------------------------------------------------------
# tools - video (sync convenience)
# ---------------------------------------------------------------------------

@mcp.tool()
def text_to_video(
    prompt: str,
    duration: Optional[Union[int, str]] = 5,
    ratio: Optional[str] = "16:9",
    model: Optional[str] = None,
    resolution: Optional[str] = None,
    seed: Optional[int] = None,
    fps: Optional[int] = None,
    camerafixed: Optional[bool] = None,
    generate_audio: Optional[bool] = None,
    watermark: Optional[bool] = None,
    negative_prompt: Optional[str] = None,
    reference_images: Optional[List[str]] = None,
    reference_videos: Optional[List[str]] = None,
    reference_audios: Optional[List[str]] = None,
    poll_interval: int = DEFAULT_TASK_POLL_INTERVAL,
    poll_max_retries: int = DEFAULT_TASK_POLL_MAX_RETRIES,
) -> Dict[str, Any]:
    """文生视频（同步版）：创建任务并阻塞轮询直到完成。

    长视频或网络较慢时建议改用 ``create_video_task`` + ``get_video_task``，
    避免 MCP 客户端 RPC 超时。默认轮询 5 分钟（60 * 5s）。
    """
    try:
        effective_model = _resolve_model(model, DEFAULT_TEXT_TO_VIDEO_MODEL)
        payload = _build_video_payload(
            model=effective_model,
            prompt=prompt,
            ratio=ratio,
            duration=duration,
            resolution=resolution,
            seed=seed,
            fps=fps,
            camerafixed=camerafixed,
            generate_audio=generate_audio,
            watermark=watermark,
            negative_prompt=negative_prompt,
            first_frame=None,
            last_frame=None,
            reference_images=reference_images,
            reference_videos=reference_videos,
            reference_audios=reference_audios,
        )
        return _run_sync_video_task(payload, poll_interval, poll_max_retries)
    except Exception as e:  # noqa: BLE001
        logger.exception("text_to_video failed")
        return {"success": False, "error": f"生成视频时出错: {e}"}


@mcp.tool()
def image_to_video(
    prompt: str,
    image_url: Optional[str] = None,
    image_base64: Optional[str] = None,
    image_path: Optional[str] = None,
    image_mime: Optional[str] = None,
    last_frame_url: Optional[str] = None,
    last_frame_base64: Optional[str] = None,
    last_frame_path: Optional[str] = None,
    last_frame_mime: Optional[str] = None,
    duration: Optional[Union[int, str]] = 5,
    ratio: Optional[str] = "16:9",
    model: Optional[str] = None,
    resolution: Optional[str] = None,
    seed: Optional[int] = None,
    fps: Optional[int] = None,
    camerafixed: Optional[bool] = None,
    generate_audio: Optional[bool] = None,
    watermark: Optional[bool] = None,
    negative_prompt: Optional[str] = None,
    reference_images: Optional[List[str]] = None,
    reference_videos: Optional[List[str]] = None,
    reference_audios: Optional[List[str]] = None,
    poll_interval: int = DEFAULT_TASK_POLL_INTERVAL,
    poll_max_retries: int = DEFAULT_TASK_POLL_MAX_RETRIES,
) -> Dict[str, Any]:
    """图生视频（同步版）：支持 url / base64 / 本地路径 三选一，可附首尾帧。

    ``last_frame_mime`` 与 ``image_mime`` 独立，避免首尾帧 MIME 互相污染。
    """
    try:
        first_frame = _normalize_image_inputs(
            image_url, image_base64, image_path, image_mime
        )
        if not first_frame:
            return {
                "success": False,
                "error": "至少需提供 image_url / image_base64 / image_path 之一",
            }
        last_frame = _normalize_image_inputs(
            last_frame_url, last_frame_base64, last_frame_path, last_frame_mime
        )

        effective_model = _resolve_model(model, DEFAULT_IMAGE_TO_VIDEO_MODEL)
        payload = _build_video_payload(
            model=effective_model,
            prompt=prompt,
            ratio=ratio,
            duration=duration,
            resolution=resolution,
            seed=seed,
            fps=fps,
            camerafixed=camerafixed,
            generate_audio=generate_audio,
            watermark=watermark,
            negative_prompt=negative_prompt,
            first_frame=first_frame,
            last_frame=last_frame,
            reference_images=reference_images,
            reference_videos=reference_videos,
            reference_audios=reference_audios,
        )
        return _run_sync_video_task(payload, poll_interval, poll_max_retries)
    except Exception as e:  # noqa: BLE001
        logger.exception("image_to_video failed")
        return {"success": False, "error": f"生成视频时出错: {e}"}


# ---------------------------------------------------------------------------
# tools - async task primitives
# ---------------------------------------------------------------------------

@mcp.tool()
def create_video_task(
    prompt: str,
    model: Optional[str] = None,
    image_url: Optional[str] = None,
    image_base64: Optional[str] = None,
    image_path: Optional[str] = None,
    image_mime: Optional[str] = None,
    last_frame_url: Optional[str] = None,
    last_frame_base64: Optional[str] = None,
    last_frame_path: Optional[str] = None,
    last_frame_mime: Optional[str] = None,
    duration: Optional[Union[int, str]] = None,
    ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    seed: Optional[int] = None,
    fps: Optional[int] = None,
    camerafixed: Optional[bool] = None,
    generate_audio: Optional[bool] = None,
    watermark: Optional[bool] = None,
    negative_prompt: Optional[str] = None,
    reference_images: Optional[List[str]] = None,
    reference_videos: Optional[List[str]] = None,
    reference_audios: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """异步创建视频生成任务，仅返回 task_id 不轮询。"""
    try:
        first_frame = _normalize_image_inputs(
            image_url, image_base64, image_path, image_mime
        )
        last_frame = _normalize_image_inputs(
            last_frame_url, last_frame_base64, last_frame_path, last_frame_mime
        )
        default_model = (
            DEFAULT_IMAGE_TO_VIDEO_MODEL if first_frame else DEFAULT_TEXT_TO_VIDEO_MODEL
        )
        effective_model = _resolve_model(model, default_model)
        payload = _build_video_payload(
            model=effective_model,
            prompt=prompt,
            ratio=ratio,
            duration=duration,
            resolution=resolution,
            seed=seed,
            fps=fps,
            camerafixed=camerafixed,
            generate_audio=generate_audio,
            watermark=watermark,
            negative_prompt=negative_prompt,
            first_frame=first_frame,
            last_frame=last_frame,
            reference_images=reference_images,
            reference_videos=reference_videos,
            reference_audios=reference_audios,
        )
        result = _create_video_task_raw(payload)
        if not result.get("_ok"):
            return {
                "success": False,
                "error": f"创建任务失败: {result.get('error')}",
            }
        task_id = _extract_task_id(result)
        if not task_id:
            return {
                "success": False,
                "error": "未获取到任务ID",
                "raw": _strip_meta(result),
            }
        return {
            "success": True,
            "task_id": task_id,
            "model": effective_model,
            "raw": _strip_meta(result),
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("create_video_task failed")
        return {"success": False, "error": f"创建任务出错: {e}"}


@mcp.tool()
def get_video_task(task_id: str) -> Dict[str, Any]:
    """查询视频任务状态。"""
    data = _get_video_task_raw(task_id)
    if not data.get("_ok"):
        return {"success": False, "error": data.get("error"), "task_id": task_id}
    return {
        "success": True,
        "task_id": task_id,
        "status": data.get("status"),
        "content": data.get("content"),
        "usage": data.get("usage"),
        "raw": _strip_meta(data),
    }


@mcp.tool()
def list_video_tasks(
    page_num: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    task_ids: Optional[List[str]] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """查询视频任务列表。"""
    params: Dict[str, Any] = {"page_num": page_num, "page_size": page_size}
    if status:
        params["filter.status"] = status
    if task_ids:
        params["filter.task_ids"] = ",".join(task_ids)
    if model:
        params["filter.model"] = model
    data = _doubao_request("GET", "/contents/generations/tasks", params=params)
    if not data.get("_ok"):
        return {"success": False, "error": data.get("error")}
    return {"success": True, "raw": _strip_meta(data)}


@mcp.tool()
def cancel_video_task(task_id: str) -> Dict[str, Any]:
    """取消或删除视频任务。先尝试 POST /cancel，回退到 DELETE。"""
    cancel = _doubao_request(
        "POST", f"/contents/generations/tasks/{task_id}/cancel"
    )
    if cancel.get("_ok"):
        return {"success": True, "task_id": task_id, "method": "cancel"}

    # 回退：部分网关 / 已完成任务只接受 DELETE
    deleted = _doubao_request("DELETE", f"/contents/generations/tasks/{task_id}")
    if deleted.get("_ok"):
        return {"success": True, "task_id": task_id, "method": "delete"}

    return {
        "success": False,
        "task_id": task_id,
        "error": deleted.get("error") or cancel.get("error"),
    }


# ---------------------------------------------------------------------------
# misc tools
# ---------------------------------------------------------------------------

@mcp.tool()
def encode_image_to_base64(image_path: str) -> Dict[str, Any]:
    """将本地图片文件编码为 base64 字符串。"""
    try:
        path = Path(os.path.expanduser(image_path)).resolve()
        if not path.is_file():
            return {"success": False, "error": f"图片不存在: {path}"}
        with path.open("rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
        mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
        return {
            "success": True,
            "base64_string": encoded_string,
            "mime_type": mime,
            "data_url": f"data:{mime};base64,{encoded_string}",
            "message": "图片编码成功",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("encode_image_to_base64 failed")
        return {"success": False, "error": f"编码图片失败: {e}"}


# ---------------------------------------------------------------------------
# resources
# ---------------------------------------------------------------------------

@mcp.resource("config://models")
def get_available_models() -> str:
    models = {
        "env": "DOUBAO_MODEL",
        "env_value": os.getenv("DOUBAO_MODEL"),
        "text_to_image": {
            "default": _resolve_model(None, DEFAULT_TEXT_TO_IMAGE_MODEL),
            "builtin_default": DEFAULT_TEXT_TO_IMAGE_MODEL,
        },
        "image_to_video": {
            "default": _resolve_model(None, DEFAULT_IMAGE_TO_VIDEO_MODEL),
            "builtin_default": DEFAULT_IMAGE_TO_VIDEO_MODEL,
        },
        "text_to_video": {
            "default": _resolve_model(None, DEFAULT_TEXT_TO_VIDEO_MODEL),
            "builtin_default": DEFAULT_TEXT_TO_VIDEO_MODEL,
        },
    }
    return f"模型配置: {models}"


@mcp.resource("config://settings")
def get_server_settings() -> str:
    settings = {
        "base_url": get_base_url(),
        "api_key_set": bool(get_api_key()),
        "default_poll_interval_s": DEFAULT_TASK_POLL_INTERVAL,
        "default_sync_max_retries": DEFAULT_TASK_POLL_MAX_RETRIES,
        "supported_image_sizes": [
            "512x512",
            "768x768",
            "1024x1024",
            "1024x1792",
            "1792x1024",
        ],
        "supported_video_ratios": [
            "16:9",
            "9:16",
            "1:1",
            "4:3",
            "3:4",
            "21:9",
            "adaptive",
        ],
        "supported_video_resolutions": ["480p", "720p", "1080p"],
        "supported_video_durations_s": [3, 5, 10, 11, 12, 15],
        "tools": [
            "text_to_image",
            "text_to_video",
            "image_to_video",
            "create_video_task",
            "get_video_task",
            "list_video_tasks",
            "cancel_video_task",
            "encode_image_to_base64",
        ],
    }
    return f"服务器配置: {settings}"


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

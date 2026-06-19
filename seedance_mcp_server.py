# seedance_mcp_server.py
import time
import base64
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests
from openai import OpenAI
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("AI Generation Server")

DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

DEFAULT_TEXT_TO_IMAGE_MODEL = "doubao-seedream-3-0-t2i-250415"
DEFAULT_IMAGE_TO_VIDEO_MODEL = "doubao-seedance-2-0-fast-260128"
DEFAULT_TEXT_TO_VIDEO_MODEL = "doubao-seedance-2-0-fast-260128"

DEFAULT_HTTP_TIMEOUT = 60
DEFAULT_TASK_POLL_INTERVAL = 5
DEFAULT_TASK_POLL_MAX_RETRIES = 360  # 5s * 360 = 30min


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


def _auth_headers() -> Dict[str, str]:
    api_key = get_api_key()
    if not api_key:
        raise ValueError("API key is required (set DOUBAO_API_KEY)")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _doubao_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: int = DEFAULT_HTTP_TIMEOUT,
) -> Dict[str, Any]:
    url = f"{get_base_url()}{path}"
    resp = requests.request(
        method,
        url,
        headers=_auth_headers(),
        params=params,
        json=json_body,
        timeout=timeout,
    )
    if resp.status_code >= 400:
        return {
            "_ok": False,
            "status_code": resp.status_code,
            "error": resp.text,
        }
    if not resp.content:
        return {"_ok": True, "status_code": resp.status_code}
    try:
        data = resp.json()
    except ValueError:
        return {"_ok": True, "status_code": resp.status_code, "raw": resp.text}
    data["_ok"] = True
    data["_status_code"] = resp.status_code
    return data


def initialize_client() -> OpenAI:
    api_key = get_api_key()
    if not api_key:
        raise ValueError("API key is required")
    return OpenAI(api_key=api_key, base_url=get_base_url())


# ---------------------------------------------------------------------------
# image / video content helpers
# ---------------------------------------------------------------------------

_PROMPT_FLAG_PATTERNS = {
    "ratio": re.compile(r"--ratio\s"),
    "duration": re.compile(r"--(duration|dur)\s"),
    "resolution": re.compile(r"--rs\s|--resolution\s"),
    "seed": re.compile(r"--seed\s"),
    "fps": re.compile(r"--fps\s"),
    "camerafixed": re.compile(r"--camerafixed\s"),
    "watermark": re.compile(r"--watermark\s"),
}


def _maybe_append_flag(prompt: str, flag: str, value: Any) -> str:
    if value is None or value == "":
        return prompt
    if _PROMPT_FLAG_PATTERNS[flag].search(prompt):
        return prompt
    return f"{prompt} --{flag} {value}"


def _build_prompt_with_flags(
    prompt: str,
    *,
    ratio: Optional[str] = None,
    duration: Optional[Union[int, str]] = None,
    resolution: Optional[str] = None,
    seed: Optional[int] = None,
    fps: Optional[int] = None,
    camerafixed: Optional[bool] = None,
    watermark: Optional[bool] = None,
) -> str:
    prompt = _maybe_append_flag(prompt, "ratio", ratio)
    prompt = _maybe_append_flag(prompt, "duration", duration)
    prompt = _maybe_append_flag(prompt, "resolution", resolution)
    prompt = _maybe_append_flag(prompt, "seed", seed)
    prompt = _maybe_append_flag(prompt, "fps", fps)
    if camerafixed is not None:
        prompt = _maybe_append_flag(prompt, "camerafixed", str(camerafixed).lower())
    if watermark is not None:
        prompt = _maybe_append_flag(prompt, "watermark", str(watermark).lower())
    return prompt


def _resolve_image_to_url(
    image_url: Optional[str],
    image_base64: Optional[str],
    image_path: Optional[str],
    mime_type: Optional[str] = None,
) -> str:
    """把 url / base64 / 本地路径统一转成可投递给 Doubao 的字符串 URL。"""
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
    mime = mime_type or "image/jpeg"
    if image_base64.startswith("data:"):
        return image_base64
    return f"data:{mime};base64,{image_base64}"


def _build_reference_part(role: str, kind: str, url: str) -> Dict[str, Any]:
    if kind not in {"image_url", "video_url", "audio_url"}:
        raise ValueError(f"未知的参考资源类型: {kind}")
    return {"type": kind, kind: {"url": url}, "role": role}


# ---------------------------------------------------------------------------
# task helpers
# ---------------------------------------------------------------------------

def _create_video_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _doubao_request(
        "POST", "/contents/generations/tasks", json_body=payload
    )


def _get_video_task(task_id: str) -> Dict[str, Any]:
    return _doubao_request("GET", f"/contents/generations/tasks/{task_id}")


def _wait_video_task(
    task_id: str,
    poll_interval: int = DEFAULT_TASK_POLL_INTERVAL,
    max_retries: int = DEFAULT_TASK_POLL_MAX_RETRIES,
) -> Dict[str, Any]:
    last_status = None
    for _ in range(max_retries):
        time.sleep(poll_interval)
        data = _get_video_task(task_id)
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
    return {
        "success": False,
        "task_id": task_id,
        "status": last_status,
        "error": f"轮询超时（>{poll_interval * max_retries // 60}min）",
    }


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
        # watermark 经由 extra_body 传递（OpenAI 客户端不识别该字段）
        params["extra_body"] = {"watermark": watermark}

        response = client.images.generate(**params)

        if not response.data:
            return {"success": False, "error": "未返回图片数据"}

        images: List[Dict[str, Any]] = []
        for item in response.data:
            images.append(
                {
                    "url": getattr(item, "url", None),
                    "b64_json": getattr(item, "b64_json", None),
                }
            )

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
        return {"success": False, "error": f"生成图片时出错: {e}"}


# ---------------------------------------------------------------------------
# tools - video helpers (sync convenience + async primitives)
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
    final_prompt = _build_prompt_with_flags(
        prompt,
        ratio=ratio,
        duration=duration,
        resolution=resolution,
        seed=seed,
        fps=fps,
        camerafixed=camerafixed,
    )
    if negative_prompt:
        final_prompt = f"{final_prompt}\nNegative prompt: {negative_prompt}"

    content: List[Dict[str, Any]] = [{"type": "text", "text": final_prompt}]

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

    # 这些字段官方 API 接受顶层参数；不存在的字段服务端会忽略
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
    if generate_audio is not None:
        payload["generate_audio"] = generate_audio
    if watermark is not None:
        payload["watermark"] = watermark
    return payload


def _normalize_image_inputs(
    image_url: Optional[str],
    image_base64: Optional[str],
    image_path: Optional[str],
    image_mime: Optional[str],
) -> Optional[str]:
    if not any((image_url, image_base64, image_path)):
        return None
    return _resolve_image_to_url(image_url, image_base64, image_path, image_mime)


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
    """文生视频（同步版）：创建任务并阻塞轮询直到完成。"""
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
        created = _create_video_task(payload)
        if not created.get("_ok"):
            return {
                "success": False,
                "error": f"创建任务失败: {created.get('error')}",
            }
        task_id = created.get("id")
        if not task_id:
            return {"success": False, "error": "未获取到任务ID"}
        return _wait_video_task(task_id, poll_interval, poll_max_retries)
    except Exception as e:  # noqa: BLE001
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
    """图生视频（同步版）：支持 url / base64 / 本地路径 三选一，可附首尾帧、参考图/视频/音频。"""
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
            last_frame_url, last_frame_base64, last_frame_path, image_mime
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
        created = _create_video_task(payload)
        if not created.get("_ok"):
            return {
                "success": False,
                "error": f"创建任务失败: {created.get('error')}",
            }
        task_id = created.get("id")
        if not task_id:
            return {"success": False, "error": "未获取到任务ID"}
        return _wait_video_task(task_id, poll_interval, poll_max_retries)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"生成视频时出错: {e}"}


# ---------- async task primitives ----------

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
            last_frame_url, last_frame_base64, last_frame_path, image_mime
        )
        # 没有首帧也算文生视频任务，不强制
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
        result = _create_video_task(payload)
        if not result.get("_ok"):
            return {
                "success": False,
                "error": f"创建任务失败: {result.get('error')}",
            }
        return {
            "success": True,
            "task_id": result.get("id"),
            "model": effective_model,
            "raw": {k: v for k, v in result.items() if not k.startswith("_")},
        }
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"创建任务出错: {e}"}


@mcp.tool()
def get_video_task(task_id: str) -> Dict[str, Any]:
    """查询视频任务状态。"""
    data = _get_video_task(task_id)
    if not data.get("_ok"):
        return {"success": False, "error": data.get("error"), "task_id": task_id}
    return {
        "success": True,
        "task_id": task_id,
        "status": data.get("status"),
        "content": data.get("content"),
        "usage": data.get("usage"),
        "raw": {k: v for k, v in data.items() if not k.startswith("_")},
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
    return {
        "success": True,
        "raw": {k: v for k, v in data.items() if not k.startswith("_")},
    }


@mcp.tool()
def cancel_video_task(task_id: str) -> Dict[str, Any]:
    """取消或删除视频任务。"""
    data = _doubao_request("DELETE", f"/contents/generations/tasks/{task_id}")
    if not data.get("_ok"):
        return {"success": False, "task_id": task_id, "error": data.get("error")}
    return {"success": True, "task_id": task_id}


# ---------------------------------------------------------------------------
# misc tools
# ---------------------------------------------------------------------------

@mcp.tool()
def encode_image_to_base64(image_path: str) -> Dict[str, Any]:
    """将本地图片文件编码为 base64 字符串。"""
    try:
        path = Path(os.path.expanduser(image_path)).resolve()
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

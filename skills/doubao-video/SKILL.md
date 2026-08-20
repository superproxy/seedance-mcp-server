---
name: doubao-video
description: Generate images and videos with the Volcengine Ark Doubao models
  (Seedance / Seedream). Use for image-to-video, first-frame video, text-to-image,
  image-to-image, 豆包/火山方舟/Seedance/Seedream workflows, and short clips from a
  photo. The skill prefers direct MCP tools and includes a fallback driver script.
metadata:
  version: 1.2.0
---

# Doubao / Seedance Video

Generate images and short videos through the Volcengine Ark Doubao models. The
canonical pipeline for a photo-to-video request is:

```text
photo/selfie → image_to_image restyle/first frame → user chooses frame
             → create_video_task → get_video_task polling → download mp4
```

## When to use

- The user provides a photo and wants a video using it as the first frame.
- The user asks for 文生图、图生图、文生视频、图生视频, Seedance, Seedream, 豆包, or 火山方舟.
- The user wants a short mp4 from an image and expects a downloaded result.

## Calling rules

1. Prefer direct MCP tools named `mcp__seedance-mcp-server__*` when available.
2. If MCP tools are unavailable, run the bundled fallback script from the repository root:

   ```bash
   PY=.venv/bin/python
   SCRIPT=skills/doubao-video/scripts/doubao_mcp.py
   ENV=.env

   # restyle a photo and download the first frame
   $PY "$SCRIPT" --env-file "$ENV" image_to_image \
     --image-path /path/to/photo.jpg \
     --size 1152x1536 \
     --prompt "..." \
     --negative-prompt "真实人物,照片,写实,3D" \
     --out /tmp/frame.jpg

   # create a first-frame video task
   $PY "$SCRIPT" --env-file "$ENV" create_video_task \
     --image-path /tmp/frame.jpg \
     --ratio adaptive \
     --duration 10 \
     --resolution 720p \
     --audio \
     --prompt "..."

   # poll and download the mp4
   $PY "$SCRIPT" --env-file "$ENV" get_video_task \
     --task-id cgt-... \
     --poll \
     --out ~/Desktop/result.mp4
   ```

   Add `--uvx` to run the server through `uvx seedance-mcp-server` instead of the local repository environment.
3. If direct MCP and the fallback script both fail, report the exact blocker and the prepared command.

## Environment variables

| Var | Required | Purpose |
| --- | --- | --- |
| `ARK_API_KEY` | yes | Volcengine Ark API key, e.g. `ark-...` |
| `ARK_BASE_URL` | no | Defaults to `https://ark.cn-beijing.volces.com/api/v3` |
| `ARK_VIDEO_MODEL` | no | Video model override |
| `ARK_IMG_MODEL` | no | Image model override |

Use `/api/v3`; `/api/plan/v3` may return subscription errors unless the account has an eligible plan.

## Models

Image and video model roles are independent:

| Role | Default model | Capability |
| --- | --- | --- |
| Image | `doubao-seedream-5-0-pro-260628` | text-to-image and image-to-image |
| Video | `doubao-seedance-2-0-fast-260128` | text-to-video and image-to-video unless overridden |

For current high-quality Seedance 2.5 video, set `ARK_VIDEO_MODEL=doubao-seedance-2-5-260628`.

## Key MCP tools

- `text_to_image(prompt, size, model, seed, ...)`
- `image_to_image(prompt, image_url|image_base64|image_path, image_mime, size, negative_prompt, ...)`
- `create_video_task(prompt, image_url|image_base64|image_path, ratio, duration, resolution, generate_audio, ...)` returns `{success, task_id}`
- `get_video_task(task_id)` returns status and `content.video_url`
- `text_to_video`, `image_to_video` for synchronous blocking calls
- `list_video_tasks`, `cancel_video_task`, `encode_image_to_base64`

## Video parameters

| Param | Guidance |
| --- | --- |
| `ratio` | Use `adaptive` for first-frame/first-last-frame tasks so the output follows the first frame. A fixed ratio can be rejected. |
| `duration` | Integer seconds; common values are 5 or 10. |
| `resolution` | `480p`, `720p`, or `1080p` where supported by the model/account. |
| `generate_audio` | Audio is rendered during generation and cannot be reliably added later. Ask the user when unclear. |
| `watermark` | Usually `false`. |
| first frame | URL, data URL/base64, or local path. The MCP/server converts local paths to data URLs. |

The first frame controls composition, character likeness, clothing, setting, and aspect ratio. The video prompt should describe motion, action beat, camera motion, lighting, and atmosphere rather than restating static appearance.

## Recommended workflow

1. Read the source photo and identify subject, outfit, pose, setting, and source aspect ratio.
2. If the source is a realistic photo/selfie, restyle it first with `image_to_image`; create 2-3 candidates and let the user choose. Use negative prompt `真实人物, 照片, 写实, 3D, 摄影质感` when a 2D/anime look is desired.
3. Generate the first frame at the target delivery ratio.
4. Write a concise motion prompt with one clear action arc and explicit camera behavior.
5. Create the video with `ratio="adaptive"` and poll every ~10 seconds.
6. Download the signed video URL immediately; it expires.
7. Report local path, size, duration, resolution, ratio, fps, and audio status.

See `references/prompting.md` for prompt templates and examples.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `API key is required (set ARK_API_KEY)` | Export `ARK_API_KEY=ark-...` or add it to the `.env` file used by the fallback script. |
| First-frame ratio error | Use `ratio="adaptive"`; do not force `16:9` from a vertical/square first frame. |
| `InputImageSensitiveContentDetected.PrivacyInformation` | Restyle/cartoonize the source image before video generation. |
| `InvalidSubscription` / AgentPlan | Use `ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3`, not `/api/plan/v3`. |
| `ModuleNotFoundError: mcp.server.fastmcp` | Run with a Python environment where `mcp[cli]>=1.9.4,<2` is installed. The repository `.venv` is the default fallback environment. |
| MCP tools unavailable | Use `skills/doubao-video/scripts/doubao_mcp.py`; do not ask the user to reload first. |

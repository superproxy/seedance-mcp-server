# Doubao / Seedance Prompting & Tool Reference

Companion to `../SKILL.md`.

## Image-to-image (cartoonize a photo) — prompt template

```
<STYLE>风格，赛璐璐上色，清晰黑色描边线，平涂色块/手绘笔触。
保留人物的长相、<HAIR>、<TOP>和<ACCESSORY>。
场景变为<SCENE>：<SUBJECT ACTION>，<PROPS>。
<LIGHTING/MOOD>，背景虚化，高饱和暖色调，电影感景深，干净线稿。
```

Negative prompt:

```
真实人物，照片，写实，3D渲染，皮肤毛孔，摄影质感，模糊，变形，
多余手指，多手，手指畸形，脸部崩坏，人物走样，文字水印，logo，低画质，暗黑，恐怖
```

Three style variants worth offering:

- **日漫赛璐璐** — clean black outlines, flat cel-shading, big eyes, high saturation.
- **水彩插画** — soft watercolor wash, paper texture, muted palette, no hard outlines.
- **90年代复古动画** — retro cel look, film grain, warm orange grade, hand-painted feel.

Preserve recognizable features so the result still reads as the person. For requests
that add a real public figure, render them only as a clearly fictional 2D anime
character matching the described look, never a photorealistic likeness.

## Video prompt template (first frame → motion)

The first frame already shows composition/outfits/setting, so describe **motion,
action arc, camera, atmosphere only**:

```
<SETTING/MOOD>，<SUBJECT>在<LOCATION>。
<ACTION BEAT 1>，然后<ACTION BEAT 2>，接着<ACTION BEAT 3>。
<ENVIRONMENT MOTION：蒸汽/沸腾/光斑/花瓣…>。
镜头<CAMERA MOVEMENT>，带轻微手持晃动感，背景虚化。
延续首帧的<STYLE>质感，描边/色调/氛围描述。
```

Worked example (milktea encounter, 3 girls):

```
阳光明媚的日系街头，三个动漫风格的年轻女孩惊喜偶遇。中间棕色波浪发、挎粉色爱心包的女孩
开心地睁大眼、张大嘴露出惊喜的笑容；右侧长直发穿浅色连衣裙的新朋友从街边轻快跑来，
身体微微前倾，兴奋地挥手加入；左侧黑色短发穿黑色外套的女孩也笑着举起奶茶。
三人开心地把手里的奶茶杯举到一起碰杯，杯里的奶茶晃动，吸管轻轻颤动，
然后各自吸一口奶茶，相视而笑，气氛欢乐温暖。
镜头从略微侧面缓缓向前平移推进，带轻微手持晃动感，背景街道和自动贩卖机柔和虚化。
柔和逆光，空气中飘着粉色花瓣和闪闪光斑，画面边缘有淡淡的动漫光晕。
延续首帧的日式动漫赛璐璐质感，清晰描边，高饱和暖色调，青春治愈氛围。
```

50–100 words (Chinese). One clear action arc beats a list of gags.

## Camera vocabulary

- 镜头缓缓向前推进 / slow push in (dolly in)
- 镜头跟随平移 / tracking shot
- 手持晃动感 / handheld
- 固定镜头 / static
- 环绕 / orbit, 左右平移 / pan (sparingly)
- 背景虚化 / shallow depth of field

Always specify camera movement or "static"; otherwise it is random.

## Fallback script reference

`scripts/doubao_mcp.py` is the fallback when MCP tools aren't loaded. Run it with the
project venv python (which has the `mcp` dependency) and an env file providing
`ARK_*`. Common invocations:

```bash
PY=.venv/bin/python
ENVF=.env
SCRIPT=skills/doubao-video/scripts/doubao_mcp.py

# 1) cartoonize / restyle -> first frame (downloaded via --out)
$PY $SCRIPT --env-file "$ENVF" image_to_image \
  --image-path /path/to/photo.jpg --size 1152x1536 \
  --prompt "..." --negative-prompt "真实人物,照片,写实,3D" \
  --out /tmp/frame.jpg

# 2) submit first-frame video -> prints task_id
$PY $SCRIPT --env-file "$ENVF" create_video_task \
  --image-path /tmp/frame.jpg --ratio adaptive --duration 10 \
  --resolution 720p --audio \
  --prompt "..." --negative-prompt "..."

# 3) poll to completion and download mp4
$PY $SCRIPT --env-file "$ENVF" get_video_task \
  --task-id cgt-... --poll --out ~/Desktop/result.mp4
```

Useful flags: `--uvx` (run server via `uvx seedance-mcp-server`), `--seed`, `--model`.
`create_video_task --audio` sets `generate_audio=true`.

## Sizes and aspect ratios

| Source | image_to_image `size` | Video output ratio |
| --- | --- | --- |
| Square 1:1 | `1024x1024` / `2048x2048` | 1:1 |
| Vertical 3:4 | `1152x1536` | 3:4 |
| Vertical 9:16 | `1080x1920` | 9:16 |
| Landscape 16:9 | `1920x1080` | 16:9 |

First-frame video forces `ratio=adaptive`, so the output ratio follows the first frame.
Generate the frame at the ratio you want to deliver.

## Audio

`generate_audio` is a generation-time render (synced ambient/effects). It cannot be
added to an already-rendered silent video — re-run generation. Ask the user
explicitly before the final render; use silent for drafts.

## Cost/speed

- 720p 5s ≈ 90s render; 720p 10s ≈ 120–180s. 480p is cheaper for drafts.
- image_to_image is ~30–60s — generate 2–3 style candidates cheaply before committing.
- Video tokens: ~100k for 5s, ~215k for 10s. Iterate action at 5s/480p, final at 10s/720p.
- The signed result URL expires in 24h; always download.

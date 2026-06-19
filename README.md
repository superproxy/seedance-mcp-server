# seedance-mcp-server

火山引擎豆包 MCP Server：文生图、文生视频、图生视频，以及视频任务的异步管理。基于 [`superproxy/doubao_mcp_server`](https://github.com/superproxy/doubao_mcp_server) 修改而来，支持通过环境变量配置 `DOUBAO_BASE_URL` 与默认模型。代码模块名为 `seedance_mcp_server`，PyPI 包名 / CLI 入口为 `seedance-mcp-server`。

当前版本：**v2.3.0**（PyPI: <https://pypi.org/project/seedance-mcp-server/>）

## 环境变量

| 名称 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `DOUBAO_API_KEY` | 是 | - | 火山引擎方舟 API Key，未设置时调用任何工具都会报 `API key is required` |
| `DOUBAO_BASE_URL` | 否 | `https://ark.cn-beijing.volces.com/api/v3` | 方舟 API 网关地址，用于切换区域、私有网关或自建代理；末尾 `/` 会被自动去掉 |
| `DOUBAO_MODEL` | 否 | 各工具内置默认模型 | 统一覆盖三个生成工具的默认模型；调用时未传 `model` 参数才生效 |

`DOUBAO_BASE_URL` 同时作用于：

- `initialize_client()` 创建的 OpenAI 兼容客户端（`text_to_image`）
- 所有视频工具通过 `requests` 调用的 `/contents/generations/tasks` 端点

模型选择按 “显式 `model` 参数 → `DOUBAO_MODEL` → 内置默认值” 的顺序生效。`config://models` 资源会返回当前生效的默认模型与内置默认值，便于排查。

各工具的内置默认模型：

| 工具 | 内置默认模型 |
| --- | --- |
| `text_to_image` | `doubao-seedream-3-0-t2i-250415` |
| `image_to_video` | `doubao-seedance-2-0-fast-260128` |
| `text_to_video` | `doubao-seedance-2-0-fast-260128` |

## 工具一览

`seedance-mcp-server` 暴露的 MCP 工具：

| 工具 | 类型 | 说明 |
| --- | --- | --- |
| `text_to_image` | 同步 | 文生图，封装 `/images/generations`，支持 `seed / guidance_scale / watermark / response_format / n` |
| `text_to_video` | 同步（轮询直到完成） | 文生视频，支持参考图/视频/音频、`generate_audio / watermark / seed / resolution / fps / camerafixed / negative_prompt` |
| `image_to_video` | 同步（轮询直到完成） | 图生视频，首帧 + 可选尾帧，支持 `image_url` / `image_base64` / `image_path` 三选一；同样支持参考素材与全部高级参数 |
| `create_video_task` | 异步 | 仅创建任务返回 `task_id`，不阻塞；同时覆盖文生视频与图生视频 |
| `get_video_task` | 异步 | 查询单个任务状态及输出 |
| `list_video_tasks` | 异步 | 分页查询任务列表，支持按 status / model / task_ids 过滤 |
| `cancel_video_task` | 异步 | 取消或删除任务 |
| `encode_image_to_base64` | 工具 | 把本地图片编码成 base64，便于 `image_to_video` 使用 |

视频工具走 `POST/GET/DELETE /contents/generations/tasks`；同步工具默认轮询上限 **5 分钟**（`poll_interval=5s`，`poll_max_retries=60`），可在调用时覆盖。耗时较长的任务建议直接使用 `create_video_task` + `get_video_task` 异步轮询，避免 MCP 客户端 RPC 超时。

### 视频高级参数

`text_to_video` / `image_to_video` / `create_video_task` 共享以下参数（不传即不发往服务端）：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `ratio` | str | `16:9` / `9:16` / `1:1` / `4:3` / `3:4` / `21:9` / `adaptive` |
| `duration` | int | 单位秒，常见 `3 / 5 / 10 / 11 / 12 / 15` |
| `resolution` | str | `480p` / `720p` / `1080p` |
| `seed` | int | 随机种子 |
| `fps` | int | 帧率 |
| `camerafixed` | bool | 是否固定镜头 |
| `generate_audio` | bool | 是否生成音频 |
| `watermark` | bool | 是否加水印 |
| `negative_prompt` | str | 负面描述 |
| `reference_images` | list[str] | 参考图片 URL 列表（不当作首帧/尾帧） |
| `reference_videos` | list[str] | 参考视频 URL 列表 |
| `reference_audios` | list[str] | 参考音频 URL 列表 |

`image_to_video` 额外提供 `last_frame_url / last_frame_base64 / last_frame_path` 用于尾帧；`image_mime` 与 `last_frame_mime` 各自独立，避免首尾帧 MIME 互相污染。

### 调用示例

文生视频（同步）：

```jsonc
{
  "tool": "text_to_video",
  "arguments": {
    "prompt": "一只小熊猫坐在木桌旁举起咖啡杯",
    "duration": 5,
    "ratio": "16:9",
    "resolution": "720p",
    "generate_audio": true
  }
}
```

图生视频（首帧 + 尾帧）：

```jsonc
{
  "tool": "image_to_video",
  "arguments": {
    "prompt": "镜头从苹果落入雪克杯，慢镜头特写",
    "image_url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg",
    "last_frame_url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic2.jpg",
    "duration": 8,
    "ratio": "16:9",
    "generate_audio": true,
    "reference_audios": ["https://ark-project.tos-cn-beijing.volces.com/doc_audio/r2v_tea_audio1.mp3"]
  }
}
```

异步任务全流程：

```jsonc
// 1. 创建任务
{
  "tool": "create_video_task",
  "arguments": {"prompt": "...", "duration": 5}
}
// -> { "success": true, "task_id": "cgt-..." }

// 2. 轮询状态
{ "tool": "get_video_task", "arguments": {"task_id": "cgt-..."} }

// 3. 查询历史
{ "tool": "list_video_tasks", "arguments": {"page_size": 20, "status": "succeeded"} }

// 4. 取消
{ "tool": "cancel_video_task", "arguments": {"task_id": "cgt-..."} }
```

## 本地以 uvx 运行

需要先安装 [`uv`](https://docs.astral.sh/uv/)。

跑已发布版本：

```bash
DOUBAO_API_KEY=ark-xxx uvx seedance-mcp-server
```

跑当前工作区源码：

```bash
DOUBAO_API_KEY=ark-xxx \
DOUBAO_BASE_URL=https://ark.cn-beijing.volces.com/api/v3 \
uvx --from . seedance-mcp-server
```

如果默认 PyPI 源不可用（例如清华镜像 403），临时切官方源：

```bash
UV_INDEX_URL=https://pypi.org/simple uvx --refresh seedance-mcp-server
```

## 在 MCP 客户端中注册

以 stdio 形式注册到任何兼容的 MCP 客户端：

```json
{
  "mcpServers": {
    "seedance": {
      "command": "uvx",
      "args": ["seedance-mcp-server"],
      "env": {
        "DOUBAO_API_KEY": "ark-xxx",
        "DOUBAO_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
        "DOUBAO_MODEL": "doubao-seedance-2-0-fast-260128"
      }
    }
  }
}
```

如果想固定使用本地工作区代码而不是 PyPI 版本：

```json
"args": ["--from", "/absolute/path/to/seedance-mcp-server", "seedance-mcp-server"]
```

注意：`DOUBAO_MODEL` 会覆盖所有生成工具的默认模型。某个工具想用自己的模型，请在 MCP 调用里显式传 `model` 参数，参数优先级最高。

## 资源

- `config://settings`：返回当前生效的 `base_url`、`api_key_set`、默认轮询参数（`default_poll_interval_s` / `default_sync_max_retries`）、支持的 ratio / resolution / duration、工具清单等运行时配置，便于排查环境是否生效。
- `config://models`：返回当前可用模型默认值。

## 版本历史

- **2.3.0**
  - 重构 `seedance_mcp_server.py`：`_doubao_request` 兜底网络异常与非 dict 响应；`_wait_video_task` 先查后等，避免无谓首次 sleep；`_extract_task_id` 兼容 `id` / `task_id` / `data.id`
  - 视频参数（`ratio` / `duration` / `resolution` / `seed` / `fps` / `camerafixed` / `generate_audio` / `watermark` / `negative_prompt`）统一改为顶层 JSON 字段，不再以 `--flag` 形式注入 prompt 文本
  - `negative_prompt` 走顶层字段，对模型实际生效
  - `image_to_video` 新增 `last_frame_mime` 参数，与 `image_mime` 独立
  - `cancel_video_task` 改为先 `POST /cancel`，失败回退 `DELETE`
  - 同步工具默认轮询上限调整为 5 分钟（`poll_max_retries=60`），长任务请使用 `create_video_task` + `get_video_task`
  - 关键路径加 `logging`；新增 `tests/test_smoke.py`（45 用例）和 `tests/test_mcp_e2e.py`（stdio 端到端）
- **2.2.2**
  - 同步发布到 PyPI 与 GitHub，覆盖之前 1.0.0 / 1.1.0 / 2.0.0 等被占用版本号
- **2.0.0**
  - 新增 `create_video_task` / `get_video_task` / `list_video_tasks` / `cancel_video_task` 异步任务工具
  - `text_to_image` 默认模型修正为 `doubao-seedream-3-0-t2i-250415`，支持 `seed / guidance_scale / watermark / response_format / n`
  - `image_to_video` 支持 `image_url / image_base64 / image_path` 三选一，去掉写死的 jpeg MIME 与 `--ratio adaptive`
  - 视频工具统一支持首帧/尾帧、参考图·视频·音频、`generate_audio / watermark / seed / resolution / fps / camerafixed / negative_prompt`
  - 抽出 `_doubao_request / _build_video_payload / _wait_video_task` 公共逻辑
  - 轮询超时由 5min 提升至 30min，所有 HTTP 调用增加 `timeout`
- **1.0.0** 初版（已停止维护，建议升级到 2.x）

## 开发说明

主要逻辑集中在 `seedance_mcp_server.py`，配置统一通过 `get_api_key()` / `get_base_url()` / `_resolve_model()` 读取，不依赖模块级全局。新增配置项请沿用同一收口方式。

## 发布到 PyPI

构建与发布：

```bash
UV_INDEX_URL=https://pypi.org/simple uv build
uv publish --token pypi-xxxxxxxxxxxx
# 或 twine
uvx twine upload dist/seedance_mcp_server-*
```

发布前请确认：

- `pyproject.toml` 中 `name` / `version` 没有与 PyPI 上现有版本冲突（PyPI 删除版本后号码不可复用）。
- 发布账号已开启 2FA 并使用 API token，token 不要提交到仓库。

也可以先通过 TestPyPI 演练：

```bash
uv publish --publish-url https://test.pypi.org/legacy/ --token pypi-xxxxxxxxxxxx
UV_INDEX_URL=https://test.pypi.org/simple/ uvx --refresh seedance-mcp-server
```

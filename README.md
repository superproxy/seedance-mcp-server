# seedance-mcp-server

火山引擎豆包 MCP Server：文生图、文生视频、图生视频。基于 [`superproxy/doubao_mcp_server`](https://github.com/superproxy/doubao_mcp_server) 修改而来，支持通过环境变量配置 `DOUBAO_BASE_URL`。代码模块名为 `seedance_mcp_server`，PyPI 包名 / CLI 入口为 `seedance-mcp-server`。

## 环境变量

| 名称 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `DOUBAO_API_KEY` | 是 | - | 火山引擎方舟 API Key，未设置时调用任何工具都会报 `API key is required` |
| `DOUBAO_BASE_URL` | 否 | `https://ark.cn-beijing.volces.com/api/v3` | 方舟 API 网关地址，用于切换区域、私有网关或自建代理；末尾 `/` 会被自动去掉 |
| `DOUBAO_MODEL` | 否 | 各工具内置默认模型 | 统一覆盖三个工具的默认模型；调用时未传 `model` 参数才生效 |

`DOUBAO_BASE_URL` 同时作用于：

- `initialize_client()` 创建的 OpenAI 兼容客户端（`text_to_image`）
- `text_to_video()` / `image_to_video()` 通过 `requests` 调用 `/contents/generations/tasks` 的视频任务接口

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
| `image_to_video` | 同步（轮询直到完成） | 图生视频，首帧 + 可选尾帧，支持 url / base64 / 本地路径三选一；同样支持参考素材与全部高级参数 |
| `create_video_task` | 异步 | 仅创建任务返回 `task_id`，不阻塞；同时覆盖文生视频与图生视频 |
| `get_video_task` | 异步 | 查询单个任务状态及输出 |
| `list_video_tasks` | 异步 | 分页查询任务列表，支持按 status / model / task_ids 过滤 |
| `cancel_video_task` | 异步 | 取消或删除任务 |
| `encode_image_to_base64` | 工具 | 把本地图片编码成 base64，便于 `image_to_video` 使用 |

视频任务相关工具调用 `POST/GET/DELETE /contents/generations/tasks` 端点；同步版默认轮询上限为 30 分钟（`poll_interval=5s`，`poll_max_retries=360`），可在调用时覆盖。

## 本地以 uvx 运行

需要先安装 [`uv`](https://docs.astral.sh/uv/)。

跑当前工作区源码：

```bash
DOUBAO_API_KEY=ark-xxx \
DOUBAO_BASE_URL=https://ark.cn-beijing.volces.com/api/v3 \
uvx --from . seedance-mcp-server
```

跑已发布版本：

```bash
DOUBAO_API_KEY=ark-xxx uvx seedance-mcp-server
```

如果默认 PyPI 源不可用（例如清华镜像 403），临时切官方源：

```bash
UV_INDEX_URL=https://pypi.org/simple uvx --from . seedance-mcp-server
```

## 在 MCP 客户端中注册

以 stdio 形式注册到任何兼容的 MCP 客户端：

```json
{
  "mcpServers": {
    "doubao": {
      "command": "uvx",
      "args": ["seedance-mcp-server"],
      "env": {
        "DOUBAO_API_KEY": "ark-xxx",
        "DOUBAO_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3"
      }
    }
  }
}
```

如果想固定使用本地工作区代码而不是 PyPI 上的版本：

```json
"args": ["--from", "/absolute/path/to/seedance-mcp-server", "seedance-mcp-server"]
```

如果默认模型需要切换，可以放进 `env`，例如：

```json
"env": {
  "DOUBAO_API_KEY": "ark-xxx",
  "DOUBAO_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
  "DOUBAO_MODEL": "doubao-seedance-2-0-260128"
}
```

注意：`DOUBAO_MODEL` 会覆盖三个工具的默认模型。如果某个工具想用自己的模型，请在 MCP 调用里显式传 `model` 参数，参数优先级最高。

## 资源

- `config://settings`：返回当前生效的 `base_url`、`api_key_set` 等运行时配置，便于排查环境变量是否生效。
- `config://models`：返回当前可用模型列表。

## 开发说明

主要逻辑集中在 `seedance_mcp_server.py`，配置统一通过 `get_api_key()` 和 `get_base_url()` 读取，不再依赖模块级全局。如需新增配置项，建议沿用同一收口方式。

## 发布到 PyPI

构建与发布：

```bash
UV_INDEX_URL=https://pypi.org/simple uv build
uv publish --token pypi-xxxxxxxxxxxx
```

发布前请确认：

- `pyproject.toml` 中 `name` / `version` 没有与 PyPI 上现有版本冲突。
- 发布账号已开启 2FA 并使用 API token，token 不要提交到仓库。

也可以先通过 TestPyPI 演练：

```bash
uv publish --publish-url https://test.pypi.org/legacy/ --token pypi-xxxxxxxxxxxx
UV_INDEX_URL=https://test.pypi.org/simple/ uvx --refresh seedance-mcp-server
```

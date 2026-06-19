# AGENTS.md

## Current State
- Local workspace root is currently empty, but the intended codebase for this task is the upstream repo `superproxy/doubao_mcp_server`.

## Working Rules
- Do not start implementation in this empty workspace by guessing a stack. Pull or mirror the upstream repo first when the task refers to `superproxy/doubao_mcp_server`.

## Upstream Repo Facts
- Upstream is a small Python package, not a monorepo. Verified files at repo root: `pyproject.toml`, `uv.lock`, `seedance_mcp_server.py`, `__init__.py` (this repo renames the historical `doubao_mcp_server.py` to `seedance_mcp_server.py`).
- Runtime requirement is Python `>=3.13` from `pyproject.toml`.
- This repo is published as `seedance-mcp-server`. The console entrypoint is `seedance-mcp-server = "seedance_mcp_server:main"`. The legacy upstream package name `doubao-mcp-server` belongs to a different maintainer on PyPI; do not reintroduce that name.
- Almost all behavior lives in `seedance_mcp_server.py`; check that file first before adding helpers or new modules.

## Integration Gotchas
- The server talks to Doubao through two paths: `OpenAI(api_key=..., base_url=BASE_URL)` in `initialize_client()` and raw `requests` calls to `f"{BASE_URL}/contents/generations/tasks"` in video generation tools. A `base_url` feature is incomplete unless both paths are updated consistently.
- `get_server_settings()` exposes `base_url`, so configuration changes should keep that resource accurate.
- Current auth flow is split: `initialize_client()` reads `DOUBAO_API_KEY` from env, but `text_to_video()` and `image_to_video()` build `Authorization` headers from the module-global `API_KEY`. Verify API key handling end-to-end when touching config code.

## Configuration
- `DOUBAO_API_KEY` is required at runtime. `DOUBAO_BASE_URL` is optional and falls back to `DEFAULT_BASE_URL` (`https://ark.cn-beijing.volces.com/api/v3`).
- A single env var `DOUBAO_MODEL` overrides the default model for all three tools. Resolution order is explicit `model` arg → `DOUBAO_MODEL` → built-in default. Do not move defaults back into hard-coded function signatures or split the env into per-tool variables; route everything through `_resolve_model()` so `config://models` stays accurate.
- Both base URL and API key are read via `get_api_key()` / `get_base_url()` — do not reintroduce module-level `API_KEY` or `BASE_URL` globals.

## Local Run
- Preferred local entrypoint is `uvx --from . doubao-mcp-server` from the repo root. The PyPI form `uvx doubao-mcp-server` only works when the configured index actually serves the package.
- `uv.lock` pins the index to a TUNA mirror that may return HTTP 403 outside CN networks. When that happens, override with `UV_INDEX_URL=https://pypi.org/simple` instead of editing `uv.lock` blindly.

## Verification
- No test, lint, or CI config is present. Minimum useful verification is `uvx --from . doubao-mcp-server` startup plus reading `config://settings` to confirm the effective `base_url`. Do not claim end-to-end success without that.

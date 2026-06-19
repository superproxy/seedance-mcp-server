# DOUBAO_BASE_URL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `DOUBAO_BASE_URL` environment-variable support with a default value and make all Doubao client paths use the same effective base URL.

**Architecture:** Keep the project single-file and introduce tiny configuration helpers inside `doubao_mcp_server.py`. Route both the OpenAI client initialization path and raw `requests` video-generation path through the same configuration reads so runtime behavior stays consistent.

**Tech Stack:** Python 3.13, `mcp`, `openai`, `requests`

---

### Task 1: Unify configuration reads

**Files:**
- Modify: `doubao_mcp_server.py`

- [ ] Add a default base URL constant plus tiny helpers for `DOUBAO_API_KEY` and `DOUBAO_BASE_URL`.
- [ ] Update `initialize_client()` to use those helpers.
- [ ] Update `text_to_video()` and `image_to_video()` to stop reading stale module globals and use the same helpers.
- [ ] Keep `config://settings` accurate by returning the effective runtime base URL.

### Task 2: Verify runtime behavior

**Files:**
- Modify: `doubao_mcp_server.py`

- [ ] Verify import succeeds.
- [ ] Verify `config://settings` shows the default base URL with no env override.
- [ ] Verify `DOUBAO_BASE_URL=<custom>` changes the reported base URL.

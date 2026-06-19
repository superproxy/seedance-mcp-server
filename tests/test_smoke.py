"""Local self-test for seedance_mcp_server. Run via ``python tests/test_smoke.py``.

No network, no real API key required. Uses unittest only (stdlib)."""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DOUBAO_API_KEY", "test-key")

import seedance_mcp_server as srv  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_resolve_model_explicit_wins(self):
        self.assertEqual(srv._resolve_model("a", "b"), "a")

    def test_resolve_model_env(self):
        with mock.patch.dict(os.environ, {"DOUBAO_MODEL": "envm"}, clear=False):
            self.assertEqual(srv._resolve_model(None, "default"), "envm")

    def test_resolve_model_default(self):
        env = {k: v for k, v in os.environ.items() if k != "DOUBAO_MODEL"}
        with mock.patch.dict(os.environ, env, clear=True):
            os.environ["DOUBAO_API_KEY"] = "test-key"
            self.assertEqual(srv._resolve_model(None, "fallback"), "fallback")

    def test_base_url_strips_trailing_slash(self):
        with mock.patch.dict(os.environ, {"DOUBAO_BASE_URL": "https://x/api/v3/"}):
            self.assertEqual(srv.get_base_url(), "https://x/api/v3")

    def test_require_api_key_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                srv._require_api_key()


class ImageResolverTests(unittest.TestCase):
    def test_url_passthrough(self):
        self.assertEqual(
            srv._resolve_image_to_url("http://x/a.jpg", None, None), "http://x/a.jpg"
        )

    def test_mutex(self):
        with self.assertRaises(ValueError):
            srv._resolve_image_to_url("http://x", "abc", None)
        with self.assertRaises(ValueError):
            srv._resolve_image_to_url(None, None, None)

    def test_base64_wrap(self):
        out = srv._resolve_image_to_url(None, "AAAA", None, "image/png")
        self.assertEqual(out, "data:image/png;base64,AAAA")

    def test_base64_data_url_passthrough(self):
        out = srv._resolve_image_to_url(None, "data:image/jpeg;base64,XXXX", None)
        self.assertEqual(out, "data:image/jpeg;base64,XXXX")

    def test_path_missing(self):
        with self.assertRaises(FileNotFoundError):
            srv._resolve_image_to_url(None, None, "/no/such/file.jpg")

    def test_normalize_image_inputs_none(self):
        self.assertIsNone(srv._normalize_image_inputs(None, None, None, None))


class TaskIdExtractTests(unittest.TestCase):
    def test_id(self):
        self.assertEqual(srv._extract_task_id({"id": "cgt-1"}), "cgt-1")

    def test_task_id(self):
        self.assertEqual(srv._extract_task_id({"task_id": "cgt-2"}), "cgt-2")

    def test_nested(self):
        self.assertEqual(srv._extract_task_id({"data": {"id": "cgt-3"}}), "cgt-3")

    def test_none(self):
        self.assertIsNone(srv._extract_task_id({"foo": "bar"}))
        self.assertIsNone(srv._extract_task_id(None))


class PayloadBuilderTests(unittest.TestCase):
    def test_minimal_payload(self):
        p = srv._build_video_payload(
            model="m", prompt="hi", ratio=None, duration=None, resolution=None,
            seed=None, fps=None, camerafixed=None, generate_audio=None,
            watermark=None, negative_prompt=None, first_frame=None, last_frame=None,
            reference_images=None, reference_videos=None, reference_audios=None,
        )
        self.assertEqual(p, {"model": "m", "content": [{"type": "text", "text": "hi"}]})

    def test_all_fields_top_level(self):
        p = srv._build_video_payload(
            model="m", prompt="hi", ratio="16:9", duration=10, resolution="1080p",
            seed=42, fps=24, camerafixed=True, generate_audio=True, watermark=False,
            negative_prompt="ugly", first_frame=None, last_frame=None,
            reference_images=None, reference_videos=None, reference_audios=None,
        )
        self.assertEqual(p["ratio"], "16:9")
        self.assertEqual(p["duration"], 10)
        self.assertEqual(p["resolution"], "1080p")
        self.assertEqual(p["seed"], 42)
        self.assertEqual(p["fps"], 24)
        self.assertIs(p["camerafixed"], True)
        self.assertIs(p["generate_audio"], True)
        self.assertIs(p["watermark"], False)
        self.assertEqual(p["negative_prompt"], "ugly")
        # prompt text must be untouched (no --flag suffixes)
        self.assertEqual(p["content"][0]["text"], "hi")

    def test_first_last_frames_and_refs(self):
        p = srv._build_video_payload(
            model="m", prompt="hi", ratio=None, duration=None, resolution=None,
            seed=None, fps=None, camerafixed=None, generate_audio=None,
            watermark=None, negative_prompt=None,
            first_frame="http://a", last_frame="http://b",
            reference_images=["http://i"], reference_videos=["http://v"],
            reference_audios=["http://au"],
        )
        roles = [c.get("role") for c in p["content"][1:]]
        self.assertEqual(
            roles,
            ["first_frame", "last_frame", "reference_image",
             "reference_video", "reference_audio"],
        )
        self.assertEqual(p["content"][3]["type"], "image_url")
        self.assertEqual(p["content"][4]["type"], "video_url")
        self.assertEqual(p["content"][5]["type"], "audio_url")

    def test_duration_string_passthrough(self):
        p = srv._build_video_payload(
            model="m", prompt="hi", ratio=None, duration="adaptive",
            resolution=None, seed=None, fps=None, camerafixed=None,
            generate_audio=None, watermark=None, negative_prompt=None,
            first_frame=None, last_frame=None,
            reference_images=None, reference_videos=None, reference_audios=None,
        )
        self.assertEqual(p["duration"], "adaptive")


class HttpWrapperTests(unittest.TestCase):
    def test_network_error_envelope(self):
        import requests as r
        with mock.patch.object(
            r, "request", side_effect=r.ConnectionError("boom")
        ):
            out = srv._doubao_request("GET", "/x")
        self.assertFalse(out["_ok"])
        self.assertEqual(out["status_code"], 0)
        self.assertIn("network error", out["error"])

    def test_4xx_envelope(self):
        fake = mock.Mock(status_code=400, text="bad", content=b"bad")
        fake.json.return_value = {"err": "x"}
        with mock.patch("requests.request", return_value=fake):
            out = srv._doubao_request("POST", "/x", json_body={"a": 1})
        self.assertFalse(out["_ok"])
        self.assertEqual(out["status_code"], 400)
        self.assertEqual(out["error"], "bad")

    def test_dict_response_merged(self):
        fake = mock.Mock(status_code=200, content=b'{"id":"cgt-1"}')
        fake.json.return_value = {"id": "cgt-1"}
        with mock.patch("requests.request", return_value=fake):
            out = srv._doubao_request("GET", "/x")
        self.assertTrue(out["_ok"])
        self.assertEqual(out["id"], "cgt-1")
        self.assertEqual(out["_status_code"], 200)

    def test_list_response_wrapped(self):
        fake = mock.Mock(status_code=200, content=b"[1,2]")
        fake.json.return_value = [1, 2]
        with mock.patch("requests.request", return_value=fake):
            out = srv._doubao_request("GET", "/x")
        self.assertTrue(out["_ok"])
        self.assertEqual(out["data"], [1, 2])

    def test_empty_body(self):
        fake = mock.Mock(status_code=204, content=b"")
        with mock.patch("requests.request", return_value=fake):
            out = srv._doubao_request("DELETE", "/x")
        self.assertTrue(out["_ok"])

    def test_missing_api_key_envelope(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            out = srv._doubao_request("GET", "/x")
        self.assertFalse(out["_ok"])
        self.assertIn("API key", out["error"])


class WaitTaskTests(unittest.TestCase):
    def test_immediate_success_no_sleep(self):
        with mock.patch.object(
            srv, "_get_video_task_raw",
            return_value={"_ok": True, "status": "succeeded",
                          "content": {"video_url": "u"}, "usage": {}},
        ):
            t0 = time.time()
            out = srv._wait_video_task("tid", poll_interval=10, max_retries=3)
            elapsed = time.time() - t0
        self.assertTrue(out["success"])
        self.assertEqual(out["video_url"], "u")
        self.assertLess(elapsed, 1.0)

    def test_failed_status(self):
        with mock.patch.object(
            srv, "_get_video_task_raw",
            return_value={"_ok": True, "status": "failed", "error": "nope"},
        ):
            out = srv._wait_video_task("tid", poll_interval=0, max_retries=2)
        self.assertFalse(out["success"])
        self.assertEqual(out["status"], "failed")

    def test_query_error_short_circuits(self):
        with mock.patch.object(
            srv, "_get_video_task_raw",
            return_value={"_ok": False, "error": "boom"},
        ):
            out = srv._wait_video_task("tid", poll_interval=0, max_retries=5)
        self.assertFalse(out["success"])
        self.assertIn("boom", out["error"])

    def test_polls_then_succeeds(self):
        seq = [
            {"_ok": True, "status": "running"},
            {"_ok": True, "status": "running"},
            {"_ok": True, "status": "succeeded",
             "content": {"video_url": "ok"}, "usage": {}},
        ]
        calls = {"n": 0}

        def fake(_):
            i = calls["n"]
            calls["n"] += 1
            return seq[i]

        with mock.patch.object(srv, "_get_video_task_raw", side_effect=fake):
            out = srv._wait_video_task("tid", poll_interval=0, max_retries=5)
        self.assertTrue(out["success"])
        self.assertEqual(calls["n"], 3)

    def test_timeout_with_running_status(self):
        with mock.patch.object(
            srv, "_get_video_task_raw",
            return_value={"_ok": True, "status": "running"},
        ):
            out = srv._wait_video_task("tid", poll_interval=0, max_retries=2)
        self.assertFalse(out["success"])
        self.assertEqual(out["status"], "running")
        self.assertIn("超时", out["error"])


class HighLevelToolTests(unittest.TestCase):
    def test_text_to_video_creates_then_polls(self):
        with mock.patch.object(
            srv, "_create_video_task_raw",
            return_value={"_ok": True, "id": "cgt-9"},
        ), mock.patch.object(
            srv, "_get_video_task_raw",
            return_value={"_ok": True, "status": "succeeded",
                          "content": {"video_url": "v"}, "usage": {}},
        ):
            out = srv.text_to_video(
                prompt="hi", duration=5, ratio="16:9",
                poll_interval=0, poll_max_retries=2,
            )
        self.assertTrue(out["success"])
        self.assertEqual(out["task_id"], "cgt-9")
        self.assertEqual(out["video_url"], "v")

    def test_text_to_video_create_failure(self):
        with mock.patch.object(
            srv, "_create_video_task_raw",
            return_value={"_ok": False, "error": "nope"},
        ):
            out = srv.text_to_video(prompt="hi", poll_interval=0, poll_max_retries=1)
        self.assertFalse(out["success"])
        self.assertIn("创建任务失败", out["error"])

    def test_image_to_video_requires_image(self):
        out = srv.image_to_video(prompt="hi", poll_interval=0, poll_max_retries=1)
        self.assertFalse(out["success"])
        self.assertIn("image_url", out["error"])

    def test_image_to_video_independent_mime(self):
        captured = {}

        def capture(payload):
            captured["payload"] = payload
            return {"_ok": True, "id": "t1"}

        with mock.patch.object(
            srv, "_create_video_task_raw", side_effect=capture
        ), mock.patch.object(
            srv, "_get_video_task_raw",
            return_value={"_ok": True, "status": "succeeded",
                          "content": {"video_url": "v"}, "usage": {}},
        ):
            out = srv.image_to_video(
                prompt="p",
                image_base64="AAA", image_mime="image/png",
                last_frame_base64="BBB", last_frame_mime="image/webp",
                poll_interval=0, poll_max_retries=2,
            )
        self.assertTrue(out["success"])
        parts = captured["payload"]["content"]
        first = next(c for c in parts if c.get("role") == "first_frame")
        last = next(c for c in parts if c.get("role") == "last_frame")
        self.assertIn("image/png", first["image_url"]["url"])
        self.assertIn("image/webp", last["image_url"]["url"])

    def test_create_video_task_returns_id(self):
        with mock.patch.object(
            srv, "_create_video_task_raw",
            return_value={"_ok": True, "id": "cgt-x", "status": "queued"},
        ):
            out = srv.create_video_task(prompt="p")
        self.assertTrue(out["success"])
        self.assertEqual(out["task_id"], "cgt-x")
        self.assertNotIn("_ok", out["raw"])

    def test_create_video_task_no_id(self):
        with mock.patch.object(
            srv, "_create_video_task_raw",
            return_value={"_ok": True, "weird": "shape"},
        ):
            out = srv.create_video_task(prompt="p")
        self.assertFalse(out["success"])
        self.assertIn("任务ID", out["error"])

    def test_get_video_task(self):
        with mock.patch.object(
            srv, "_get_video_task_raw",
            return_value={"_ok": True, "status": "running",
                          "content": None, "usage": None},
        ):
            out = srv.get_video_task("tid")
        self.assertTrue(out["success"])
        self.assertEqual(out["status"], "running")

    def test_list_video_tasks_filters(self):
        captured = {}

        def fake(method, path, *, params=None, json_body=None, timeout=60):
            captured["params"] = params
            return {"_ok": True, "items": []}

        with mock.patch.object(srv, "_doubao_request", side_effect=fake):
            out = srv.list_video_tasks(
                page_num=2, page_size=5, status="succeeded",
                task_ids=["a", "b"], model="m",
            )
        self.assertTrue(out["success"])
        self.assertEqual(captured["params"]["page_num"], 2)
        self.assertEqual(captured["params"]["filter.status"], "succeeded")
        self.assertEqual(captured["params"]["filter.task_ids"], "a,b")
        self.assertEqual(captured["params"]["filter.model"], "m")

    def test_cancel_video_task_uses_post_first(self):
        calls = []

        def fake(method, path, **kw):
            calls.append((method, path))
            return {"_ok": True}

        with mock.patch.object(srv, "_doubao_request", side_effect=fake):
            out = srv.cancel_video_task("tid")
        self.assertTrue(out["success"])
        self.assertEqual(out["method"], "cancel")
        self.assertEqual(calls[0], ("POST", "/contents/generations/tasks/tid/cancel"))

    def test_cancel_video_task_falls_back_to_delete(self):
        responses = [
            {"_ok": False, "error": "no cancel"},
            {"_ok": True},
        ]

        def fake(method, path, **kw):
            return responses.pop(0)

        with mock.patch.object(srv, "_doubao_request", side_effect=fake):
            out = srv.cancel_video_task("tid")
        self.assertTrue(out["success"])
        self.assertEqual(out["method"], "delete")

    def test_cancel_video_task_both_fail(self):
        with mock.patch.object(
            srv, "_doubao_request",
            return_value={"_ok": False, "error": "nope"},
        ):
            out = srv.cancel_video_task("tid")
        self.assertFalse(out["success"])
        self.assertEqual(out["error"], "nope")


class EncodeImageTests(unittest.TestCase):
    def test_encode_existing(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\nfake")
            tmp = f.name
        try:
            out = srv.encode_image_to_base64(tmp)
            self.assertTrue(out["success"])
            self.assertTrue(out["data_url"].startswith("data:image/png;base64,"))
        finally:
            os.unlink(tmp)

    def test_encode_missing(self):
        out = srv.encode_image_to_base64("/no/such/file.png")
        self.assertFalse(out["success"])


class ResourceTests(unittest.TestCase):
    def test_settings_resource(self):
        text = srv.get_server_settings()
        self.assertIn("base_url", text)
        self.assertIn("api_key_set", text)

    def test_models_resource(self):
        text = srv.get_available_models()
        self.assertIn("text_to_image", text)
        self.assertIn("text_to_video", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

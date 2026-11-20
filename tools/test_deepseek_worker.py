"""Offline checks for the development worker's data and failure boundaries."""

from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import deepseek_worker as worker
from collaboration_mode import start_mode


class WorkerTests(unittest.TestCase):
    def test_environment_overrides_local_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env.deepseek.local"
            env.write_text("DEEPSEEK_API_KEY=local-test\nDEEPSEEK_MODEL=deepseek-flash\n", encoding="utf-8")
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "environment-test"}, clear=True):
                config = worker.load_config(env)
            self.assertEqual(config["DEEPSEEK_API_KEY"], "environment-test")
            self.assertEqual(config["DEEPSEEK_MODEL"], "deepseek-flash")

    def test_task_cannot_send_configured_or_other_api_keys(self):
        for text in ("example configured-secret", "example sk-" + "a" * 32):
            with self.subTest(text=text):
                with self.assertRaises(worker.WorkerError):
                    worker.build_messages(text, "", "configured-secret")

    def test_context_cannot_escape_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repo"
            root.mkdir()
            (base / "outside.py").write_text("secret", encoding="utf-8")
            with self.assertRaises(worker.WorkerError):
                worker.read_context(["../outside.py"], root)

    def test_untracked_context_and_env_files_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("print('hello')", encoding="utf-8")
            (root / ".env.py").write_text("secret", encoding="utf-8")
            with patch.object(worker.subprocess, "run") as run:
                run.return_value.returncode = 1
                with self.assertRaises(worker.WorkerError):
                    worker.read_context(["source.py"], root)
                with self.assertRaises(worker.WorkerError):
                    worker.read_context([".env.py"], root)

    def test_request_preserves_model_limit_and_usage(self):
        response = {"model": "deepseek-flash", "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}], "usage": {"total_tokens": 17}}
        with patch.object(worker.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = BytesIO(json.dumps(response).encode())
            result = worker.call_deepseek("test-key", "deepseek-flash", [], 100, 5)
            req = urlopen.call_args.args[0]
            payload = json.loads(req.data)
            self.assertEqual(req.full_url, worker.ENDPOINT)
            self.assertEqual(payload["model"], "deepseek-flash")
            self.assertEqual(payload["max_tokens"], 100)
            self.assertEqual(payload["thinking"], {"type": "disabled"})
            self.assertEqual(result["usage"]["total_tokens"], 17)
            self.assertTrue(result["ok"])

    def test_truncated_completion_is_not_success(self):
        response = {"choices": [{"message": {"content": "partial diff"}, "finish_reason": "length"}]}
        with patch.object(worker.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = BytesIO(json.dumps(response).encode())
            result = worker.call_deepseek("test-key", "deepseek-flash", [], 10, 5)
        self.assertFalse(result["ok"])

    def test_http_error_does_not_expose_body_or_retry(self):
        exc = HTTPError(worker.ENDPOINT, 401, "rejected", {}, BytesIO(b"private request data"))
        with patch.object(worker.request, "urlopen", side_effect=exc) as urlopen:
            with self.assertRaises(worker.WorkerError) as caught:
                worker.call_deepseek("test-key", "deepseek-flash", [], 10, 5)
            self.assertIn("401", str(caught.exception))
            self.assertNotIn("private", str(caught.exception))
            self.assertEqual(urlopen.call_count, 1)

    def test_stdout_and_receipt_redact_provider_key_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            start_mode("offline test", directory)
            result = {"ok": True, "content": "configured-secret sk-" + "b" * 32}
            with patch.object(worker, "RUN_DIR", Path(directory)), patch.object(worker, "load_config", return_value={"DEEPSEEK_API_KEY": "configured-secret"}), patch.object(worker, "call_deepseek", return_value=result), patch("sys.stdout", new_callable=StringIO) as output:
                code = worker.main(["--task", "hello"])
            receipt = next(Path(directory).glob("*.json")).read_text(encoding="utf-8")
            self.assertEqual(code, 0)
            for text in (output.getvalue(), receipt):
                self.assertNotIn("configured-secret", text)
                self.assertNotIn("sk-" + "b" * 32, text)
                self.assertIn("[REDACTED]", text)


if __name__ == "__main__":
    unittest.main()

"""Checks for truthful state, local trace redaction and the read-only server."""

from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib import error, request
from unittest.mock import patch

import collaboration_dashboard as dashboard
from collaboration_trace import TraceRecorder, append_action, list_runs, read_run
from collaboration_mode import start_mode
import deepseek_worker as worker


class TraceTests(unittest.TestCase):
    def test_success_is_pending_review_and_receipts_are_not_double_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = TraceRecorder(directory, "Review a script", {"model": "deepseek-flash"})
            trace.finish({"ok": True, "content": "review", "usage": {"total_tokens": 12}})
            receipt = {"ok": True, "timestamp_utc": trace.data["created_at"], "run_id": trace.data["id"]}
            (Path(directory) / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            runs = list_runs(directory)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["state"], "awaiting_review")
            self.assertEqual(dashboard.snapshot(directory)["runs"][0]["usage"]["total_tokens"], 12)

    def test_completion_requires_review_changes_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = TraceRecorder(directory, "read-only review", {})
            trace.finish({"ok": True, "content": "analysis"})
            run_id = trace.data["id"]
            with self.assertRaises(ValueError):
                append_action(run_id, "complete", "done", run_dir=directory)
            for kind, outcome in [("review", "rejected"), ("changes", "not_applicable"), ("validation", "passed")]:
                append_action(run_id, kind, "evidence", outcome=outcome, run_dir=directory)
            with self.assertRaises(ValueError):
                append_action(run_id, "complete", "done", run_dir=directory)
            append_action(run_id, "review", "accepted after review", outcome="accepted", run_dir=directory)
            result = append_action(run_id, "complete", "review completed", run_dir=directory)
            self.assertEqual(result["state"], "completed")

    def test_legacy_history_does_not_invent_prompts_or_review(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = {"ok": True, "timestamp_utc": "2026-11-20T09:00:00+00:00", "content": "hello"}
            (Path(directory) / "old.json").write_text(json.dumps(receipt), encoding="utf-8")
            run = list_runs(directory)[0]
            self.assertIsNone(run["request"])
            self.assertEqual(run["state"], "historical")
            self.assertEqual([event["kind"] for event in run["events"]], ["response"])
            with self.assertRaises(ValueError):
                append_action(run["id"], "review", "invented", run_dir=directory)

    def test_unrelated_passing_validation_does_not_hide_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = TraceRecorder(directory, "review", {})
            trace.finish({"ok": True, "content": "analysis"})
            run_id = trace.data["id"]
            append_action(run_id, "review", "reviewed", outcome="accepted", run_dir=directory)
            append_action(run_id, "changes", "no changes", outcome="not_applicable", run_dir=directory)
            append_action(run_id, "validation", "analyze", outcome="failed", run_dir=directory, command=["analyze"])
            append_action(run_id, "validation", "test", outcome="passed", run_dir=directory, command=["test"])
            with self.assertRaises(ValueError):
                append_action(run_id, "complete", "done", run_dir=directory)
            append_action(run_id, "validation", "analyze", outcome="passed", run_dir=directory, command=["analyze"])
            self.assertEqual(append_action(run_id, "complete", "done", run_dir=directory)["state"], "completed")

    def test_worker_records_actual_handoff_and_redacts_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            start_mode("offline test", directory)
            response = {"choices": [{"message": {"content": "configured-secret"}, "finish_reason": "stop"}]}
            with patch.object(worker, "RUN_DIR", Path(directory)), patch.object(worker, "load_config", return_value={"DEEPSEEK_API_KEY": "configured-secret"}), patch.object(worker.request, "urlopen") as urlopen, patch("sys.stdout"):
                urlopen.return_value.__enter__.return_value = BytesIO(json.dumps(response).encode())
                self.assertEqual(worker.main(["--task", "Review this", "--max-tokens", "50"]), 0)
            run = list_runs(directory)[0]
            self.assertEqual(run["request"]["messages"][1]["content"], "Review this")
            self.assertEqual([event["kind"] for event in run["events"]], ["handoff", "request", "response"])
            self.assertNotIn("configured-secret", json.dumps(run))
            self.assertEqual(run["result"]["content"], "[REDACTED]")

    def test_failed_model_call_cannot_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = TraceRecorder(directory, "failed task", {})
            trace.finish({"ok": False, "error": "timeout"})
            self.assertEqual(read_run(directory, trace.data["id"])["state"], "failed")
            with self.assertRaises(ValueError):
                read_run(directory, "../.env.deepseek.local")


class ServerTests(unittest.TestCase):
    def test_read_only_routes_and_no_secret_disclosure(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = TraceRecorder(directory, "configured-secret", {}, secret="configured-secret")
            trace.finish({"ok": True, "content": "done"})
            handler = type("TestHandler", (dashboard.DashboardHandler,), {"run_dir": Path(directory), "secret": "configured-secret"})
            server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                for route in ("/", "/dashboard.js", "/dashboard.css", "/api/runs", "/api/runs/" + trace.data["id"]):
                    with request.urlopen(base + route) as response:
                        payload = response.read().decode("utf-8")
                        self.assertNotIn("configured-secret", payload)
                        self.assertEqual(response.headers["Cache-Control"], "no-store")
                for route in ("/.env.deepseek.local", "/api/runs/../../.env.deepseek.local"):
                    with self.assertRaises(error.HTTPError) as caught:
                        request.urlopen(base + route)
                    self.assertEqual(caught.exception.code, 404)
                    caught.exception.close()
                with self.assertRaises(error.HTTPError) as caught:
                    request.urlopen(request.Request(base + "/api/runs", data=b"{}", method="POST"))
                self.assertEqual(caught.exception.code, 403)
                caught.exception.close()
                with self.assertRaises(error.HTTPError) as caught:
                    request.urlopen(request.Request(base + "/", headers={"Host": "untrusted.example"}))
                self.assertEqual(caught.exception.code, 403)
                caught.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()

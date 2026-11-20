from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib import error, request

import collaboration_dashboard as dashboard
from collaboration_mode import read_mode, start_mode, end_mode, require_active, _save
from collaboration_progress import progress
from collaboration_trace import TraceRecorder, append_action
import deepseek_worker as worker


class ModeTests(unittest.TestCase):
    def test_default_is_off_and_worker_never_calls_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(read_mode(directory)["status"], "off")
            with patch.object(worker, "RUN_DIR", Path(directory)), patch.object(worker, "call_deepseek") as call, patch("sys.stdout"):
                self.assertEqual(worker.main(["--task", "must not send"]), 1)
                call.assert_not_called()

    def test_task_start_end_and_distinct_session_history(self):
        with tempfile.TemporaryDirectory() as directory:
            first = start_mode("task one", directory)["current"]
            self.assertEqual(require_active(directory)["id"], first["id"])
            with self.assertRaises(ValueError):
                start_mode("task two", directory)
            end_mode("user stopped", directory)
            with self.assertRaises(ValueError):
                require_active(directory)
            second = start_mode("task two", directory)
            self.assertNotEqual(second["current"]["id"], first["id"])
            self.assertEqual(second["history"][-1]["end_reason"], "user stopped")
            with self.assertRaises(ValueError):
                end_mode("stale browser", directory, expected_id=first["id"])
            self.assertEqual(read_mode(directory)["status"], "active")

    def test_expiry_blocks_provider_and_corrupt_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            state = start_mode("task", directory)
            state["current"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            _save(state, directory)
            with self.assertRaises(ValueError):
                require_active(directory)
            self.assertEqual(read_mode(directory)["status"], "off")
            (Path(directory) / "control/mode.json").write_text("broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                require_active(directory)

    def test_ending_mode_does_not_fake_completion_of_inflight_call(self):
        with tempfile.TemporaryDirectory() as directory:
            session = start_mode("task", directory)["current"]
            trace = TraceRecorder(directory, "analysis", {"session_id": session["id"]})
            end_mode("user stopped", directory)
            trace.finish({"ok": True, "content": "late response"})
            self.assertEqual(trace.data["state"], "awaiting_review")
            self.assertEqual(read_mode(directory)["status"], "off")

    def test_final_acceptance_closes_only_associated_task_session(self):
        import collaboration_trace as trace_module
        with tempfile.TemporaryDirectory() as directory:
            session = start_mode("scoped task", directory)["current"]
            trace = TraceRecorder(directory, "review", {"session_id":session["id"]})
            trace.finish({"ok": True, "content": "answer"})
            for kind,outcome in [("review","accepted"),("changes","not_applicable"),("validation","not_applicable")]:
                append_action(trace.data["id"],kind,"evidence",outcome=outcome,run_dir=directory)
            argv = ["trace","--run",trace.data["id"],"--kind","complete","--summary","accepted","--end-session"]
            with patch.object(trace_module,"RUN_DIR",Path(directory)),patch.object(worker,"load_config",return_value={"DEEPSEEK_API_KEY":"test"}),patch("sys.argv",argv),patch("sys.stdout"):
                self.assertEqual(trace_module.main(),0)
            self.assertEqual(read_mode(directory)["status"],"off")
            self.assertIn("验收完成",read_mode(directory)["current"]["end_reason"])

    def test_model_wait_and_running_validation_have_observable_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = TraceRecorder(directory, "review", {})
            trace.event("request", "worker", "sent")
            state = progress(trace.data)
            self.assertEqual(state["active_index"], 2)
            self.assertEqual(state["resolved"], 2)
            self.assertIsNone(state["estimated_percentage"])
            trace.finish({"ok": True, "content": "answer"})
            self.assertEqual(progress(trace.data)["label"], "等待Codex 审查")
            data = append_action(trace.data["id"], "validation_started", "running tests", run_dir=directory)
            self.assertEqual(progress(data)["active_index"], 5)
            data = append_action(trace.data["id"], "validation", "tests passed", outcome="passed", run_dir=directory)
            self.assertIsNone(progress(data)["active_index"])

    def test_legacy_is_unknown_instead_of_finished(self):
        state = progress({"source": "legacy", "state": "historical", "result": {"ok": True}, "events": []})
        self.assertEqual(state["resolved"], 1)
        self.assertEqual(state["stages"][0]["state"], "unknown")

    def test_validation_start_is_written_before_command_finishes(self):
        import collaboration_trace as trace_module
        with tempfile.TemporaryDirectory() as directory:
            trace = TraceRecorder(directory, "test", {})
            trace.finish({"ok": True, "content": "answer"})
            def command(_command, **kwargs):
                from collaboration_trace import read_run
                pending = read_run(directory, trace.data["id"])
                self.assertEqual(pending["state"], "validating")
                self.assertEqual(progress(pending)["active_index"], 5)
                return __import__("subprocess").CompletedProcess(_command, 0, b"passed", b"")
            argv = ["trace", "--run", trace.data["id"], "--kind", "validation", "--summary", "check", "--command-json", '["test"]']
            with patch.object(trace_module, "RUN_DIR", Path(directory)), patch.object(trace_module, "append_action", side_effect=lambda *args, **kwargs: append_action(*args, **dict(kwargs, run_dir=directory))), patch.object(worker, "load_config", return_value={"DEEPSEEK_API_KEY": "test"}), patch.object(trace_module.subprocess, "run", side_effect=command), patch("sys.argv", argv), patch("sys.stdout"):
                self.assertEqual(trace_module.main(), 0)


class ModeServerTests(unittest.TestCase):
    def test_controls_require_same_origin_and_token_and_do_not_call_model(self):
        with tempfile.TemporaryDirectory() as directory:
            handler = type("ModeTestHandler", (dashboard.DashboardHandler,), {"run_dir": Path(directory)})
            server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with request.urlopen(base + "/api/mode") as response:
                    token = json.load(response)["control_token"]
                def post(route, body, origin=base, supplied_token=token):
                    return request.urlopen(request.Request(base+route, data=json.dumps(body).encode(), headers={"Origin": origin,"Content-Type":"application/json","X-Collaboration-Token":supplied_token},method="POST"))
                for origin, supplied_token in [("https://untrusted.example", token),(base,"wrong-token")]:
                    with self.assertRaises(error.HTTPError) as caught:
                        post("/api/mode/start", {"title":"task"},origin,supplied_token)
                    self.assertEqual(caught.exception.code,403)
                    caught.exception.close()
                with patch.object(worker,"call_deepseek") as call:
                    with post("/api/mode/start", {"title":"UI task"}) as response:
                        data = json.load(response)
                    self.assertEqual(data["mode"]["status"],"active")
                    with post("/api/mode/end", {"session_id":data["mode"]["current"]["id"]}) as response:
                        self.assertEqual(json.load(response)["mode"]["status"],"off")
                    call.assert_not_called()
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()

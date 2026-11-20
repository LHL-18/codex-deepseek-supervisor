"""Local, redacted records of observable Codex/DeepSeek handoffs."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "tools" / ".deepseek-runs"
ID_PATTERN = re.compile(r"[a-zA-Z0-9_-]{1,100}\Z")


def now():
    return datetime.now(timezone.utc).isoformat()


def sanitize(value, secret=""):
    if isinstance(value, str):
        if secret:
            value = value.replace(secret, "[REDACTED]")
        return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", value)
    if isinstance(value, dict):
        return {key: sanitize(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item, secret) for item in value]
    return value


def save_trace(run_dir, data, secret=""):
    folder = Path(run_dir) / "traces"
    folder.mkdir(parents=True, exist_ok=True)
    if not ID_PATTERN.fullmatch(data["id"]):
        raise ValueError("Invalid run ID")
    destination = folder / (data["id"] + ".json")
    temporary = destination.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(sanitize(data, secret), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


class TraceRecorder:
    def __init__(self, run_dir, task, request_data, secret=""):
        self.run_dir = Path(run_dir)
        self.secret = secret
        timestamp = now()
        self.data = {
            "id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8],
            "source": "trace", "created_at": timestamp, "updated_at": timestamp,
            "title": task.splitlines()[0].split("：", 1)[0][:72], "state": "running", "request": request_data,
            "events": [],
        }
        self.event("handoff", "Codex → 调用脚本", "任务与上下文已准备", task)

    def event(self, kind, actor, summary, detail="", **extra):
        self.data["events"].append({
            "kind": kind, "actor": actor, "summary": summary,
            "detail": detail, "timestamp": now(), **extra,
        })
        self.data["updated_at"] = now()
        save_trace(self.run_dir, self.data, self.secret)

    def finish(self, result):
        self.data["result"] = result
        self.data["state"] = "awaiting_review" if result.get("ok") else "failed"
        self.event("response" if result.get("ok") else "error", "DeepSeek / 调用脚本",
                   "DeepSeek 已返回，等待 Codex 审查" if result.get("ok") else "调用失败或输出不完整",
                   result.get("content", result.get("error", "")))


def read_run(run_dir, run_id):
    if not ID_PATTERN.fullmatch(run_id):
        raise ValueError("Invalid run ID")
    legacy = run_id.startswith("legacy-")
    filename = run_id.removeprefix("legacy-") if legacy else run_id
    path = Path(run_dir) / ("" if legacy else "traces") / (filename + ".json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if legacy:
        if "ok" not in data or "timestamp_utc" not in data:
            raise ValueError("Not a worker receipt")
        return {
            "id": run_id, "source": "legacy", "created_at": data["timestamp_utc"],
            "updated_at": data["timestamp_utc"], "state": "historical",
            "title": "连通性验证" if data.get("content", "").strip() == "DEEPSEEK_CONNECTED" else "历史源码审查调用",
            "request": None, "result": data,
            "events": [{"kind": "response", "actor": "DeepSeek", "summary": "历史 API 返回记录",
                        "detail": data.get("content", data.get("error", "")), "timestamp": data["timestamp_utc"]}],
        }
    return data


def list_runs(run_dir=RUN_DIR):
    runs = []
    folder = Path(run_dir)
    for path in (folder / "traces").glob("*.json"):
        try:
            runs.append(read_run(folder, path.stem))
        except (OSError, ValueError, KeyError):
            continue
    represented = {item["id"] for item in runs}
    for path in folder.glob("*.json"):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            if receipt.get("run_id") in represented:
                continue
            runs.append(read_run(folder, "legacy-" + path.stem))
        except (OSError, ValueError, KeyError):
            continue
    runs.sort(key=lambda item: item["created_at"], reverse=True)
    return runs


def append_action(run_id, kind, summary, detail="", outcome="recorded", run_dir=RUN_DIR, secret="", **extra):
    data = read_run(run_dir, run_id)
    if data.get("source") != "trace":
        raise ValueError("Legacy receipts cannot be given reconstructed events")
    if kind.endswith("_started"):
        data["state"] = {"review_started": "reviewing", "changes_started": "changing", "validation_started": "validating"}[kind]
    elif kind == "complete":
        kinds = {event["kind"] for event in data["events"]}
        if not {"review", "changes", "validation"}.issubset(kinds):
            raise ValueError("Record review, changes and validation before completing a run")
        if not data.get("result", {}).get("ok"):
            raise ValueError("Cannot complete a failed or incomplete API call")
        latest = {event["kind"]: event for event in data["events"]}
        if latest["review"].get("outcome") != "accepted":
            raise ValueError("The latest review must be accepted")
        validations = {}
        for event in data["events"]:
            if event["kind"] == "validation":
                validations[json.dumps(event.get("command", event["summary"]))] = event
        if any(event.get("outcome") not in {"passed", "not_applicable"} for event in validations.values()):
            raise ValueError("Every recorded validation must pass or be explicitly not applicable")
        if latest["changes"].get("outcome") not in {"applied", "not_applicable"}:
            raise ValueError("Record whether changes were applied")
        data["state"] = "completed"
    elif kind == "review" and outcome == "rejected":
        data["state"] = "needs_revision"
    elif kind == "review" and outcome == "accepted":
        data["state"] = "reviewed"
    elif kind == "validation" and outcome == "failed":
        data["state"] = "validation_failed"
    elif kind in {"validation", "changes"}:
        data["state"] = "reviewed"
    data["events"].append({"kind": kind, "actor": "Codex", "summary": summary,
                           "detail": detail, "outcome": outcome, "timestamp": now(), **extra})
    data["updated_at"] = now()
    save_trace(run_dir, data, secret)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--kind", required=True, choices=["review", "changes", "validation", "complete", "review_started", "changes_started"])
    parser.add_argument("--summary", required=True)
    parser.add_argument("--detail", default="")
    parser.add_argument("--outcome", default="recorded", choices=["recorded", "accepted", "rejected", "applied", "not_applicable"])
    parser.add_argument("--command-json", help="For validation: actual argv to execute, as a JSON list")
    parser.add_argument("--end-session", action="store_true", help="With complete: end this task's collaboration authorization")
    args = parser.parse_args()
    if args.end_session and args.kind != "complete":
        parser.error("end-session is only valid with complete")
    from deepseek_worker import load_config
    secret = load_config()["DEEPSEEK_API_KEY"]
    extra = {}
    if args.kind == "validation" and args.command_json:
        command = json.loads(args.command_json)
        if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
            parser.error("command-json must be a nonempty list of strings")
        append_action(args.run, "validation_started", args.summary, "验证命令正在运行，结果尚未产生。", run_dir=RUN_DIR, secret=secret, command=command)
        try:
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=180, check=False)
            args.detail = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")[-100_000:]
            args.outcome = "passed" if completed.returncode == 0 else "failed"
            extra = {"command": command, "exit_code": completed.returncode}
        except subprocess.TimeoutExpired:
            args.outcome, args.detail = "failed", "Validation exceeded 180 seconds."
            extra = {"command": command, "exit_code": None}
        except OSError:
            args.outcome, args.detail = "failed", "Validation command could not be started."
            extra = {"command": command, "exit_code": None}
    elif args.kind == "validation" and args.outcome != "not_applicable":
        parser.error("Use command-json to capture actual validation, or outcome not_applicable")
    data = append_action(args.run, args.kind, args.summary, args.detail, args.outcome, run_dir=RUN_DIR, secret=secret, **extra)
    if args.end_session:
        from collaboration_mode import end_mode
        session_id = (data.get("request") or {}).get("session_id")
        if not session_id:
            parser.error("This run is not associated with a task session")
        if any((run.get("request") or {}).get("session_id") == session_id and run["state"] != "completed" for run in list_runs(RUN_DIR)):
            parser.error("Finish or explicitly end remaining calls before ending this task session")
        end_mode("本次任务验收完成，协作模式自动关闭", run_dir=RUN_DIR, expected_id=session_id)
    print(json.dumps({"run_id": data["id"], "state": data["state"], "recorded": args.kind}, ensure_ascii=False))
    return 1 if args.outcome == "failed" else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

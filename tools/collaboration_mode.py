"""Task-scoped opt-in for DeepSeek calls; off unless explicitly started."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
import sys
import uuid

from collaboration_trace import RUN_DIR, now, sanitize

_LOCK = threading.RLock()


def read_mode(run_dir=RUN_DIR):
    path = Path(run_dir) / "control" / "mode.json"
    if not path.exists():
        return {"status": "off", "current": None, "history": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        session = data.get("current")
        if session and session["status"] == "active":
            if datetime.fromisoformat(session["expires_at"]) <= datetime.now(timezone.utc):
                session.update(status="ended", ended_at=session["expires_at"], end_reason="任务授权已超过 8 小时，请重新开启")
        data["status"] = "active" if session and session["status"] == "active" else "off"
        return data
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("协作模式记录无法读取；为避免误调用，请检查本地记录") from None


def _save(data, run_dir):
    folder = Path(run_dir) / "control"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "mode.json"
    temporary = target.with_suffix("." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(sanitize(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def start_mode(title, run_dir=RUN_DIR):
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
        raise ValueError("请填写 1～160 字的本次任务名称")
    with _LOCK:
        data = read_mode(run_dir)
        if data["status"] == "active":
            raise ValueError("已有协作任务，请先结束当前任务")
        if data["current"]:
            data["history"] = (data["history"] + [data["current"]])[-20:]
        data["current"] = {
            "id": "session-" + uuid.uuid4().hex[:12], "title": title.strip(), "status": "active",
            "started_at": now(), "expires_at": (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(),
        }
        data["status"] = "active"
        _save(data, run_dir)
        return data


def end_mode(reason="用户结束协作，恢复 Codex 独立处理", run_dir=RUN_DIR, expected_id=None):
    with _LOCK:
        data = read_mode(run_dir)
        if expected_id and (not data["current"] or data["current"]["id"] != expected_id):
            raise ValueError("任务已切换，不能结束另一个协作任务")
        if data["status"] != "active":
            return data
        data["current"].update(status="ended", ended_at=now(), end_reason=str(reason)[:300])
        data["status"] = "off"
        _save(data, run_dir)
        return data


def require_active(run_dir=RUN_DIR):
    data = read_mode(run_dir)
    if data["status"] != "active":
        raise ValueError("双模型协作未开启。请先为本次任务开启协作；当前不会调用 DeepSeek。")
    return data["current"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "start", "end"])
    parser.add_argument("--task", default="")
    parser.add_argument("--reason", default="本次任务结束，恢复 Codex 独立处理")
    args = parser.parse_args()
    try:
        data = start_mode(args.task) if args.action == "start" else end_mode(args.reason) if args.action == "end" else read_mode()
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except ValueError as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()

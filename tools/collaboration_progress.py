"""Derive visible milestones from evidence, without estimating model progress."""

KINDS = ["handoff", "request", "response", "review", "changes", "validation"]
NAMES = ["任务交接", "发送上下文", "模型回复", "Codex 审查", "代码变更", "执行验证"]


def progress(run):
    events = run.get("events", [])
    latest = {event["kind"]: event for event in events}
    checks = {}
    for event in events:
        if event["kind"] == "validation":
            checks[str(event.get("command", event["summary"]))] = event
    stages = []
    for index, kind in enumerate(KINDS):
        event = latest.get(kind)
        if kind == "response":
            state = "done" if run.get("result", {}).get("ok") else "failed" if "result" in run else "pending"
        elif kind == "validation" and any(check.get("outcome") == "failed" for check in checks.values()):
            state = "failed"
        elif event:
            outcome = event.get("outcome")
            state = "failed" if outcome in {"rejected", "failed"} else "skipped" if outcome == "not_applicable" else "done"
        else:
            state = "unknown" if run.get("source") == "legacy" else "pending"
        stages.append({"kind": kind, "name": NAMES[index], "state": state})
    active = None
    for event in reversed(events):
        if event["kind"] in {"review_started", "changes_started", "validation_started"}:
            kind = event["kind"].removesuffix("_started")
            position = events.index(event)
            if not any(item["kind"] == kind for item in events[position + 1:]):
                active = KINDS.index(kind)
            break
    if run.get("state") == "running" and "result" not in run:
        active = 2
    if run.get("state") == "completed":
        active = None
        label, owner = "本轮协作已完成", "Codex"
    elif active is not None:
        stages[active]["state"] = "active"
        label = "等待 DeepSeek 返回" if active == 2 else "正在" + NAMES[active]
        owner = "DeepSeek" if active == 2 else "Codex / 本机命令"
    elif run.get("source") == "legacy":
        label, owner = "历史记录 · 进度不可还原", "未记录"
    elif any(stage["state"] == "failed" for stage in stages):
        failed = next(stage for stage in stages if stage["state"] == "failed")
        label, owner = failed["name"] + "未通过 · 等待处理", "Codex"
    else:
        pending = next((index for index, stage in enumerate(stages) if stage["state"] == "pending"), None)
        label = "等待" + NAMES[pending] if pending is not None else "阶段已记录 · 等待任务验收"
        owner = "Codex"
    resolved = sum(stage["state"] in {"done", "skipped"} for stage in stages)
    started = next((event["timestamp"] for event in reversed(events) if event["kind"] == (KINDS[active] + "_started" if active is not None and active != 2 else "request")), None)
    return {"stages": stages, "resolved": resolved, "total": 6, "active_index": active,
            "label": label, "owner": owner, "active_since": started if active is not None else None,
            "estimated_percentage": None}

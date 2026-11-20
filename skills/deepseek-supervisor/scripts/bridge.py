"""Dispatch the skill's commands to the current project's existing local bridge."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

BRIDGE_FILES = ("collaboration_mode.py", "deepseek_worker.py", "collaboration_trace.py")


def find_project(project=None, cwd=None):
    origin = Path(project if project is not None else cwd or Path.cwd()).resolve()
    candidates = [origin] if project is not None else [origin, *origin.parents]
    for root in candidates:
        scripts = [root / "tools" / name for name in BRIDGE_FILES]
        if root.is_dir() and all(path.is_file() and path.resolve().is_relative_to(root) for path in scripts):
            return root
    raise ValueError("No project bridge found. Select a project containing tools/collaboration_mode.py, deepseek_worker.py and collaboration_trace.py. No other project was selected.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, help="Explicit repository root; no cross-project fallback")
    parser.add_argument("action", choices=["status", "start", "stop", "ask", "record"])
    parser.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded unchanged to the local bridge")
    args = parser.parse_args(argv)
    try:
        root = find_project(args.project)
    except ValueError as exc:
        parser.exit(2, str(exc) + "\n")
    if args.action in {"status", "start", "stop"}:
        filename = "collaboration_mode.py"
        forwarded = [{"status": "status", "start": "start", "stop": "end"}[args.action], *args.args]
    else:
        filename = "deepseek_worker.py" if args.action == "ask" else "collaboration_trace.py"
        forwarded = args.args
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        result = subprocess.run([sys.executable, str(root / "tools" / filename), *forwarded], cwd=root, env=env, check=False)
    except OSError:
        parser.exit(2, "Could not start the project's bridge command.\n")
    return result.returncode


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

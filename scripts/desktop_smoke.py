"""Exercise the desktop worker through its public JSONL protocol, without a GUI.

Use a local, authorized PPTX. Reports and source files are not committed or uploaded.
"""
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from slideguard.util import sha256_file


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--worker", type=Path, help="Test a packaged worker instead of Python source")
    args = parser.parse_args()
    source = args.input.resolve(strict=True)
    output = args.output.resolve(strict=True)
    if not output.is_dir():
        parser.error("output must be an existing directory")
    before = sha256_file(source)
    started = time.monotonic()
    command = [str(args.worker.resolve(strict=True))] if args.worker else [sys.executable, "-m", "slideguard.desktop.server"]
    child = subprocess.Popen(command,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8")
    replies = queue.Queue()
    diagnostics = []
    def receive():
        for line in child.stdout:
            replies.put(line)
        replies.put(None)
    def errors():
        for line in child.stderr:
            if len(diagnostics) < 100:
                diagnostics.append(line.rstrip())
    threading.Thread(target=receive, daemon=True).start()
    threading.Thread(target=errors, daemon=True).start()
    sequence = 0
    def call(method, **params):
        nonlocal sequence
        sequence += 1
        message = dict(version=1, id=str(sequence), method=method, params=params)
        child.stdin.write(json.dumps(message) + "\n")
        child.stdin.flush()
        line = replies.get(timeout=30)
        if line is None:
            raise RuntimeError("worker exited: " + "; ".join(diagnostics))
        value = json.loads(line)
        assert value["version"] == 1 and value["id"] == str(sequence)
        if not value["ok"]:
            raise RuntimeError(value["error"])
        return value["result"]
    def idle():
        previous = None
        while time.monotonic() - started < args.timeout:
            state = call("state")
            signature = state["busy"], state["status"]
            if signature != previous:
                print(json.dumps(dict(stage=signature[0], message=signature[1],
                    seconds=round(time.monotonic()-started, 1)), ensure_ascii=False), flush=True)
                previous = signature
            if not state["busy"]:
                return state
            time.sleep(.5)
        raise TimeoutError("desktop smoke deadline exceeded")
    try:
        call("output", path=str(output))
        call("open", path=str(source))
        state = idle()
        assert state["ready"] and state["sourceAsset"], state["status"]
        original = state["base"], state["margins"]
        call("edit", action="begin")
        call("edit", action="drag", handle="se", x=.85, y=.85)
        call("edit", action="drag", handle="se", x=.8, y=.8)
        call("edit", action="end")
        state = call("edit", action="undo")
        assert (state["base"], state["margins"]) == original
        call("edit", action="margin", value=2, edge=-1)
        state = call("edit", action="undo")
        assert (state["base"], state["margins"]) == original
        call("check")
        state = idle()
        assert state["check"] == "参数检查通过", state["status"]
        call("export")
        state = idle()
        assert state["verdict"] == "PASS" and state["resultCurrent"], state["status"]
        assert state["results"], "No result artifacts"
        call("verify")
        checked = idle()
        assert "PASS" in checked["status"], checked["status"]
        assert before == sha256_file(source), "Source changed"
        print(json.dumps(dict(status="passed", sourceUnchanged=True,
            seconds=round(time.monotonic()-started, 1), verdict=state["verdict"],
            artifacts=[{k:v for k,v in item.items() if k != "asset"} for item in state["results"]]),
            ensure_ascii=False), flush=True)
        call("close")
        child.stdin.close()
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


if __name__ == "__main__":
    main()

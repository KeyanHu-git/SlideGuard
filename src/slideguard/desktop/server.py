"""Versioned, local JSON-lines worker for the desktop host and trusted CLI callers.

The host owns native file dialogs. This worker never exposes a network listener.
Rendering stays in the existing pipeline; UI edits use the existing CropEditor.
"""
from __future__ import annotations

import base64
import copy
import io
import json
import math
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..cancellation import CancellationToken
from ..studio.editor import CropEditor
from ..util import default_work_root, sha256_file
from ..workspace import create_owned_workspace, delete_owned_workspace, mark_workspace_complete

MAX_REQUEST = 1024 * 1024
MAX_ASSET = 24 * 1024 * 1024
METHODS = {"state", "open", "page", "output", "edit", "check", "export", "cancel", "asset", "verify", "close"}


def strict_message(line: str) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("Non-finite JSON number")
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("Non-finite JSON number")
        return parsed
    value = json.loads(line, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)
    if not isinstance(value, dict) or set(value) != {"version", "id", "method", "params"}:
        raise ValueError("Expected version, id, method and params")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Unsupported desktop protocol version")
    if not isinstance(value["id"], str) or not 1 <= len(value["id"]) <= 80:
        raise ValueError("Invalid request ID")
    if value["method"] not in METHODS or not isinstance(value["params"], dict):
        raise ValueError("Unsupported method or params")
    return value


class DesktopSession:
    def __init__(self):
        self.lock = threading.RLock()
        self.editor = CropEditor()
        self.source: Path | None = None
        self.source_hash = ""
        self.slide = 1
        self.pages = 0
        self.output = default_work_root().parent / "desktop-output"
        self.status = "打开 PowerPoint 文件"
        self.check_status = "尚未检查"
        self.busy = ""
        self.started = 0.
        self.revision = 0
        self.thread: threading.Thread | None = None
        self.token: CancellationToken | None = None
        self.workspace = None
        self.assets: dict[str, Path] = {}
        self.source_asset = ""
        self.results: list[dict] = []
        self.result: dict | None = None
        self.result_request: dict | None = None
        self.saved_pages: dict[int, CropEditor] = {}

    def state(self):
        with self.lock:
            base = self.editor.base
            effective = self.editor.effective
            box = self.editor.pixel_box
            return {
                "revision": self.revision, "filename": self.source.name if self.source else "",
                "page": self.slide, "pages": self.pages, "ready": self.editor.ready,
                "busy": self.busy, "elapsed": round(time.monotonic()-self.started, 1) if self.busy else 0,
                "status": self.status, "check": self.check_status, "output": str(self.output),
                "sourceAsset": self.source_asset, "width": self.editor.width, "height": self.editor.height,
                "base": [base.left, base.top, base.right, base.bottom],
                "effective": [effective.left, effective.top, effective.right, effective.bottom],
                "cropSize": [box[2]-box[0], box[3]-box[1]], "mode": self.editor.crop.mode,
                "margins": list(self.editor.crop.expand_percent), "limit": self.editor.limit_mb,
                "canUndo": self.editor.history.can_undo, "canRedo": self.editor.history.can_redo,
                "results": copy.deepcopy(self.results),
                "verdict": self.result.get("verdict") if self.result else None,
                "resultCurrent": bool(self.result_request and self.source and self.editor.ready and
                    self.result_request == self.editor.request(self.source, self.slide, self.output)),
            }

    def register(self, path: Path):
        if path.suffix.lower() not in {".png", ".pdf"} or not path.is_file():
            raise ValueError("Unsupported preview asset")
        if len(self.assets) >= 64:
            self.assets.pop(next(iter(self.assets)))
        key = uuid.uuid4().hex
        self.assets[key] = path.resolve()
        return key

    def _start(self, kind, operation, finish):
        if self.busy:
            raise ValueError("A task is already running")
        self.busy, self.started = kind, time.monotonic()
        self.token = CancellationToken()
        self.revision += 1
        def run():
            try:
                value = operation()
                with self.lock:
                    finish(value)
            except Exception as exc:
                with self.lock:
                    self.status = f"操作未完成：{exc}"
                    if kind == "preview":
                        self.editor.ready = False
                        self.source_asset = ""
                    if kind == "check":
                        self.check_status = "检查未通过"
            finally:
                with self.lock:
                    self.busy = ""
                    self.token = None
                    self.revision += 1
        self.thread = threading.Thread(target=run, name=f"slideguard-{kind}")
        self.thread.start()

    def _preview(self):
        from ..crop import crop_pixels
        from ..ooxml import PptxPackage
        from ..powerpoint import preview_reference
        if self.workspace is None:
            self.workspace = create_owned_workspace(default_work_root(), prefix="desktop-preview",
                task_id="desktop-preview", kind="preview-workspace")
        source, slide = self.source, self.slide
        destination = self.workspace.path / uuid.uuid4().hex
        self.source_asset = ""
        self.editor.ready = False
        self.status = "正在生成 PowerPoint 原生参考图"
        self.check_status = "等待参考图"
        def operation():
            before = sha256_file(source)
            pages = PptxPackage.open(source).slide_count
            reference = preview_reference(source, slide, destination, preview_width=4000)
            path = Path(reference["referencePng"])
            box = crop_pixels(path, padding_px=0, crop_percent=None, expand_percent=(0.,)*4)
            if sha256_file(source) != before:
                raise ValueError("源稿已改变，请重新打开")
            return before, pages, path, box
        def finish(value):
            self.source_hash, self.pages, path, box = value
            self.editor.install_reference(box)
            self.source_asset = self.register(path)
            self.check_status = "尚未检查"
            self.status = "拖动边框裁剪，滚轮缩放，空格拖动画布"
        self._start("preview", operation, finish)

    def _progress(self, event):
        with self.lock:
            self.status = str(event.get("message", "处理中"))
            self.revision += 1

    def _export(self, dry_run):
        if not self.source or not self.editor.ready or self.editor._gesture is not None:
            raise ValueError("请先完成预览和裁剪操作")
        request = self.editor.request(self.source, self.slide, self.output, dry_run=dry_run)
        source, expected = self.source, self.source_hash
        self.status = "检查参数" if dry_run else "正在导出并验收"
        def operation():
            if sha256_file(source) != expected:
                raise ValueError("源稿在预览后改变，请重新打开")
            from ..application import ExportService
            return ExportService().execute(copy.deepcopy(request), base_dir=source.parent,
                event_sink=self._progress, cancel_token=self.token)
        def finish(value):
            if value["status"] == "failed" or value.get("exitCode", 1) != 0:
                raise ValueError((value.get("error") or {}).get("message", "导出未通过验收"))
            if dry_run:
                self.check_status = "参数检查通过"
                self.status = "参数有效，保真结果需导出后验收"
                return
            package = Path(value["output"]["packagePath"]).resolve()
            results = []
            for item in value["artifacts"]:
                path = (package / item["relativePath"]).resolve()
                if package not in path.parents:
                    raise ValueError("产物路径越界")
                entry = {"kind": item["kind"], "name": path.name, "bytes": item["bytes"],
                    "asset": self.register(path) if path.suffix.lower() in {".png", ".pdf"} else ""}
                if path.suffix.lower() == ".png":
                    from PIL import Image
                    with Image.open(path) as image:
                        entry.update(width=image.width, height=image.height)
                results.append(entry)
            self.results, self.result, self.result_request = results, value, copy.deepcopy(request)
            self.status = "导出完成，结果已按当前规则验收"
        self._start("check" if dry_run else "export", operation, finish)

    def asset(self, params):
        from PIL import Image
        key = params.get("id")
        if key not in self.assets:
            raise ValueError("Unknown session asset")
        path = self.assets[key]
        if path.stat().st_size > MAX_ASSET:
            raise ValueError("Preview asset exceeds 24 MiB")
        if path.suffix.lower() == ".pdf":
            return {"mime": "application/pdf", "data": base64.b64encode(path.read_bytes()).decode()}
        width = params.get("width", 1600)
        if isinstance(width, bool) or not isinstance(width, int) or not 128 <= width <= 4096:
            raise ValueError("Preview width must be 128..4096")
        with Image.open(path) as img:
            img.load()
            original = img.size
            img.thumbnail((width, 4096), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            img.save(output, format="PNG")
            return {"mime": "image/png", "data": base64.b64encode(output.getvalue()).decode(),
                    "width": original[0], "height": original[1]}

    def dispatch(self, method, params):
        with self.lock:
            if method == "state":
                return self.state()
            if method == "asset":
                return self.asset(params)
            if method == "cancel":
                if self.busy in {"check", "export"} and self.token:
                    self.token.cancel()
                    self.status = "正在安全取消"
                    self.revision += 1
                return self.state()
            if self.busy:
                raise ValueError("任务运行中，设置已锁定")
            if self.editor._gesture is not None and method not in {"edit"}:
                raise ValueError("请先结束或取消当前拖动")
            if method == "open":
                path = Path(params["path"]).resolve()
                if not path.is_file() or path.suffix.lower() != ".pptx":
                    raise ValueError("请选择 PPTX 文件")
                self.source = path
                self.editor = CropEditor()
                self.saved_pages.clear()
                self.assets.clear()
                self.results, self.result, self.result_request = [], None, None
                self.slide, self.pages = 1, 0
                self._preview()
            elif method == "page":
                page = params["page"]
                if type(page) is not int or not 1 <= page <= self.pages:
                    raise ValueError("Invalid page")
                if page != self.slide:
                    self.saved_pages[self.slide] = self.editor
                    self.slide = page
                    self.editor = self.saved_pages.get(page, CropEditor())
                    self._preview()
            elif method == "output":
                output = Path(params["path"]).resolve()
                if not output.is_dir():
                    raise ValueError("请选择已有文件夹")
                self.output = output
                self.check_status = "保存位置已改变"
            elif method == "edit":
                if not self.editor.ready:
                    raise ValueError("Reference is not ready")
                action, value = params["action"], params.get("value")
                if action == "mode": self.editor.mode(value)
                elif action == "margin": self.editor.margin(params.get("edge", -1), value)
                elif action == "budget": self.editor.budget(value)
                elif action == "bounds": self.editor.bounds(tuple(value))
                elif action == "resize": self.editor.resize(params["handle"], params["x"], params["y"])
                elif action == "move": self.editor.move(params["dx"], params["dy"])
                elif action == "drag":
                    from ..geometry import NormalizedRect, move_normalized_rect, resize_normalized_rect
                    initial = self.editor._gesture
                    if initial is None:
                        raise ValueError("No active gesture")
                    base = self.editor.auto_rect if initial.crop.mode == "auto" else NormalizedRect.from_percent(initial.crop.bounds_percent)
                    if params["handle"] == "move":
                        rect = move_normalized_rect(base, params["dx"], params["dy"])
                    else:
                        rect = resize_normalized_rect(base, params["handle"], params["x"], params["y"],
                            reference_width=self.editor.width, reference_height=self.editor.height)
                    self.editor.bounds(rect.to_percent())
                elif action == "undo": self.editor.undo(False)
                elif action == "redo": self.editor.undo(True)
                elif action == "begin": self.editor.begin()
                elif action == "end": self.editor.end(bool(params.get("cancel", False)))
                else: raise ValueError("Unknown edit action")
                self.check_status = "设置已改变，尚未检查"
            elif method in {"check", "export"}:
                self._export(method == "check")
            elif method == "verify":
                if not self.result:
                    raise ValueError("尚无导出结果")
                from ..verify import verify_package
                manifest = Path(self.result["output"]["packagePath"]) / "manifest.json"
                def finish(value):
                    self.status = f"文件完整性：{value[0].value} · {len(value[1])}项（不替代保真验收）"
                self._start("verify", lambda: verify_package(manifest), finish)
            elif method == "close":
                if self.workspace and mark_workspace_complete(self.workspace):
                    delete_owned_workspace(self.workspace)
                    self.workspace = None
            else:
                raise ValueError("Unsupported method")
            self.revision += 1
            return self.state()


def serve(reader, writer):
    session = DesktopSession()
    while True:
        line = reader.readline(MAX_REQUEST + 1)
        if not line:
            break
        request_id = None
        try:
            if len(line) > MAX_REQUEST:
                # Drain the rest of this line; it must never become another request.
                while not line.endswith("\n"):
                    line = reader.readline(MAX_REQUEST + 1)
                    if not line:
                        break
                raise ValueError("Request exceeds 1 MiB")
            message = strict_message(line)
            request_id = message["id"]
            result = session.dispatch(message["method"], message["params"])
            response = {"version": 1, "id": request_id, "ok": True, "result": result}
        except Exception as exc:
            response = {"version": 1, "id": request_id, "ok": False,
                        "error": {"code": "DESKTOP_REQUEST_FAILED", "message": str(exc)}}
        writer.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n")
        writer.flush()
    if session.thread:
        session.thread.join()
    session.dispatch("close", {})


def main():
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()

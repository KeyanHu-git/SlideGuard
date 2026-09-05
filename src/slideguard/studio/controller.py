from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Property, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QImage
from PySide6.QtWidgets import QFileDialog

from ..cancellation import CancellationToken
from ..crop import crop_pixels
from ..ooxml import PptxPackage
from ..powerpoint import preview_reference
from ..util import default_work_root, sha256_file
from ..verify import verify_package
from ..workspace import create_owned_workspace, delete_owned_workspace, mark_workspace_complete
from .editor import CropEditor
from .rendering import LocalPreviewProvider


class Task(QObject):
    finished = Signal(object)
    progress = Signal(object)

    def __init__(self, operation: Callable) -> None:
        super().__init__()
        self.operation = operation

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit({"ok": True, "value": self.operation(self.progress.emit)})
        except Exception as exc:
            self.finished.emit({"ok": False, "error": str(exc)})


class StudioController(QObject):
    changed = Signal()

    def __init__(self, provider: LocalPreviewProvider) -> None:
        super().__init__()
        self.provider = provider
        self.editor = CropEditor()
        self.source: Path | None = None
        self.source_hash = ""
        self.slide = 1
        self.pages = 0
        self.output = default_work_root().parent / "studio-output"
        self.preview_url = ""
        self.view_url = ""
        self.view_kind = "source"
        self.view_width, self.view_height = 4000, 2250
        self.status = "打开 PPTX，先看清边界，再导出"
        self.check_status = "尚未检查参数"
        self.result_summary = "尚未导出。预览不代表最终保真验收。"
        self.results: dict[str, Path] = {}
        self.result_package: Path | None = None
        self.result_request: dict | None = None
        self.active_request: dict | None = None
        self._busy = ""
        self._started = 0.
        self._elapsed = 0
        self._task: Task | None = None
        self._thread: QThread | None = None
        self._pending: dict | None = None
        self._preview_workspace = None
        self._token: CancellationToken | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    @Property("QVariantMap", notify=changed)
    def state(self) -> dict:
        base = self.editor.base
        effective = self.editor.effective
        box = self.editor.pixel_box
        return {
            "source": str(self.source) if self.source else "",
            "filename": self.source.name if self.source else "未选择文件",
            "page": self.slide, "pages": self.pages, "output": str(self.output),
            "ready": self.editor.ready, "busy": bool(self._busy), "operation": self._busy,
            "status": self.status, "elapsed": self._elapsed,
            "previewUrl": self.view_url, "viewKind": self.view_kind,
            "imageWidth": self.view_width, "imageHeight": self.view_height,
            "mode": self.editor.crop.mode,
            "bounds": list(self.editor.crop.bounds_percent),
            "base": [base.left, base.top, base.right, base.bottom],
            "effective": [effective.left, effective.top, effective.right, effective.bottom],
            "margins": list(self.editor.crop.expand_percent),
            "limit": self.editor.limit_mb,
            "cropSize": f"{box[2] - box[0]} × {box[3] - box[1]} 参考像素",
            "checkStatus": self.check_status, "resultSummary": self.result_summary,
            "hasResult": self.result_package is not None,
            "canUndo": self.editor.history.can_undo, "canRedo": self.editor.history.can_redo,
        }

    def _tick(self) -> None:
        self._elapsed = int(time.monotonic() - self._started)
        self.changed.emit()

    def _start(self, kind: str, operation: Callable) -> None:
        if self._busy:
            return
        self._busy, self._started, self._elapsed = kind, time.monotonic(), 0
        self._pending = None
        self._thread = QThread(self)
        self._task = Task(operation)
        self._task.moveToThread(self._thread)
        self._thread.started.connect(self._task.run)
        self._task.progress.connect(self._progress)
        self._task.finished.connect(self._receive)
        self._task.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._task.deleteLater)
        self._thread.finished.connect(self._finish)
        self._timer.start()
        self._thread.start()
        self.changed.emit()

    @Slot(object)
    def _receive(self, result: dict) -> None:
        self._pending = result

    @Slot()
    def _finish(self) -> None:
        kind = self._busy
        result = self._pending or {"ok": False, "error": "后台任务未返回结果"}
        self._timer.stop()
        self._tick()
        if self._thread:
            self._thread.deleteLater()
        self._thread = self._task = None
        self._busy = ""
        try:
            if not result["ok"]:
                raise ValueError(result["error"])
            value = result["value"]
            if kind == "preview":
                self.source = Path(value["source"])
                self.source_hash = value["hash"]
                self.pages = value["pages"]
                self.editor.install_reference(value["box"])
                self.preview_url = self.provider.register(Path(value["path"]))
                self.view_url, self.view_kind = self.preview_url, "source"
                self.view_width, self.view_height = self.editor.width, self.editor.height
                self.status = "参考图已就绪 · 紫框为裁剪范围，绿框为最终输出范围"
            elif kind == "check":
                if value["status"] != "validated":
                    raise ValueError((value.get("error") or {}).get("message", "参数检查失败"))
                self.check_status = "参数检查通过 · 最终保真仍需导出验收"
                self.status = self.check_status
            elif kind == "export":
                if value["status"] != "succeeded":
                    error = value.get("error") or {}
                    raise ValueError(f"{error.get('code', 'ERROR')}：{error.get('message', '导出失败')}")
                self.result_request = copy.deepcopy(self.active_request)
                self._load_result(Path(value["output"]["packagePath"]))
            elif kind == "verify":
                self.status = f"上次产物完整性复核：{value[0]} · {value[1]} 项；不替代保真QA"
        except Exception as exc:
            self.status = f"操作未完成：{exc}"
            if kind == "preview":
                self.editor.ready = False
                self.preview_url = self.view_url = ""
            if kind == "check":
                self.check_status = self.status
        self._token = None
        self.changed.emit()

    @Slot(object)
    def _progress(self, event: dict) -> None:
        phase = str(event.get("phase", ""))
        messages = {"validation": "检查输入与配置", "environment": "检查本机渲染环境",
                    "slide": "处理页面", "powerpoint": "原生排版已完成", "pdf": "PDF处理已完成",
                    "svg": "SVG处理完成，继续进行多尺度验收", "export": "导出与自动验收",
                    "publication": "发布验收结果"}
        self.status = messages.get(phase, "后台任务进行中") + " · 运行设置已锁定"
        self.changed.emit()

    @Slot()
    def chooseFile(self) -> None:
        if self._busy:
            return
        filename, _ = QFileDialog.getOpenFileName(None, "打开 PowerPoint", "", "PowerPoint (*.pptx)")
        if filename:
            self.loadSource(filename)

    @Slot(str)
    def loadSource(self, filename: str) -> None:
        if self._busy:
            return
        self.source = Path(filename).resolve()
        self.source_hash = ""
        self.slide, self.pages = 1, 0
        self.editor = CropEditor()
        self.result_package = None
        self.results = {}
        self.result_summary = "尚未导出。预览不代表最终保真验收。"
        self._preview()

    def _preview(self) -> None:
        self.preview_url = self.view_url = ""
        self.view_kind = "source"
        self.editor.ready = False
        self.check_status = "等待参考图"
        self.status = "正在生成4000像素原生参考图与自动边界"
        if self._preview_workspace is None:
            self._preview_workspace = create_owned_workspace(default_work_root(), prefix="studio-preview",
                                                            task_id="studio-preview", kind="preview-workspace")
        source, slide = self.source, self.slide
        output = self._preview_workspace.path / f"s{slide}-{time.monotonic_ns()}"

        def operation(progress):
            before = sha256_file(source)
            pages = PptxPackage.open(source).slide_count
            result = preview_reference(source, slide, output, preview_width=4000)
            path = Path(result["referencePng"])
            box = crop_pixels(path, padding_px=0, crop_percent=None, expand_percent=(0.,) * 4)
            if sha256_file(source) != before:
                raise ValueError("源文件在预览期间改变，请重新打开")
            return {"source": str(source), "hash": before, "pages": pages, "path": str(path), "box": box}

        self._start("preview", operation)

    @Slot(int)
    def selectPage(self, page: int) -> None:
        if not self._busy and self.source and 1 <= page <= self.pages and page != self.slide:
            self.slide = page
            self.editor = CropEditor()
            self._preview()

    def _edit(self, operation: Callable) -> None:
        if self._busy or not self.editor.ready or self.view_kind != "source":
            return
        try:
            operation()
            self.check_status = "设置已改变 · 需要重新检查"
        except Exception as exc:
            self.status = f"设置无效：{exc}"
        self.changed.emit()

    @Slot(str)
    def setMode(self, mode: str) -> None:
        self._edit(lambda: self.editor.mode(mode))

    @Slot(int, float)
    def setMargin(self, edge: int, value: float) -> None:
        self._edit(lambda: self.editor.margin(edge, value))

    @Slot(float)
    def setBudget(self, value: float) -> None:
        self._edit(lambda: self.editor.budget(value))

    @Slot()
    def beginEdit(self) -> None:
        self._edit(self.editor.begin)

    @Slot(bool)
    def endEdit(self, cancel: bool = False) -> None:
        self._edit(lambda: self.editor.end(cancel))

    @Slot(str, float, float)
    def resizeCrop(self, handle: str, x: float, y: float) -> None:
        self._edit(lambda: self.editor.resize(handle, x, y))

    @Slot(float, float)
    def moveCrop(self, dx: float, dy: float) -> None:
        self._edit(lambda: self.editor.move(dx, dy))

    @Slot(int, float)
    def setBound(self, edge: int, value: float) -> None:
        def operation():
            if not 0 <= edge < 4:
                raise ValueError("Unknown bound")
            values = list(self.editor.base.to_percent())
            values[edge] = value
            self.editor.bounds(tuple(values))
        self._edit(operation)

    @Slot(bool)
    def undo(self, redo: bool = False) -> None:
        self._edit(lambda: self.editor.undo(redo))

    @Slot()
    def chooseOutput(self) -> None:
        if self._busy:
            return
        directory = QFileDialog.getExistingDirectory(None, "选择输出文件夹", str(self.output))
        if directory:
            self.output = Path(directory)
            self.check_status = "输出位置已改变 · 需要重新检查"
            self.changed.emit()

    @Slot(bool)
    def export(self, dry_run: bool = False) -> None:
        if self._busy or not self.editor.ready or not self.source or self.editor._gesture is not None:
            return
        self.active_request = self.editor.request(self.source, self.slide, self.output, dry_run=dry_run)
        request = copy.deepcopy(self.active_request)
        source, expected_hash = self.source, self.source_hash
        self._token = CancellationToken()
        token = self._token
        self.status = "检查参数…" if dry_run else "开始导出 · 当前参数已锁定"

        def operation(progress):
            if sha256_file(source) != expected_hash:
                raise ValueError("PPTX已在预览后改变；请重新打开以更新边界")
            from ..application import ExportService
            return ExportService().execute(request, base_dir=source.parent,
                                           event_sink=progress, cancel_token=token)
        self._start("check" if dry_run else "export", operation)

    @Slot()
    def cancel(self) -> None:
        if self._token:
            self._token.cancel()
            self.status = "正在安全取消，不会发布半成品"
            self.changed.emit()

    def _load_result(self, package: Path) -> None:
        report = json.loads((package / "qa-report.json").read_text(encoding="utf-8"))
        self.results = {}
        sizes = []
        for item in report["artifacts"]:
            path = (package / item["path"]).resolve()
            if package.resolve() not in path.parents:
                raise ValueError("结果路径越界")
            kind = item["kind"]
            if kind == "pdf":
                self.results["pdf"] = path
                sizes.append(f"PDF {item['bytes']/1e6:.2f} MB")
            elif kind == "png":
                self.results["alpha"] = path
            elif path.suffix.lower() == ".svg":
                label = "紧凑SVG" if "under-" in path.name else "完整SVG"
                sizes.append(f"{label} {item['bytes']/1e6:.2f} MB")
        self.result_package = package
        passed = sum(f["status"] == "PASS" for f in report["findings"])
        self.result_summary = (f"上次导出：{report['verdict']} · {passed}/{len(report['findings'])} 项通过\n"
                               + " · ".join(sizes) + "\n紧凑版本会压缩位图；矢量图形不因此变成位图。")
        self.status = "导出完成 · 可切换到PDF或透明结果，或独立复核产物"
        self.showView("alpha" if "alpha" in self.results else "pdf")

    @Slot(str)
    def showView(self, kind: str) -> None:
        if self._busy:
            return
        if kind == "source":
            self.view_url = self.preview_url
            self.view_width, self.view_height = self.editor.width, self.editor.height
        elif kind in self.results:
            self.view_url = self.provider.register(self.results[kind])
            if "alpha" in self.results:
                image = QImage(str(self.results["alpha"]))
                self.view_width, self.view_height = image.width(), image.height()
        else:
            return
        self.view_kind = kind
        self.changed.emit()

    @Slot()
    def verifyResult(self) -> None:
        if self._busy or not self.result_package:
            return
        path = self.result_package / "manifest.json"
        self.status = "独立复核上次产物的清单与校验和"
        def operation(progress):
            verdict, findings = verify_package(path)
            return verdict.value, len(findings)
        self._start("verify", operation)

    @Slot(str)
    def openResult(self, kind: str) -> None:
        if self.result_package:
            path = self.result_package / "report.html" if kind == "report" else self.result_package
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @Slot(result=bool)
    def canClose(self) -> bool:
        if self._busy:
            self.status = "任务仍在运行，请先安全取消导出或等待预览结束"
            self.changed.emit()
            return False
        if self._preview_workspace and mark_workspace_complete(self._preview_workspace):
            delete_owned_workspace(self._preview_workspace)
            self._preview_workspace = None
        return True

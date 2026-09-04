from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .application import ExportService
from .geometry import NormalizedRect, effective_pixel_box, move_normalized_rect, resize_normalized_rect
from .ooxml import PptxPackage
from .powerpoint import preview_reference
from .util import default_work_root, ensure_within, sha256_file


class CropCanvas(QWidget):
    rect_changed = Signal(object)

    HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(360, 260)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pixmap: QPixmap | None = None
        self._crop = NormalizedRect(0.05, 0.05, 0.95, 0.95)
        self._effective: NormalizedRect | None = None
        self._show_crop = True
        self._drag: str | None = None
        self._press_point: tuple[float, float] | None = None
        self._press_rect: NormalizedRect | None = None
        self._editable = True

    def set_image(self, path: Path) -> None:
        image = QImage(str(path))
        if image.isNull():
            raise ValueError(f"Cannot open preview: {path}")
        image.setDevicePixelRatio(1.0)
        self._pixmap = QPixmap.fromImage(image)
        self._pixmap.setDevicePixelRatio(1.0)
        self.update()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self.setCursor(Qt.CursorShape.CrossCursor if editable else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_show_crop(self, visible: bool) -> None:
        self._show_crop = visible
        self.update()

    def set_crop(self, rect: NormalizedRect, *, emit: bool = False) -> None:
        self._crop = rect
        self.update()
        if emit:
            self.rect_changed.emit(rect)

    def set_effective(self, rect: NormalizedRect | None) -> None:
        self._effective = rect
        self.update()

    def _image_rect(self) -> QRectF:
        if not self._pixmap:
            return QRectF()
        available = QRectF(self.rect()).adjusted(16, 16, -16, -16)
        image_ratio = self._pixmap.width() / self._pixmap.height()
        area_ratio = available.width() / max(1.0, available.height())
        if image_ratio >= area_ratio:
            width = available.width()
            height = width / image_ratio
        else:
            height = available.height()
            width = height * image_ratio
        return QRectF(
            available.center().x() - width / 2,
            available.center().y() - height / 2,
            width,
            height,
        )

    @staticmethod
    def _mapped(rect: NormalizedRect, image_rect: QRectF) -> QRectF:
        return QRectF(
            image_rect.left() + rect.left * image_rect.width(),
            image_rect.top() + rect.top * image_rect.height(),
            (rect.right - rect.left) * image_rect.width(),
            (rect.bottom - rect.top) * image_rect.height(),
        )

    @staticmethod
    def _handle_points(rect: QRectF) -> dict[str, QPointF]:
        return {
            "nw": rect.topLeft(),
            "n": QPointF(rect.center().x(), rect.top()),
            "ne": rect.topRight(),
            "e": QPointF(rect.right(), rect.center().y()),
            "se": rect.bottomRight(),
            "s": QPointF(rect.center().x(), rect.bottom()),
            "sw": rect.bottomLeft(),
            "w": QPointF(rect.left(), rect.center().y()),
        }

    def _normalized_point(self, point: QPointF) -> tuple[float, float]:
        image = self._image_rect()
        x = min(1.0, max(0.0, (point.x() - image.left()) / max(1.0, image.width())))
        y = min(1.0, max(0.0, (point.y() - image.top()) / max(1.0, image.height())))
        return x, y

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#20242b"))
        image_rect = self._image_rect()
        if not self._pixmap:
            painter.setPen(QColor("#aeb6c2"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "选择 PPTX 后显示 PowerPoint 预览")
            return
        painter.drawPixmap(image_rect, self._pixmap, QRectF(self._pixmap.rect()))
        crop_rect = self._mapped(self._crop, image_rect)

        if self._show_crop:
            shade = QPainterPath()
            shade.addRect(image_rect)
            hole = QPainterPath()
            hole.addRect(crop_rect)
            painter.fillPath(shade.subtracted(hole), QColor(0, 0, 0, 105))

        if self._effective:
            effective_rect = self._mapped(self._effective, image_rect)
            painter.setPen(QPen(QColor("#3ddc84"), 2, Qt.PenStyle.DashLine))
            painter.drawRect(effective_rect)

        if self._show_crop:
            painter.setPen(QPen(QColor("#00a8ff"), 2))
            painter.drawRect(crop_rect)
        if self._editable and self._show_crop:
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.setBrush(QColor("#00a8ff"))
            for point in self._handle_points(crop_rect).values():
                painter.drawRect(QRectF(point.x() - 5, point.y() - 5, 10, 10))

    def mousePressEvent(self, event: Any) -> None:
        if not self._editable or not self._pixmap or event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position()
        crop_rect = self._mapped(self._crop, self._image_rect())
        self._drag = None
        for name, handle in self._handle_points(crop_rect).items():
            if abs(point.x() - handle.x()) <= 12 and abs(point.y() - handle.y()) <= 12:
                self._drag = name
                break
        if self._drag is None and crop_rect.contains(point):
            self._drag = "move"
        if self._drag:
            self._press_point = self._normalized_point(point)
            self._press_rect = self._crop
            self.setFocus()

    def mouseMoveEvent(self, event: Any) -> None:
        if not self._drag or not self._press_rect or not self._press_point:
            return
        x, y = self._normalized_point(event.position())
        rect = self._press_rect
        if self._drag == "move":
            dx, dy = x - self._press_point[0], y - self._press_point[1]
            updated = move_normalized_rect(rect, dx, dy)
        else:
            reference_width = 4000
            reference_height = 2250
            if self._pixmap:
                reference_height = round(reference_width * self._pixmap.height() / self._pixmap.width())
            updated = resize_normalized_rect(
                rect, self._drag, x, y,
                reference_width=reference_width,
                reference_height=reference_height,
            )
        self.set_crop(updated, emit=True)

    def mouseReleaseEvent(self, _event: Any) -> None:
        self._drag = None
        self._press_point = None
        self._press_rect = None


class PreviewWorker(QObject):
    finished = Signal(int, str)
    failed = Signal(int, str)

    def __init__(self, generation: int, source: Path, slide: int, output_dir: Path) -> None:
        super().__init__()
        self.generation = generation
        self.source = source
        self.slide = slide
        self.output_dir = output_dir

    @Slot()
    def run(self) -> None:
        try:
            path = self.output_dir / "powerpoint-preview.png"
            valid_cache = False
            if path.exists():
                image = QImage(str(path))
                valid_cache = not image.isNull() and image.width() > 0 and image.height() > 0
                if not valid_cache:
                    path.unlink(missing_ok=True)
            if not valid_cache:
                result = preview_reference(self.source, self.slide, self.output_dir)
                path = Path(result["referencePng"])
            self.finished.emit(self.generation, str(path))
        except Exception as exc:
            self.failed.emit(self.generation, str(exc))


class ExportWorker(QObject):
    finished = Signal(object)
    progress = Signal(object)

    def __init__(self, document: dict[str, Any], base_dir: Path) -> None:
        super().__init__()
        self.document = document
        self.base_dir = base_dir

    @Slot()
    def run(self) -> None:
        result = ExportService().execute(self.document, base_dir=self.base_dir, event_sink=self.progress.emit)
        self.finished.emit(result)


class SlideGuardWindow(QMainWindow):
    def __init__(self, initial: Path | None = None) -> None:
        super().__init__()
        self.setFont(QFont("Microsoft YaHei UI", 9))
        self.setWindowTitle("SlideGuard — PPTX 高保真导出")
        self.resize(1260, 780)
        self.setAcceptDrops(True)
        self._source: Path | None = None
        self._source_sha: str | None = None
        self._preview_generation = 0
        self._preview_running = False
        self._pending_preview = False
        self._export_running = False
        self._preview_root = default_work_root() / f"preview-session-{uuid.uuid4().hex}"
        self._threads: set[QThread] = set()
        self._workers: set[QObject] = set()
        self._last_package: Path | None = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(250)
        self._preview_timer.timeout.connect(self._request_preview)
        self._build_ui()
        if initial:
            self._load_source(initial)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        file_row = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("选择一个 PPTX 文件")
        self.file_edit.setReadOnly(True)
        choose = QPushButton("选择 PPTX…")
        choose.clicked.connect(self._choose_file)
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(choose)
        root.addLayout(file_row)

        splitter = QSplitter()
        self.canvas = CropCanvas()
        self.canvas.rect_changed.connect(self._canvas_changed)
        splitter.addWidget(self.canvas)

        panel = QFrame()
        panel.setMinimumWidth(290)
        panel_layout = QVBoxLayout(panel)
        form = QFormLayout()
        self.slide_spin = QSpinBox()
        self.slide_spin.setRange(1, 1)
        self.slide_spin.valueChanged.connect(self._schedule_preview)
        form.addRow("页面", self.slide_spin)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("手动裁剪", "manual")
        self.mode_combo.addItem("自动紧边", "auto")
        self.mode_combo.currentIndexChanged.connect(self._controls_changed)
        form.addRow("裁剪模式", self.mode_combo)

        self.bound_spins: dict[str, QDoubleSpinBox] = {}
        for name, label, value in (
            ("left", "左边界 (%)", 5.0),
            ("top", "上边界 (%)", 5.0),
            ("right", "右边界 (%)", 95.0),
            ("bottom", "下边界 (%)", 95.0),
        ):
            spin = QDoubleSpinBox()
            spin.setRange(0, 100)
            spin.setDecimals(4)
            spin.setValue(value)
            spin.valueChanged.connect(self._controls_changed)
            self.bound_spins[name] = spin
            form.addRow(label, spin)

        self.expand_spins: dict[str, QDoubleSpinBox] = {}
        for name, label in (("left", "向左扩展 (%)"), ("top", "向上扩展 (%)"), ("right", "向右扩展 (%)"), ("bottom", "向下扩展 (%)")):
            spin = QDoubleSpinBox()
            spin.setRange(0, 100)
            spin.setDecimals(2)
            spin.valueChanged.connect(self._controls_changed)
            self.expand_spins[name] = spin
            form.addRow(label, spin)

        self.padding_spin = QSpinBox()
        self.padding_spin.setRange(0, 10000)
        self.padding_spin.setValue(16)
        self.padding_spin.valueChanged.connect(self._controls_changed)
        form.addRow("安全边 (参考像素)", self.padding_spin)

        self.limit_spin = QDoubleSpinBox()
        self.limit_spin.setRange(0.1, 1000)
        self.limit_spin.setDecimals(2)
        self.limit_spin.setValue(2.5)
        self.limit_spin.setSuffix(" MB")
        form.addRow("PDF / 紧凑 SVG 上限", self.limit_spin)
        panel_layout.addLayout(form)

        panel_layout.addWidget(QLabel("蓝线：手动裁剪框    绿虚线：扩展与安全边后的实际输出框"))
        note = QLabel("PPT 里的 PNG/JPEG 会保留原始像素，但不会被伪装成矢量路径。最终 SVG 画布透明；真实白色图形仍保留。")
        note.setWordWrap(True)
        panel_layout.addWidget(note)
        self.output_edit = QLineEdit()
        output_button = QPushButton("选择输出文件夹…")
        output_button.clicked.connect(self._choose_output)
        panel_layout.addWidget(self.output_edit)
        panel_layout.addWidget(output_button)

        self.export_button = QPushButton("导出并自动验收")
        self.export_button.clicked.connect(self._start_export)
        self.export_button.setEnabled(False)
        panel_layout.addWidget(self.export_button)
        self.open_button = QPushButton("打开结果文件夹")
        self.open_button.clicked.connect(self._open_result)
        self.open_button.setEnabled(False)
        panel_layout.addWidget(self.open_button)
        panel_layout.addStretch(1)
        panel_scroll = QScrollArea()
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setFrameShape(QFrame.Shape.NoFrame)
        panel_scroll.setWidget(panel)
        panel_scroll.setMinimumWidth(310)
        splitter.addWidget(panel_scroll)
        splitter.setStretchFactor(0, 1)
        root.addWidget(splitter, 1)

        self.status = QLabel("就绪")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.setCentralWidget(central)
        self._controls_changed()

    def _choose_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "选择 PowerPoint 文件", "", "PowerPoint (*.pptx)")
        if filename:
            self._load_source(Path(filename))

    def _load_source(self, path: Path) -> None:
        try:
            package = PptxPackage.open(path)
        except Exception as exc:
            QMessageBox.critical(self, "无法打开", str(exc))
            return
        self._source = path.resolve()
        try:
            self._source_sha = sha256_file(self._source)
        except Exception as exc:
            QMessageBox.critical(self, "无法读取", str(exc))
            self._source = None
            return
        self.file_edit.setText(str(self._source))
        self.slide_spin.setRange(1, package.slide_count)
        self.output_edit.setText(str(self._source.parent / "slideguard-output"))
        self.export_button.setEnabled(True)
        self._schedule_preview()

    def _schedule_preview(self) -> None:
        if self._source and not self._export_running:
            self._preview_generation += 1
            self._preview_timer.start()

    def _request_preview(self) -> None:
        if not self._source:
            return
        if self._preview_running:
            self._pending_preview = True
            return
        self._preview_running = True
        generation = self._preview_generation
        slide = self.slide_spin.value()
        key = f"{self._source_sha}-s{slide:04d}-w1600"
        output_dir = self._preview_root / key
        self.status.setText(f"正在生成第 {slide} 页预览…")
        worker = PreviewWorker(generation, self._source, slide, output_dir)
        self._run_worker(worker, worker.run, self._preview_ready, self._preview_failed)

    def _run_worker(
        self,
        worker: QObject,
        run_slot: Callable[[], None],
        finished_slot: Callable[..., None],
        failed_slot: Callable[..., None] | None = None,
    ) -> None:
        thread = QThread(self)
        self._threads.add(thread)
        self._workers.add(worker)
        worker.moveToThread(thread)
        thread.started.connect(run_slot)
        worker.finished.connect(finished_slot)
        worker.finished.connect(thread.quit)
        if failed_slot is not None and hasattr(worker, "failed"):
            worker.failed.connect(failed_slot)
            worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda: self._threads.discard(thread))
        thread.finished.connect(lambda: self._workers.discard(worker))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _preview_ready(self, generation: int, path: str) -> None:
        self._preview_running = False
        if generation != self._preview_generation:
            self._start_pending_preview()
            return
        try:
            self.canvas.set_image(Path(path))
            self.status.setText(f"第 {self.slide_spin.value()} 页预览已就绪")
            self._controls_changed()
        except Exception as exc:
            self._preview_failed(generation, str(exc))
            return
        self._start_pending_preview()

    def _preview_failed(self, generation: int, message: str) -> None:
        self._preview_running = False
        if generation == self._preview_generation:
            self.status.setText(f"预览失败：{message}")
        self._start_pending_preview()

    def _start_pending_preview(self) -> None:
        if self._pending_preview and not self._export_running:
            self._pending_preview = False
            QTimer.singleShot(0, self._request_preview)

    def _canvas_changed(self, rect: NormalizedRect) -> None:
        for spin, value in zip(self.bound_spins.values(), rect.to_percent()):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self._update_effective(rect)

    def _manual_rect(self) -> NormalizedRect | None:
        try:
            return NormalizedRect.from_percent(tuple(spin.value() for spin in self.bound_spins.values()))
        except Exception:
            return None

    def _controls_changed(self) -> None:
        manual = self.mode_combo.currentData() == "manual"
        self.canvas.set_editable(manual)
        self.canvas.set_show_crop(manual)
        for spin in self.bound_spins.values():
            spin.setEnabled(manual)
        rect = self._manual_rect()
        if manual and rect:
            self.canvas.set_show_crop(True)
            self.canvas.set_crop(rect)
            self._update_effective(rect)
            self.export_button.setEnabled(self._source is not None and not self._export_running)
        elif manual:
            self.canvas.set_show_crop(False)
            self.canvas.set_effective(None)
            self.export_button.setEnabled(False)
            self.status.setText("裁剪边界无效：左边必须小于右边，上边必须小于下边")
        else:
            self.canvas.set_effective(None)
            self.export_button.setEnabled(self._source is not None and not self._export_running)
            if self._source:
                self.status.setText("自动紧边将在最终 4000 像素参考图上计算")

    def _update_effective(self, rect: NormalizedRect) -> None:
        reference_width = 4000
        reference_height = 4000
        if self.canvas._pixmap:
            reference_height = round(reference_width * self.canvas._pixmap.height() / self.canvas._pixmap.width())
        pixels = effective_pixel_box(
            rect,
            reference_width,
            reference_height,
            expand_percent=tuple(spin.value() for spin in self.expand_spins.values()),
            padding_px=self.padding_spin.value(),
        )
        self.canvas.set_effective(NormalizedRect.from_pixels(*pixels))

    def _choose_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择输出文件夹", self.output_edit.text())
        if directory:
            self.output_edit.setText(directory)

    def _request_document(self) -> dict[str, Any]:
        assert self._source is not None
        mode = self.mode_combo.currentData()
        crop: dict[str, Any] = {
            "mode": mode,
            "expandPercent": {name: spin.value() for name, spin in self.expand_spins.items()},
            "paddingPx": self.padding_spin.value(),
        }
        if mode == "manual":
            crop["boundsPercent"] = {name: spin.value() for name, spin in self.bound_spins.items()}
        byte_limit = int(self.limit_spin.value() * 1_000_000)
        return {
            "schemaVersion": "1.0",
            "input": str(self._source),
            "slides": str(self.slide_spin.value()),
            "outputRoot": self.output_edit.text(),
            "crop": crop,
            "quality": {"pdfMaxBytes": byte_limit, "svgMaxBytes": byte_limit},
            "behavior": {"strict": True, "dryRun": False, "progress": "jsonl"},
        }

    def _start_export(self) -> None:
        if not self._source:
            return
        if self._export_running:
            return
        self._export_running = True
        self._preview_timer.stop()
        self._pending_preview = False
        self.export_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.status.setText("正在准备导出…")
        worker = ExportWorker(self._request_document(), self._source.parent)
        worker.progress.connect(self._export_progress)
        self._run_worker(worker, worker.run, self._export_finished)

    def _export_progress(self, event: dict[str, Any]) -> None:
        phase = event.get("phase")
        state = event.get("state")
        if phase == "validation":
            message = "正在检查输入和裁剪设置…" if state == "start" else "输入检查通过"
        elif phase == "export":
            message = "正在导出并自动验收…" if state == "start" else "导出与验收完成"
        else:
            message = "正在处理…"
        self.status.setText(message)

    def _export_finished(self, result: dict[str, Any]) -> None:
        self._export_running = False
        self._controls_changed()
        if result["status"] == "succeeded":
            self._last_package = Path(result["output"]["packagePath"])
            self.open_button.setEnabled(True)
            verdict = result.get("verdict") or "完成"
            self.status.setText(f"导出完成：{verdict}\n{self._last_package}")
        else:
            error = result.get("error") or {}
            self.status.setText(f"导出失败：{error.get('code', 'ERROR')} — {error.get('message', '')}")

    def _open_result(self) -> None:
        if self._last_package:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_package)))

    def dragEnterEvent(self, event: Any) -> None:
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile() and urls[0].toLocalFile().lower().endswith(".pptx"):
            event.acceptProposedAction()

    def dropEvent(self, event: Any) -> None:
        urls = event.mimeData().urls()
        if urls:
            self._load_source(Path(urls[0].toLocalFile()))
            event.acceptProposedAction()

    def closeEvent(self, event: Any) -> None:
        if self._threads:
            QMessageBox.information(
                self,
                "任务仍在运行",
                "SlideGuard 正在安全完成当前 PowerPoint 调用。请在任务结束后再关闭窗口；程序不会强行结束您的 PowerPoint。",
            )
            event.ignore()
            return
        try:
            ensure_within(self._preview_root, default_work_root())
            if self._preview_root.exists():
                shutil.rmtree(self._preview_root)
        except Exception:
            pass
        event.accept()


def run_gui(initial: Path | None = None) -> int:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("SlideGuard")
    window = SlideGuardWindow(initial)
    screen = app.primaryScreen()
    if screen:
        available = screen.availableGeometry()
        window.resize(min(1260, int(available.width() * 0.9)), min(780, int(available.height() * 0.9)))
    window.show()
    return app.exec()


def main() -> int:
    return run_gui()

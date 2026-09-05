import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, QSize, QUrl, QPointF, qInstallMessageHandler
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication
from PySide6.QtQuick import QQuickItem

from slideguard.studio.controller import StudioController
from slideguard.studio.rendering import LocalPreviewProvider, bounded_size
import slideguard.studio.app as studio_app


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_busy_commands_cannot_change_snapshot(app):
    controller = StudioController(LocalPreviewProvider())
    controller.editor.install_reference((100, 100, 900, 800, 1000, 1000))
    before = controller.editor.state
    controller._busy = "export"
    controller.setMargin(-1, 5)
    controller.setMode("full")
    controller.loadSource("not-opened.pptx")
    controller.setBound(0, 50)
    assert controller.editor.state == before
    assert controller.source is None


def test_qml_load_and_zoom_anchor(app):
    errors = []
    old = qInstallMessageHandler(lambda kind, context, message: errors.append(message))
    engine = QQmlApplicationEngine()
    provider = LocalPreviewProvider()
    controller = StudioController(provider)
    engine.addImageProvider("localpreview", provider)
    engine.rootContext().setContextProperty("studio", controller)
    try:
        engine.load(QUrl.fromLocalFile(str(Path(studio_app.__file__).parent / "qml" / "Main.qml")))
        app.processEvents()
        assert engine.rootObjects(), errors
        root = engine.rootObjects()[0]
        viewport = root.findChild(QObject, "cropViewport")
        assert viewport is not None
        root.setProperty("width", 1000)
        root.setProperty("height", 700)
        app.processEvents()
        assert viewport.property("width") > 400
        assert viewport.property("height") > 350
        viewport.setProperty("zoom", 8.0)
        app.processEvents()
        assert viewport.property("zoom") == 8
        assert controller.editor.crop.mode == "auto"
        root.close()
        engine.deleteLater()
        app.processEvents()
        assert not [m for m in errors if any(s in m for s in ("Error", "is not defined", "Unable to assign", "Binding loop", "failed to load"))], errors
    finally:
        qInstallMessageHandler(old)


def test_provider_rejects_unregistered_path_and_preserves_alpha(app, tmp_path):
    provider = LocalPreviewProvider()
    size = QSize()
    assert provider.requestImage("../../private.png", size, QSize(100, 100)).isNull()
    path = tmp_path / "alpha.png"
    image = QImage(400, 200, QImage.Format.Format_ARGB32)
    image.fill(0)
    image.save(str(path))
    token = provider.register(path).rsplit("/", 1)[1]
    rendered = provider.requestImage(token, size, QSize(800, 400))
    assert size.width() == 800
    assert rendered.pixelColor(0, 0).alpha() == 0
    assert bounded_size(1000, 2000, 100000) == QSize(2048, 4096)


def test_pdf_provider_rerenders_vectors_at_requested_resolution(app, tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject
    path = tmp_path / "vector.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(200, 100)
    stream = DecodedStreamObject()
    stream.set_data(b"0 0 0 RG 2 w 10 10 m 190 90 l S")
    page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as output:
        writer.write(output)
    provider = LocalPreviewProvider()
    token = provider.register(path).rsplit("/", 1)[1]
    low = provider.requestImage(token, QSize(), QSize(400, 200))
    high = provider.requestImage(token, QSize(), QSize(1600, 800))
    assert not low.isNull() and not high.isNull()
    assert low.size() == QSize(400, 200)
    assert high.size() == QSize(1600, 800)
    assert high.pixelColor(800, 400).value() < 30


@pytest.mark.parametrize("dimensions", [(1000, 700), (1400, 880)])
@pytest.mark.parametrize("mode", ["empty", "source", "busy", "alpha"])
def test_design_workbench_state_and_layout(app, tmp_path, dimensions, mode):
    errors = []
    previous = qInstallMessageHandler(lambda kind, context, message: errors.append(message))
    engine = QQmlApplicationEngine()
    provider = LocalPreviewProvider()
    controller = StudioController(provider)
    if mode != "empty":
        path = tmp_path / "reference.png"
        reference = QImage(1000, 600, QImage.Format.Format_ARGB32)
        reference.fill(0xffffffff)
        assert reference.save(str(path))
        controller.editor.install_reference((100, 100, 900, 500, 1000, 600))
        controller.source = Path("a-long-document-name-that-must-not-push-the-toolbar.pptx")
        controller.pages = 2
        controller.preview_url = controller.view_url = provider.register(path)
        controller.view_width, controller.view_height = 1000, 600
        if mode == "busy":
            controller._busy = "export"
        if mode == "alpha":
            controller.view_kind = "alpha"
            controller.result_package = tmp_path
    engine.addImageProvider("localpreview", provider)
    engine.rootContext().setContextProperty("studio", controller)
    try:
        engine.load(QUrl.fromLocalFile(str(Path(studio_app.__file__).parent / "qml" / "Main.qml")))
        assert engine.rootObjects(), errors
        root = engine.rootObjects()[0]
        root.setWidth(dimensions[0])
        root.setHeight(dimensions[1])
        # Yield the Python GIL as the asynchronous image provider enters Python.
        # QTest.qWait alone can leave it blocked while engine teardown waits on it.
        for _ in range(30):
            app.processEvents()
            time.sleep(0.01)
        viewport = root.findChild(QQuickItem, "cropViewport")
        assert viewport.width() > 400 and viewport.height() > 450
        for name in ("documentBar", "canvasToolbar", "exportDock", "checkButton", "exportButton"):
            item = root.findChild(QQuickItem, name)
            assert item is not None and item.isVisible(), name
            position = item.mapToScene(QPointF(0, 0))
            assert position.x() >= 0 and position.y() >= 0, name
            assert position.x() + item.width() <= root.width() + 1, name
            assert position.y() + item.height() <= root.height() + 1, name
        export = root.findChild(QQuickItem, "exportButton")
        assert export.isEnabled() == (mode != "empty")
        assert root.findChild(QQuickItem, "checkButton").isEnabled() == (mode in ("source", "alpha"))
        assert export.property("text") == ("取消导出" if mode == "busy" else "导出并验收")
        if mode != "empty":
            # The source thumbnail must remain independent of the delivered preview.
            assert controller.state["sourcePreviewUrl"] == controller.preview_url
        if mode == "source":
            before = controller.editor.state
            viewport.fitContent()
            for _ in range(10):
                app.processEvents()
                time.sleep(0.01)
            rect = controller.editor.effective
            scale = viewport.property("fit") * viewport.property("zoom")
            center_x = (rect.left + rect.right) / 2 * 1000
            center_y = (rect.top + rect.bottom) / 2 * 600
            assert abs((center_x - 500) * scale + viewport.property("panX")) < 0.01
            assert abs((center_y - 300) * scale + viewport.property("panY")) < 0.01
            assert controller.editor.state == before
        assert not [m for m in errors if any(s in m for s in ("Error", "is not defined", "Unable to assign", "Binding loop", "failed to load"))], errors
    finally:
        controller._busy = ""
        if engine.rootObjects():
            engine.rootObjects()[0].close()
        engine.deleteLater()
        app.processEvents()
        qInstallMessageHandler(previous)

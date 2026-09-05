import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, QSize, QUrl, qInstallMessageHandler
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

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

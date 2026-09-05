from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from .controller import StudioController
from .rendering import LocalPreviewProvider


def run_studio(initial: Path | None = None) -> int:
    QQuickStyle.setStyle("Basic")
    application = QApplication.instance() or QApplication(["SlideGuard Studio"])
    application.setApplicationName("SlideGuard Studio")
    engine = QQmlApplicationEngine()
    provider = LocalPreviewProvider()
    controller = StudioController(provider)
    engine.addImageProvider("localpreview", provider)
    engine.rootContext().setContextProperty("studio", controller)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "qml" / "Main.qml")))
    if not engine.rootObjects():
        return 20
    if initial:
        QTimer.singleShot(0, lambda: controller.loadSource(str(initial)))
    return application.exec()


def main() -> int:
    return run_studio(Path(sys.argv[1]) if len(sys.argv) > 1 else None)


if __name__ == "__main__":
    raise SystemExit(main())

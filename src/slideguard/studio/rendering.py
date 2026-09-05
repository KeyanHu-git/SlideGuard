"""Local, allowlisted image provider. PDF is rerendered, not stretched forever."""
from __future__ import annotations

from pathlib import Path
from threading import Lock
from uuid import uuid4

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtPdf import QPdfDocument
from PySide6.QtQuick import QQuickImageProvider


MAX_TEXTURE_EDGE = 4096


def bounded_size(width: int, height: int, requested_width: int) -> QSize:
    if width <= 0 or height <= 0:
        raise ValueError("Invalid page size")
    scale = min(max(1, requested_width) / width, MAX_TEXTURE_EDGE / max(width, height))
    return QSize(max(1, round(width * scale)), max(1, round(height * scale)))


class LocalPreviewProvider(QQuickImageProvider):
    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image,
                         QQuickImageProvider.Flag.ForceAsynchronousImageLoading)
        self._paths: dict[str, Path] = {}
        self._lock = Lock()

    def register(self, path: Path) -> str:
        path = path.resolve(strict=True)
        if path.suffix.lower() not in {".png", ".pdf"}:
            raise ValueError("Unsupported preview format")
        token = uuid4().hex
        with self._lock:
            self._paths[token] = path
        return "image://localpreview/" + token

    def requestImage(self, identifier, size, requestedSize):
        with self._lock:
            path = self._paths.get(identifier.split("?")[0])
        if path is None:
            return QImage()
        wanted = requestedSize.width() if requestedSize.width() > 0 else 1600
        if path.suffix.lower() == ".pdf":
            # Each asynchronous request owns its document in the requesting thread.
            document = QPdfDocument()
            if document.load(str(path)) != QPdfDocument.Error.None_:
                return QImage()
            points = document.pagePointSize(0)
            target = bounded_size(round(points.width()), round(points.height()), wanted)
            image = document.render(0, target)
            document.close()
        else:
            image = QImage(str(path))
            if not image.isNull():
                target = bounded_size(image.width(), image.height(), wanted)
                image = image.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        if not image.isNull():
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image

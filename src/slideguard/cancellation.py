from __future__ import annotations

import threading

from .errors import CancelledError


class CancellationToken:
    """Thread-safe cooperative cancellation shared by GUI and export layers."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def throw_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise CancelledError("The export was cancelled", stage="cancellation")

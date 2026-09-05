from __future__ import annotations

from typing import Final


NETWORK_POLICY_VERSION: Final = "1.0"


def offline_policy() -> dict[str, object]:
    """Return the immutable-by-copy network defaults exposed in local reports."""
    return {
        "version": NETWORK_POLICY_VERSION,
        "mode": "offline-only",
        "telemetryEnabled": False,
        "automaticUploadsEnabled": False,
        "updateChecksEnabled": False,
    }

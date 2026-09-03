from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Verdict(str, Enum):
    PASS = "PASS"
    PASS_WITH_SOURCE_WARNINGS = "PASS_WITH_SOURCE_WARNINGS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "N/A"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(slots=True)
class Finding:
    code: str
    status: Verdict
    severity: Severity
    message: str
    validator: str
    slide: int | None = None
    object_id: str | None = None
    metric: str | None = None
    expected: Any = None
    actual: Any = None
    threshold: Any = None
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FeatureInventory:
    slide: int
    slide_part: str
    shape_count: int = 0
    image_count: int = 0
    line_count: int = 0
    dashed_line_count: int = 0
    shadow_count: int = 0
    transparency_count: int = 0
    crop_count: int = 0
    group_count: int = 0
    gradient_count: int = 0
    text_run_count: int = 0
    formula_count: int = 0
    fonts: list[str] = field(default_factory=list)
    external_relationships: list[str] = field(default_factory=list)
    media: list[dict[str, Any]] = field(default_factory=list)

    def active_features(self) -> list[str]:
        mapping = {
            "images": self.image_count,
            "lines": self.line_count,
            "dashes": self.dashed_line_count,
            "shadows": self.shadow_count,
            "alpha": self.transparency_count,
            "crops": self.crop_count,
            "groups": self.group_count,
            "gradients": self.gradient_count,
            "text": self.text_run_count,
            "math": self.formula_count,
        }
        return [name for name, count in mapping.items() if count]


@dataclass(slots=True)
class ArtifactRecord:
    kind: str
    path: str
    sha256: str
    bytes: int
    slide: int | None = None
    producer: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class JobReport:
    schema_version: str
    tool_version: str
    job_id: str
    source_path: str
    source_sha256_before: str
    source_sha256_after: str
    config: dict[str, Any]
    environment: dict[str, Any]
    features: list[FeatureInventory]
    artifacts: list[ArtifactRecord]
    findings: list[Finding]
    verdict: Verdict
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        def encode(value: Any) -> Any:
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, Path):
                return str(value)
            if hasattr(value, "__dataclass_fields__"):
                return {key: encode(val) for key, val in asdict(value).items()}
            if isinstance(value, list):
                return [encode(item) for item in value]
            if isinstance(value, dict):
                return {key: encode(val) for key, val in value.items()}
            return value

        return encode(self)


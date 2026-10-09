from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.domain.document import BBox
from app.domain.enums import Severity


@dataclass
class Evidence:
    page: int | None = None
    bbox: BBox | None = None
    snippet: str | None = None
    measured_value: float | None = None
    threshold: float | None = None
    location: str | None = None  # visible | hidden | mixed | metadata | file
    regions: list[dict[str, Any]] = field(default_factory=list)  # [{"page": 1, "bbox": [...]}] for previews
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.bbox is not None:
            data["bbox"] = [round(v, 1) for v in self.bbox]
        for region in data["regions"]:
            region["bbox"] = [round(v, 1) for v in region["bbox"]]
        return data


@dataclass
class Flag:
    code: str
    severity: Severity
    detector: str
    message: str
    evidence: Evidence = field(default_factory=Evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": str(self.severity),
            "detector": self.detector,
            "message": self.message,
            "evidence": self.evidence.to_dict(),
        }


def snippet(text: str, limit: int = 220) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

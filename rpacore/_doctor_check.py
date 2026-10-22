"""Shared privacy-bounded doctor check representation."""

from __future__ import annotations

from dataclasses import dataclass, field

from rpacore._doctor_errors import input_error_details


PASS = "pass"
WARNING = "warning"
FAIL = "fail"
NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class DoctorCheck:
    """One privacy-bounded, JSON-safe diagnostic result."""

    id: str
    status: str
    summary: str
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
        }


def input_failure(check_id: str, label: str, exc: Exception) -> DoctorCheck:
    details = input_error_details(exc)
    location = f" ({details['location']})" if "location" in details else ""
    return DoctorCheck(
        check_id, FAIL,
        f"{label}: {details['cause']}{location}; check the file's syntax, fields, and readability",
        details,
    )

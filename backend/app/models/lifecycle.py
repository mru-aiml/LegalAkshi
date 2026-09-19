"""Complaint lifecycle — shared transition rules (both repositories).

Terminal CLOSED complaints cannot move. All other moves must follow the
forward lifecycle; officers may additionally close from review/action stages.
"""
from __future__ import annotations

STATUSES = ("SUBMITTED", "ACKNOWLEDGED", "UNDER_REVIEW", "INSPECTION_SCHEDULED",
            "INSPECTION_COMPLETED", "ACTION_TAKEN", "RESOLVED", "CLOSED")

TRANSITIONS: dict[str, tuple[str, ...]] = {
    "SUBMITTED": ("ACKNOWLEDGED", "CLOSED"),
    "ACKNOWLEDGED": ("UNDER_REVIEW", "CLOSED"),
    "UNDER_REVIEW": ("INSPECTION_SCHEDULED", "ACTION_TAKEN", "CLOSED"),
    "INSPECTION_SCHEDULED": ("INSPECTION_COMPLETED", "CLOSED"),
    "INSPECTION_COMPLETED": ("ACTION_TAKEN", "CLOSED"),
    "ACTION_TAKEN": ("RESOLVED", "CLOSED"),
    "RESOLVED": ("CLOSED",),
    "CLOSED": (),
}


def allowed(from_status: str, to_status: str) -> bool:
    return to_status in TRANSITIONS.get(from_status, ())


# Consumer suggestion lifecycle — separate from complaints (own table,
# own 5-status workflow). Terminal CLOSED suggestions cannot move.
SUGGESTION_STATUSES = ("SUBMITTED", "UNDER_REVIEW", "ACKNOWLEDGED",
                       "ACTIONED", "CLOSED")

SUGGESTION_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "SUBMITTED": ("UNDER_REVIEW", "ACKNOWLEDGED", "CLOSED"),
    "UNDER_REVIEW": ("ACKNOWLEDGED", "ACTIONED", "CLOSED"),
    "ACKNOWLEDGED": ("ACTIONED", "CLOSED"),
    "ACTIONED": ("CLOSED",),
    "CLOSED": (),
}

SUGGESTION_CATEGORIES = ("Product authenticity", "Packaging verification",
                         "Food safety", "Label transparency",
                         "Consumer awareness", "Digital verification",
                         "Other")


def suggestion_allowed(from_status: str, to_status: str) -> bool:
    return to_status in SUGGESTION_TRANSITIONS.get(from_status, ())

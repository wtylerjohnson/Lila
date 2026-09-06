"""Lead and parent identity helpers. No I/O, no hashing, no persistence."""

from __future__ import annotations


def _clean(value: str, label: str) -> str:
    cleaned = " ".join(str(value or "").split())
    if not cleaned:
        raise ValueError(f"{label} is required")
    return cleaned


def compose_assessment_id(
    assess_run_id: str, subject_kind: str, subject_id: str,
) -> str:
    """Stable parent id from Assess run identity plus subject grain."""

    return (
        f"oa:{_clean(assess_run_id, 'assess_run_id')}:"
        f"{_clean(subject_kind, 'subject_kind')}:"
        f"{_clean(subject_id, 'subject_id')}"
    )


def compose_lead_id(
    parent_assessment_id: str,
    motion_id: str,
    pathway_id: str,
    path_id: str,
) -> str:
    """Stable child id from the four-factor product keys."""

    return (
        f"lr:{_clean(parent_assessment_id, 'parent_assessment_id')}:"
        f"{_clean(motion_id, 'motion_id')}:"
        f"{_clean(pathway_id, 'pathway_id')}:"
        f"{_clean(path_id, 'path_id')}"
    )


def compose_trace_id(assess_run_id: str, subject_id: str) -> str:
    return (
        f"dt:{_clean(assess_run_id, 'assess_run_id')}:"
        f"{_clean(subject_id, 'subject_id')}"
    )

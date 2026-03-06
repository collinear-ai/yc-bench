"""Handler for task progress milestone events."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from ...db.models.event import SimEvent
from ...db.models.task import Task


@dataclass
class TaskHalfResult:
    task_id: UUID
    handled: bool
    milestone_pct: int


def handle_task_half(db: Session, event: SimEvent) -> TaskHalfResult:
    """Record the progress milestone on the task."""
    task_id = UUID(event.payload["task_id"])
    milestone_pct = event.payload.get("milestone_pct", 50)
    task = db.query(Task).filter(Task.id == task_id).one_or_none()

    if task is None:
        return TaskHalfResult(task_id=task_id, handled=False, milestone_pct=milestone_pct)

    task.progress_milestone_pct = max(task.progress_milestone_pct or 0, milestone_pct)
    db.flush()

    return TaskHalfResult(task_id=task_id, handled=True, milestone_pct=milestone_pct)

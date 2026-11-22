# models.py

from pydantic import BaseModel, ConfigDict, Field
from typing import Dict, List, Optional, Literal
class ExplicitTask(BaseModel):
    task_id: str = Field(description="Unique identifier for the task, e.g., T1")
    description: str = Field(description="A concise, action-oriented description of the work, excluding first-person phrasing and task-number introductions based on the transcript")
    explicit_owner: Optional[str] = Field(
        default=None, 
        description="The person explicitly assigned to this task. Leave null if not stated out loud."
    )
    explicit_deadline: Optional[str] = Field(
        default=None, 
        description="The exact deadline stated. Leave null if not explicitly stated."
    )
    explicit_prerequisites: List[str] = Field(
        default_factory=list, 
        description="List of task_ids that must be completed before this one, only if explicitly stated."
    )

class FactualExtraction(BaseModel):
    meeting_title: str
    date: str
    tasks: List[ExplicitTask]

def _require_deadline_date(schema: dict) -> None:
    # Optional for validation (an answer without it is still accepted and handled by the calendar rules),
    # but listed as required in the schema the model sees, so it reliably fills it in.
    required = schema.setdefault("required", [])
    if "deadline_date" not in required:
        required.append("deadline_date")


class EnrichedTask(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require_deadline_date)

    task_id: str = Field(description="Must exactly match the task_id from Pass 1")
    description: str = Field(description="A concise, action-oriented description of the work, excluding first-person phrasing and task-number introductions based on the transcript")
    owner: Optional[str] = Field(description="The explicitly stated owner, or logically inferred owner. Null if entirely unknown.")
    missing_owner: bool = Field(description="Set to True if the owner was not explicitly stated and cannot be inferred confidently.")
    deadline: Optional[str] = Field(description="Explicitly stated or logically inferred deadline.")
    deadline_date: Optional[str] = Field(
        default=None,
        description="The exact calendar date (YYYY-MM-DD) the deadline refers to, worked out from meeting_date and the supplied calendar. Null when no date can be determined."
    )
    missing_deadline: bool = Field(description="Set to True if no deadline was stated or implied.")
    priority: Literal["Low", "Medium", "High"] = Field(description="Infer priority based on urgency words, deadlines, and project context.")
    explicit_prerequisites: List[str] = Field(
            description="List of task IDs (e.g., 'T1') OR string descriptions of external blockers (e.g., 'Waiting on AWS support') explicitly stated as prerequisites."
        )
    inferred_prerequisites: List[str] = Field(
        default_factory=list, 
        description="Task IDs OR string descriptions of external blockers that logically must happen first, even if not explicitly stated."
    )
    dependency_evidence: List[Dict[str, str]] = Field(
        default_factory=list,
        description="For each dependency between tracked tasks, provide prerequisite_task_id, task_id (the dependent task), and the verbatim transcript statement supporting that dependency."
    )
    status: Literal["pending", "in progress", "completed", "blocked"] = Field(
        default="pending", 
        description="Inferred current status of the task based on the meeting dialogue."
    )

class ContextualInference(BaseModel):
    enriched_tasks: List[EnrichedTask]
    initial_circular_dependencies: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Each dependency edge in every original transcript cycle. from_task_id is the prerequisite, to_task_id is the dependent task, and description is the verbatim supporting transcript statement."
    )
    resolved_dependencies: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Agreements or execution steps that resolved the original circular dependencies. Each item should explain the resolution and affected task IDs."
    )
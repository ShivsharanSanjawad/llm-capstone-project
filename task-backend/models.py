# models.py

from typing import Annotated, Any
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from typing import Dict, List, Optional, Literal
def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "; ".join(_text(v) for v in value if v is not None)
    return str(value)


def _text_dict(value: Any) -> Any:
    """Models sometimes put a number, null or a list where a string belongs. Coerce instead of failing the whole analysis."""
    return {str(k): _text(v) for k, v in value.items()} if isinstance(value, dict) else value


def _text_list(value: Any) -> Any:
    return [_text(v) for v in value if v is not None] if isinstance(value, (list, tuple)) else value


TextDict = Annotated[Dict[str, str], BeforeValidator(_text_dict)]
TextList = Annotated[List[str], BeforeValidator(_text_list)]


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
    # Optional for validation (an answer without them is still accepted and handled by code),
    # but listed as required in the schema the model sees, so it reliably fills them in.
    required = schema.setdefault("required", [])
    for name in ("deadline_date", "llm_importance", "priority_reason"):
        if name not in required:
            required.append(name)


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
    priority: Literal["Low", "Medium", "High"] = Field(description="Your own priority label from urgency words, deadlines and project context. The final priority is recomputed as a score from this, the deadline and how many tasks it blocks.")
    llm_importance: Optional[int] = Field(
        default=None, ge=0, le=10,
        description="How important and urgent this task is from the discussion alone, 0 (trivial) to 10 (critical): urgency words, stated stakes, risk if delayed, customer or release impact. Do not consider the due date or dependencies; those are scored separately."
    )
    priority_reason: Optional[str] = Field(
        default=None,
        description="One short sentence quoting or paraphrasing what in the meeting justifies llm_importance."
    )
    explicit_prerequisites: TextList = Field(
            description="List of task IDs (e.g., 'T1') OR string descriptions of external blockers (e.g., 'Waiting on AWS support') explicitly stated as prerequisites."
        )
    inferred_prerequisites: TextList = Field(
        default_factory=list, 
        description="Task IDs OR string descriptions of external blockers that logically must happen first, even if not explicitly stated."
    )
    dependency_evidence: List[TextDict] = Field(
        default_factory=list,
        description="For each dependency between tracked tasks, provide prerequisite_task_id, task_id (the dependent task), and the verbatim transcript statement supporting that dependency."
    )
    status: Literal["pending", "in progress", "completed", "blocked"] = Field(
        default="pending", 
        description="Inferred current status of the task based on the meeting dialogue."
    )

class ContextualInference(BaseModel):
    enriched_tasks: List[EnrichedTask]
    initial_circular_dependencies: List[TextDict] = Field(
        default_factory=list,
        description="Each dependency edge in every original transcript cycle. from_task_id is the prerequisite, to_task_id is the dependent task, and description is the verbatim supporting transcript statement."
    )
    resolved_dependencies: List[TextDict] = Field(
        default_factory=list,
        description="Agreements or execution steps that resolved the original circular dependencies. Each item should explain the resolution and affected task IDs."
    )
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Task:
    record_id: str
    description: str
    priority: str
    status: str = "待开始"
    summary: str = ""
    progress: str = ""
    project: Optional[str] = None
    task_title: Optional[str] = None
    base_branch: Optional[str] = None
    engine: Optional[str] = None
    spec_dir: Optional[str] = None
    clarify_pointer: Optional[str] = None
    queue_position: Optional[int] = None
    plan_generated_at: Optional[str] = None
    approved_at: Optional[str] = None
    completed_at: Optional[str] = None
    branch_info: Optional[str] = None

    def branch_name(self) -> str:
        return f"feature/auto-{self.record_id}"


@dataclass
class Decision:
    action: str
    record_id: str
    stage: str = ""
    form: dict = field(default_factory=dict)
    input_text: str = ""

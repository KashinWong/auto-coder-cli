import json
from abc import ABC, abstractmethod
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from autocoder.models import Task

_PRIORITY_ORDER = ["重要紧急", "紧急不重要", "重要不紧急", "不紧急不重要"]


class TaskStore(ABC):
    @abstractmethod
    def fetch_pending(self) -> list: ...
    @abstractmethod
    def get(self, record_id: str) -> Task: ...
    @abstractmethod
    def update_status(self, record_id: str, status: str) -> None: ...
    @abstractmethod
    def update_summary(self, record_id: str, summary: str, progress: str) -> None: ...
    @abstractmethod
    def set_clarify_pointer(self, record_id: str, relpath: str) -> None: ...
    @abstractmethod
    def set_queue_position(self, record_id: str, position: int) -> None: ...
    @abstractmethod
    def complete(self, record_id, branch, summary, timeline) -> None: ...


class JsonTaskStore(TaskStore):
    """每个任务存 <workspace_dir>/<record_id>/task.json。"""

    def __init__(self, workspace_dir: str):
        self.root = Path(workspace_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, record_id: str) -> Path:
        return self.root / record_id / "task.json"

    def _save(self, task: Task) -> None:
        p = self._path(task.record_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(task), ensure_ascii=False, indent=2))

    def add(self, task: Task) -> None:
        self._save(task)

    def get(self, record_id: str) -> Task:
        data = json.loads(self._path(record_id).read_text())
        return Task(**data)

    def _all(self) -> list:
        tasks = []
        for d in self.root.iterdir():
            f = d / "task.json"
            if f.exists():
                tasks.append(Task(**json.loads(f.read_text())))
        return tasks

    def fetch_pending(self) -> list:
        pending = [t for t in self._all() if t.status == "待开始"]

        def rank(t: Task) -> int:
            return _PRIORITY_ORDER.index(t.priority) if t.priority in _PRIORITY_ORDER else 99

        return sorted(pending, key=rank)

    def _update(self, record_id: str, **fields) -> None:
        t = self.get(record_id)
        for k, v in fields.items():
            setattr(t, k, v)
        self._save(t)

    def update_status(self, record_id: str, status: str) -> None:
        self._update(record_id, status=status)

    def update_summary(self, record_id: str, summary: str, progress: str) -> None:
        self._update(record_id, summary=summary, progress=progress)

    def set_clarify_pointer(self, record_id: str, relpath: str) -> None:
        self._update(record_id, clarify_pointer=relpath)

    def set_queue_position(self, record_id: str, position: int) -> None:
        self._update(record_id, queue_position=position)

    def complete(self, record_id, branch, summary, timeline) -> None:
        from datetime import datetime
        self._update(
            record_id,
            status="已完成",
            branch_info=branch,
            summary=summary,
            progress=timeline,
            completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

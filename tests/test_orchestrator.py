import threading
from pathlib import Path

from autocoder.core.orchestrator import Orchestrator
from autocoder.core.engine_runner import EngineResult
from autocoder.adapters.store import JsonTaskStore
from autocoder.models import Task, Decision


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def send_clarify(self, *a, **k): self.calls.append("clarify")
    def send_charter(self, *a, **k): self.calls.append("charter")
    def send_plan(self, *a, **k): self.calls.append("plan")
    def send_complete(self, *a, **k): self.calls.append("complete")
    def send_failure(self, *a, **k): self.calls.append("failure")


class ScriptedRouter:
    """按 stage 返回预设决策。"""
    def __init__(self, decisions: dict):
        self._d = decisions

    def await_decision(self, record_id, stage):
        return self._d[stage]


def _config(tmp_path, project_path):
    class Cfg:
        workspace_dir = str(tmp_path / "ws")
        concurrency_limit = 3
        default_engine = "fake"
        projects = {"demo": {
            "path": str(project_path), "engine": "fake",
            "match_keywords": ["demo"], "base_branch": "main",
            "test_command": "true", "build_command": "true",
        }}
        engines = {"fake": {"command": "true", "args": [], "timeout": 5, "env": {}}}
        feishu = {}
        def engine_spec(self, name): return self.engines[name]
        def match_project(self, text):
            return "demo" if "demo" in text else None
    return Cfg()


def test_dispatch_happy_path_to_planning(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="给 demo 加功能", priority="重要紧急"))
    notifier = FakeNotifier()

    router = ScriptedRouter({
        "clarify": Decision("clarify_submit", "r1", "clarify",
                            form={"scope": "明确范围", "acceptance": "明确验收"}),
        "charter": Decision("reject_charter", "r1", "charter"),  # 立项卡选「拒」提前收尾
    })

    fake_wt = type("WT", (), {
        "create": staticmethod(lambda p, b, r: str(tmp_path / "wt")),
        "remove": staticmethod(lambda p, r: None),
        "active_count": staticmethod(lambda p: 0),
        "gate_open": staticmethod(lambda a, l: a < l),
    })

    orch = Orchestrator(cfg, store, notifier, router,
                        worktree=fake_wt,
                        predict_fn=lambda d, p: __import__(
                            "autocoder.core.clarify", fromlist=["Prediction"]
                        ).Prediction(["m.py"], ["风险"]))
    orch.dispatch_one()

    assert "clarify" in notifier.calls
    assert "charter" in notifier.calls
    assert store.get("r1").status == "已搁置"  # reject_charter → 已搁置


def test_dispatch_no_pending_is_noop(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    notifier = FakeNotifier()
    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}),
                        predict_fn=lambda d, p: None)
    orch.dispatch_one()  # 不抛异常
    assert notifier.calls == []


def test_execute_runs_engine_and_completes(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    wt_dir = tmp_path / "wt"
    wt_dir.mkdir()
    (wt_dir / "specs" / "001-x").mkdir(parents=True)
    (wt_dir / "specs" / "001-x" / "tasks.md").write_text("- [ ] T001 做事\n")
    store.add(Task(record_id="r1", description="x", priority="p",
                   project="demo", task_title="做事", engine="fake",
                   base_branch="main", spec_dir="specs/001-x",
                   status="进行中"))
    notifier = FakeNotifier()

    fake_git = type("G", (), {
        "add_commit_push": staticmethod(lambda wt, msg, br: (True, "1 file changed")),
    })

    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}),
                        git_ops=fake_git,
                        engine_run=lambda spec, wd, prompt, log: EngineResult.SUCCESS,
                        resolve_worktree=lambda task: str(wt_dir))
    orch.execute("r1")

    assert "complete" in notifier.calls
    assert store.get("r1").status == "已完成"


def test_execute_engine_failure_sets_stalled(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    wt_dir = tmp_path / "wt"
    (wt_dir / "specs" / "001-x").mkdir(parents=True)
    (wt_dir / "specs" / "001-x" / "tasks.md").write_text("- [ ] T001\n")
    store.add(Task(record_id="r1", description="x", priority="p",
                   project="demo", task_title="t", engine="fake",
                   base_branch="main", spec_dir="specs/001-x", status="进行中"))
    notifier = FakeNotifier()
    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}),
                        engine_run=lambda *a: EngineResult.FAILURE,
                        resolve_worktree=lambda task: str(wt_dir))
    orch.execute("r1")
    assert "failure" in notifier.calls
    assert store.get("r1").status == "已停滞"


def test_execute_record_lock_blocks_duplicate(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="x", priority="p", status="进行中"))
    orch = Orchestrator(cfg, store, FakeNotifier(), ScriptedRouter({}))
    # 手动占锁
    assert orch._acquire_lock("r1") is True
    assert orch._acquire_lock("r1") is False  # 第二次拿不到
    orch._release_lock("r1")
    assert orch._acquire_lock("r1") is True

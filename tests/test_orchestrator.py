import threading
from pathlib import Path

from autocoder.core.orchestrator import Orchestrator
from autocoder.core.engine_runner import EngineResult
from autocoder.adapters.store import JsonTaskStore
from autocoder.models import Task, Decision


def _one_round_predict(modules=None, risks=None):
    """生成一个有状态 predict_fn：第 1 次给一个问题（触发一轮澄清卡），
    之后无问题（AI 判定够了 → 进入立项）。模拟 AI 决断轮次。"""
    from autocoder.core.clarify import Prediction, Question
    state = {"calls": 0}

    def predict(description, project_path):
        state["calls"] += 1
        if state["calls"] == 1:
            return Prediction(modules or [], risks or [],
                              questions=[Question(key="scope", ask="范围？")])
        return Prediction(modules or [], risks or [])

    return predict


class FakeNotifier:
    def __init__(self):
        self.calls = []
        self.complete_args = None
        self.queued_position = None

    def send_clarify(self, *a, **k): self.calls.append("clarify")
    def send_charter(self, *a, **k): self.calls.append("charter")
    def send_plan(self, *a, **k): self.calls.append("plan")
    def send_complete(self, task, branch, change_stats, duration, timeline):
        self.calls.append("complete")
        self.complete_args = dict(branch=branch, change_stats=change_stats,
                                  duration=duration, timeline=timeline)
    def send_failure(self, *a, **k): self.calls.append("failure")
    def send_queued(self, task, position):
        self.calls.append("queued")
        self.queued_position = position
    def send_zombie_alert(self, task, status, kind, minutes):
        self.calls.append("zombie")
        self.zombie_args = dict(record_id=task.record_id, status=status,
                                kind=kind, minutes=minutes)


class ScriptedRouter:
    """按 stage 返回预设决策。"""
    def __init__(self, decisions: dict):
        self._d = decisions

    def await_decision(self, record_id, stage, questions=None):
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
        "active_count": staticmethod(lambda p, x=None: 0),
        "gate_open": staticmethod(lambda a, l: a < l),
    })

    orch = Orchestrator(cfg, store, notifier, router,
                        worktree=fake_wt,
                        predict_fn=_one_round_predict(["m.py"], ["风险"]))
    orch.dispatch_one()

    assert "clarify" in notifier.calls
    assert "charter" in notifier.calls
    assert store.get("r1").status == "已搁置"  # reject_charter → 已搁置
    # title 缺失时由澄清阶段用 description 兜底，避免 commit "feat: None"
    assert store.get("r1").task_title == "给 demo 加功能"


class MutableRouter:
    """每个 stage 可返回一串决策，按调用次序消费。"""
    def __init__(self, scripts: dict):
        self._scripts = {k: list(v) for k, v in scripts.items()}

    def await_decision(self, record_id, stage, questions=None):
        return self._scripts[stage].pop(0)


def test_merge_answers_appends_other_to_select():
    """选项题除了选中值，还允许 {key}__other 自由补充，合并进同一答案。"""
    pending = [{"key": "scope", "ask": "范围？"}]
    form = {"scope": "只改前端", "scope__other": "也要兼容旧版浏览器"}
    qa = Orchestrator._merge_answers(pending, form)
    assert qa == [{"ask": "范围？",
                   "answer": "只改前端（补充：也要兼容旧版浏览器）"}]


def test_merge_answers_other_only_when_no_selection():
    """没选任何选项、只填了 __other → 答案就是补充内容本身。"""
    pending = [{"key": "scope", "ask": "范围？"}]
    form = {"scope__other": "选项都不合适，应该按部门分批上线"}
    qa = Orchestrator._merge_answers(pending, form)
    assert qa == [{"ask": "范围？", "answer": "选项都不合适，应该按部门分批上线"}]


def test_merge_answers_multi_select_with_other():
    """多选题（answer 为 list）叠加 __other：list 末尾追加补充项。"""
    pending = [{"key": "mods", "ask": "改哪些模块？"}]
    form = {"mods": ["A", "B"], "mods__other": "还有 C 模块"}
    qa = Orchestrator._merge_answers(pending, form)
    assert qa == [{"ask": "改哪些模块？", "answer": ["A", "B", "还有 C 模块"]}]


def test_merge_answers_global_supplement_is_separate_entry():
    """整卡级 __supplement → 独立问答条目，不挂在任何具体问题下。"""
    pending = [{"key": "scope", "ask": "范围？"}]
    form = {"scope": "只改前端", "__supplement": "上线前先灰度 10%"}
    qa = Orchestrator._merge_answers(pending, form)
    assert qa == [
        {"ask": "范围？", "answer": "只改前端"},
        {"ask": "其他补充说明", "answer": "上线前先灰度 10%"},
    ]


def test_merge_answers_ignores_empty_other_and_supplement():
    """空白的 __other / __supplement 不产生噪声条目，也不改原答案。"""
    pending = [{"key": "scope", "ask": "范围？"}]
    form = {"scope": "只改前端", "scope__other": "   ", "__supplement": ""}
    qa = Orchestrator._merge_answers(pending, form)
    assert qa == [{"ask": "范围？", "answer": "只改前端"}]


def test_dispatch_revise_charter_then_resume(tmp_path):
    """charter 选『改』→ 停在澄清中；resume 后能从澄清续跑，不死锁。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="给 demo 加功能", priority="重要紧急"))
    notifier = FakeNotifier()

    created = []
    fake_wt = type("WT", (), {
        "create": staticmethod(lambda p, b, r: created.append(r) or str(tmp_path / "wt")),
        "remove": staticmethod(lambda p, r: None),
        "active_count": staticmethod(lambda p, x=None: 0),
        "gate_open": staticmethod(lambda a, l: a < l),
    })

    router = MutableRouter({
        "clarify": [
            Decision("clarify_submit", "r1", "clarify",
                     form={"scope": "范围", "acceptance": "验收"}),
            Decision("clarify_submit", "r1", "clarify",
                     form={"scope": "范围2", "acceptance": "验收2"}),
        ],
        # 第一次 charter 选『改』退回；resume 后第二次选『拒』提前收尾
        "charter": [
            Decision("revise_charter", "r1", "charter"),
            Decision("reject_charter", "r1", "charter"),
        ],
    })

    orch = Orchestrator(cfg, store, notifier, router, worktree=fake_wt,
                        predict_fn=lambda d, p: __import__(
                            "autocoder.core.clarify", fromlist=["Prediction"]
                        ).Prediction([], []))
    orch.dispatch_one()
    assert store.get("r1").status == "澄清中"  # 驳回后停在回退态

    orch.resume("r1")  # 续跑：不应死锁
    assert store.get("r1").status == "已搁置"
    assert created == []  # 两次都没批准，worktree 从未创建


def test_resume_from_planning_runs_plan_approval(tmp_path):
    """需求停在『规划中』时，resume 应跑规划→审批；plan 选『退·改方案』回到规划中。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="给 demo 加功能", priority="重要紧急",
                   project="demo", summary="## 需求\n摘要", status="规划中"))
    notifier = FakeNotifier()

    fake_wt = type("WT", (), {
        "create": staticmethod(lambda p, b, r: str(tmp_path / "wt")),
        "remove": staticmethod(lambda p, r: None),
        "active_count": staticmethod(lambda p, x=None: 0),
        "gate_open": staticmethod(lambda a, l: a < l),
    })

    router = MutableRouter({
        "plan": [Decision("revise_plan", "r1", "plan")],
    })

    orch = Orchestrator(cfg, store, notifier, router, worktree=fake_wt,
                        engine_run=lambda spec, wd, prompt, log: EngineResult.SUCCESS)
    orch.resume("r1")

    assert "plan" in notifier.calls          # 跑到了方案审批
    assert store.get("r1").status == "规划中"  # 退·改方案 → 回到规划中（未死锁）


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
        "add_commit_push": staticmethod(lambda wt, msg, br, push=True: (True, "1 file changed")),
    })

    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}),
                        git_ops=fake_git,
                        engine_run=lambda spec, wd, prompt, log: EngineResult.SUCCESS,
                        resolve_worktree=lambda task: str(wt_dir))
    orch.execute("r1")

    assert "complete" in notifier.calls
    assert store.get("r1").status == "已完成"
    # 执行入口应写入 execute_started_at（monitor 双信号之一）
    assert store.get("r1").execute_started_at is not None


def test_execute_completion_removes_worktree_and_wakes_queue(tmp_path):
    """完成（push:true）后须回收 worktree 并促活排队任务——闭环并发闸。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    wt_dir = tmp_path / "wt"
    wt_dir.mkdir()
    (wt_dir / "specs" / "001-x").mkdir(parents=True)
    (wt_dir / "specs" / "001-x" / "tasks.md").write_text("- [ ] T001\n")
    store.add(Task(record_id="r1", description="x", priority="p",
                   project="demo", task_title="做事", engine="fake",
                   base_branch="main", spec_dir="specs/001-x", status="进行中"))
    notifier = FakeNotifier()

    removed = []
    fake_wt = type("WT", (), {
        "create": staticmethod(lambda p, b, r: "wt"),
        "remove": staticmethod(lambda p, r: removed.append(r)),
        "active_count": staticmethod(lambda p, x=None: 3),  # 促活时仍满 → 不起新任务
        "gate_open": staticmethod(lambda a, l: a < l),
    })
    fake_git = type("G", (), {
        "add_commit_push": staticmethod(lambda wt, msg, br, push=True: (True, "1 file")),
    })

    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}),
                        worktree=fake_wt, git_ops=fake_git,
                        engine_run=lambda spec, wd, prompt, log: EngineResult.SUCCESS,
                        resolve_worktree=lambda task: str(wt_dir))
    orch.execute("r1")

    assert store.get("r1").status == "已完成"
    assert removed == ["r1"]   # worktree 被回收，槽位释放


def test_execute_completion_push_false_keeps_worktree(tmp_path):
    """push:false 时 worktree 是改动唯一载体，完成后不得回收。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    cfg.projects["demo"]["push"] = False
    store = JsonTaskStore(cfg.workspace_dir)
    wt_dir = tmp_path / "wt"
    wt_dir.mkdir()
    (wt_dir / "specs" / "001-x").mkdir(parents=True)
    (wt_dir / "specs" / "001-x" / "tasks.md").write_text("- [ ] T001\n")
    store.add(Task(record_id="r1", description="x", priority="p",
                   project="demo", task_title="做事", engine="fake",
                   base_branch="main", spec_dir="specs/001-x", status="进行中"))

    removed = []
    fake_wt = type("WT", (), {
        "create": staticmethod(lambda p, b, r: "wt"),
        "remove": staticmethod(lambda p, r: removed.append(r)),
        "active_count": staticmethod(lambda p, x=None: 0),
        "gate_open": staticmethod(lambda a, l: a < l),
    })
    fake_git = type("G", (), {
        "add_commit_push": staticmethod(lambda wt, msg, br, push=True: (True, "1 file")),
    })

    orch = Orchestrator(cfg, store, FakeNotifier(), ScriptedRouter({}),
                        worktree=fake_wt, git_ops=fake_git,
                        engine_run=lambda spec, wd, prompt, log: EngineResult.SUCCESS,
                        resolve_worktree=lambda task: str(wt_dir))
    orch.execute("r1")

    assert store.get("r1").status == "已完成"
    assert removed == []   # 未推送 → 保留 worktree


def test_execute_push_false_completion_wording(tmp_path):
    """push: false 时完成文案应是『已提交(未推送)』而非『已推送』。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    cfg.projects["demo"]["push"] = False
    store = JsonTaskStore(cfg.workspace_dir)
    wt_dir = tmp_path / "wt"
    wt_dir.mkdir()
    (wt_dir / "specs" / "001-x").mkdir(parents=True)
    (wt_dir / "specs" / "001-x" / "tasks.md").write_text("- [ ] T001\n")
    store.add(Task(record_id="r1", description="x", priority="p",
                   project="demo", task_title="做事", engine="fake",
                   base_branch="main", spec_dir="specs/001-x", status="进行中"))
    notifier = FakeNotifier()

    seen = {}
    fake_git = type("G", (), {
        "add_commit_push": staticmethod(
            lambda wt, msg, br, push=True: (seen.update(push=push) or (True, "1 file")),
        ),
    })

    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}),
                        git_ops=fake_git,
                        engine_run=lambda spec, wd, prompt, log: EngineResult.SUCCESS,
                        resolve_worktree=lambda task: str(wt_dir))
    orch.execute("r1")

    assert seen["push"] is False                       # push 开关已透传
    assert "已提交(未推送)" in notifier.complete_args["timeline"]
    assert "已推送" not in notifier.complete_args["timeline"]
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


def test_advance_async_launches_worker_without_touching_state(tmp_path):
    """advance_async 只 fire-and-forget 启动后台 worker，不同步推进状态。

    这是绕开 hermes 120s 同步窗口的关键：predict/synthesize 可能跑满 180s，
    必须在脱离的后台进程里跑，hermes 入口本身要秒回。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="给 demo 加功能", priority="p",
                   status="澄清中"))
    notifier = FakeNotifier()
    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}))

    launched = []
    orch._launch_bg = lambda *a: launched.append(a)
    decision = Decision("clarify_submit", "r1", "clarify",
                        form={"scope": "范围"})
    orch.advance_async("r1", decision)

    # 启动了 advance-worker，参数透传正确
    assert len(launched) == 1
    args = launched[0]
    assert args[0] == "advance-worker"
    assert args[1] == "r1"
    assert args[2] == "clarify_submit"
    assert "--stage" in args and "clarify" in args
    assert '"scope"' in args[args.index("--form") + 1]
    # 没有同步发卡、没有改状态——全部留给后台 worker
    assert notifier.calls == []
    assert store.get("r1").status == "澄清中"


def test_advance_clarify_ready_sends_charter(tmp_path):
    """advance clarify_submit（满足立项条件）→ 发 charter 卡，状态变 待立项。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="给 demo 加功能", priority="p",
                   status="澄清中"))
    notifier = FakeNotifier()
    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}),
                        predict_fn=lambda d, p: __import__(
                            "autocoder.core.clarify", fromlist=["Prediction"]
                        ).Prediction([], []))
    decision = Decision("clarify_submit", "r1", "clarify",
                        form={"scope": "范围", "acceptance": "验收"})
    orch.advance("r1", decision)

    assert "charter" in notifier.calls
    assert store.get("r1").status == "待立项"
    assert store.get("r1").task_title == "给 demo 加功能"  # title 兜底


def test_advance_clarify_not_ready_sends_next_round(tmp_path):
    """advance clarify_submit 后 AI 仍有问题 → 再发一轮澄清卡，状态保持 澄清中。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    from autocoder.core.clarify import Prediction, Question, ClarifyOrchestrator
    store.add(Task(record_id="r1", description="x", priority="p", status="澄清中",
                   progress=ClarifyOrchestrator.encode_progress(
                       1, [], [{"key": "scope", "ask": "范围？",
                                "type": "text", "options": []}])))
    notifier = FakeNotifier()
    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}),
                        predict_fn=lambda d, p: Prediction(
                            [], [], questions=[Question(key="timeout",
                                                        ask="超时如何处理？")]))
    # AI 本轮仍给出问题 → ready_to_charter 返回 False
    decision = Decision("clarify_submit", "r1", "clarify",
                        form={"scope": "只改前端"})
    orch.advance("r1", decision)

    assert notifier.calls == ["clarify"]   # 再发一轮
    assert store.get("r1").status == "澄清中"
    dec = ClarifyOrchestrator.decode_progress(store.get("r1").progress)
    assert dec["round"] == 2
    assert dec["qa"] == [{"ask": "范围？", "answer": "只改前端"}]


def test_advance_charter_reject(tmp_path):
    """advance reject_charter → 状态变 已搁置。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="x", priority="p", status="待立项",
                   project="demo", summary="s"))
    notifier = FakeNotifier()
    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}))
    orch.advance("r1", Decision("reject_charter", "r1", "charter"))

    assert store.get("r1").status == "已搁置"
    assert notifier.calls == []


def test_advance_charter_approve_creates_worktree_and_launches_plan(tmp_path):
    """advance approve_charter → 创建 worktree，后台启动 plan，状态变 规划中。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="x", priority="p", status="待立项",
                   project="demo", summary="s"))
    notifier = FakeNotifier()

    created = []
    launched = []
    fake_wt = type("WT", (), {
        "create": staticmethod(lambda p, b, r: created.append(r) or "wt"),
        "remove": staticmethod(lambda p, r: None),
        "active_count": staticmethod(lambda p, x=None: 0),
        "gate_open": staticmethod(lambda a, l: a < l),
    })

    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}), worktree=fake_wt)
    orch._launch_bg = lambda *a: launched.append(a)  # 拦截后台启动

    orch.advance("r1", Decision("approve_charter", "r1", "charter"))

    assert created == ["r1"]
    assert store.get("r1").status == "规划中"
    assert ("plan", "r1") in launched


def test_advance_plan_approve_launches_execute(tmp_path):
    """advance approve_plan（门开着）→ 状态变 进行中，后台启动 execute。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="x", priority="p", status="待审批",
                   project="demo", summary="s", task_title="t", base_branch="main"))
    notifier = FakeNotifier()

    fake_wt = type("WT", (), {
        "create": staticmethod(lambda p, b, r: "wt"),
        "remove": staticmethod(lambda p, r: None),
        "active_count": staticmethod(lambda p, x=None: 0),
        "gate_open": staticmethod(lambda a, l: a < l),
    })

    launched = []
    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}), worktree=fake_wt)
    orch._launch_bg = lambda *a: launched.append(a)

    orch.advance("r1", Decision("approve_plan", "r1", "plan"))

    assert store.get("r1").status == "进行中"
    assert ("execute", "r1") in launched


def test_advance_plan_approve_gate_closed_enqueues(tmp_path):
    """门关着（并发槽满）→ 不执行，状态留待审批、写排队位置+批准时间、发已排队卡。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="给 demo 加功能", priority="p",
                   status="待审批", project="demo", summary="s", task_title="t"))
    notifier = FakeNotifier()

    fake_wt = type("WT", (), {
        "create": staticmethod(lambda p, b, r: "wt"),
        "remove": staticmethod(lambda p, r: None),
        "active_count": staticmethod(lambda p, x=None: 3),  # 满
        "gate_open": staticmethod(lambda a, l: a < l),
    })

    launched = []
    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}), worktree=fake_wt)
    orch._launch_bg = lambda *a: launched.append(a)

    orch.advance("r1", Decision("approve_plan", "r1", "plan"))

    assert launched == []                       # 没执行
    assert store.get("r1").status == "待审批"     # 留在待审批
    assert store.get("r1").queue_position == 3
    assert store.get("r1").approved_at          # FIFO 排序键已写
    assert "queued" in notifier.calls
    assert notifier.queued_position == 3


def test_wake_next_queued_promotes_oldest_first(tmp_path):
    """槽位释放后，按 approved_at FIFO 促活排队最久的一个：转进行中、清排队位、起 execute。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    # r_old 批准更早，应先被促活；r_new 更晚
    store.add(Task(record_id="r_new", description="给 demo 加 B", priority="p",
                   status="待审批", project="demo", queue_position=1,
                   approved_at="2026-06-05 10:00:00"))
    store.add(Task(record_id="r_old", description="给 demo 加 A", priority="p",
                   status="待审批", project="demo", queue_position=0,
                   approved_at="2026-06-05 09:00:00"))
    notifier = FakeNotifier()

    fake_wt = type("WT", (), {
        "create": staticmethod(lambda p, b, r: "wt"),
        "remove": staticmethod(lambda p, r: None),
        "active_count": staticmethod(lambda p, x=None: 0),  # 有空槽
        "gate_open": staticmethod(lambda a, l: a < l),
    })

    launched = []
    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}), worktree=fake_wt)
    orch._launch_bg = lambda *a: launched.append(a)

    orch._wake_next_queued("demo")

    # 只促活最早的一个
    assert launched == [("execute", "r_old")]
    assert store.get("r_old").status == "进行中"
    assert store.get("r_old").queue_position is None
    # 较晚的仍在排队
    assert store.get("r_new").status == "待审批"
    assert store.get("r_new").queue_position == 1


def test_wake_next_queued_noop_when_gate_closed(tmp_path):
    """门仍关着时不促活任何任务。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="给 demo 加功能", priority="p",
                   status="待审批", project="demo", queue_position=3,
                   approved_at="2026-06-05 09:00:00"))
    fake_wt = type("WT", (), {
        "active_count": staticmethod(lambda p, x=None: 3),  # 仍满
        "gate_open": staticmethod(lambda a, l: a < l),
    })
    launched = []
    orch = Orchestrator(cfg, store, FakeNotifier(), ScriptedRouter({}),
                        worktree=fake_wt)
    orch._launch_bg = lambda *a: launched.append(a)

    orch._wake_next_queued("demo")

    assert launched == []
    assert store.get("r1").status == "待审批"
    assert store.get("r1").queue_position == 3


def test_dispatch_feishu_sends_clarify_and_exits(tmp_path):
    """dispatch_feishu → 发澄清卡、写 round:1、状态变 澄清中，立即返回。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="给 demo 加功能", priority="p"))
    notifier = FakeNotifier()

    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}),
                        predict_fn=lambda d, p: __import__(
                            "autocoder.core.clarify", fromlist=["Prediction"]
                        ).Prediction([], []))
    orch.dispatch_feishu()

    assert "clarify" in notifier.calls
    assert store.get("r1").status == "澄清中"
    from autocoder.core.clarify import ClarifyOrchestrator
    assert ClarifyOrchestrator.decode_progress(
        store.get("r1").progress)["round"] == 1


def test_dispatch_feishu_send_failure_keeps_pending(tmp_path):
    """发卡失败时状态须留在「待开始」，不能被孤立在「澄清中」。
    回归：先翻状态再发卡会导致发卡失败后任务既无卡又无法重新 dispatch。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="给 demo 加功能", priority="p"))

    class FailingNotifier(FakeNotifier):
        def send_clarify(self, *a, **k):
            raise RuntimeError("飞书发卡失败 code=230002")

    orch = Orchestrator(cfg, store, FailingNotifier(), ScriptedRouter({}),
                        predict_fn=lambda d, p: __import__(
                            "autocoder.core.clarify", fromlist=["Prediction"]
                        ).Prediction([], []))
    import pytest
    with pytest.raises(RuntimeError):
        orch.dispatch_feishu()

    # 关键断言：发卡失败后任务仍可被重新 dispatch
    assert store.get("r1").status == "待开始"
    assert store.get("r1").progress in ("", None)


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


# ---- monitor: 僵尸任务判定 -------------------------------------------------

def test_is_zombie_pure_function():
    from autocoder.core.orchestrator import is_zombie
    # 只有「超时 且 进程已死」才是僵尸
    assert is_zombie(stale=True, alive=False) is True
    assert is_zombie(stale=True, alive=True) is False   # 还活着，慢任务
    assert is_zombie(stale=False, alive=False) is False  # 没超时
    assert is_zombie(stale=False, alive=True) is False


def _monitor_orch(tmp_path, monkeypatch, alive=False):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    notifier = FakeNotifier()
    # 控制进程信号
    import autocoder.core.process_probe as probe
    monkeypatch.setattr(probe, "worker_alive", lambda rid, kind: alive)
    orch = Orchestrator(cfg, store, notifier, ScriptedRouter({}))
    return orch, store, notifier


def test_monitor_alerts_dead_execute_over_threshold(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    orch, store, notifier = _monitor_orch(tmp_path, monkeypatch, alive=False)
    old = (datetime.now() - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%S")
    store.add(Task(record_id="r1", description="x", priority="p",
                   status="进行中", project="demo", execute_started_at=old))
    orch.monitor()
    assert "zombie" in notifier.calls
    assert notifier.zombie_args["status"] == "进行中"


def test_monitor_no_alert_when_worker_alive(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    orch, store, notifier = _monitor_orch(tmp_path, monkeypatch, alive=True)
    old = (datetime.now() - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%S")
    store.add(Task(record_id="r1", description="x", priority="p",
                   status="进行中", project="demo", execute_started_at=old))
    orch.monitor()
    assert "zombie" not in notifier.calls   # 进程活着 → 不是僵尸


def test_monitor_no_alert_within_threshold(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    orch, store, notifier = _monitor_orch(tmp_path, monkeypatch, alive=False)
    recent = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
    store.add(Task(record_id="r1", description="x", priority="p",
                   status="进行中", project="demo", execute_started_at=recent))
    orch.monitor()
    assert "zombie" not in notifier.calls   # 未超时 → 不告警


def test_monitor_alerts_dead_plan_over_threshold(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    orch, store, notifier = _monitor_orch(tmp_path, monkeypatch, alive=False)
    old = (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")
    store.add(Task(record_id="r1", description="x", priority="p",
                   status="规划中", project="demo", plan_started_at=old))
    orch.monitor()
    assert "zombie" in notifier.calls
    assert notifier.zombie_args["kind"] == "plan"


def test_monitor_alerts_clarify_without_card(tmp_path, monkeypatch):
    orch, store, notifier = _monitor_orch(tmp_path, monkeypatch, alive=False)
    # 澄清中但 progress 为空 → 发卡前崩溃，无卡可点
    store.add(Task(record_id="r1", description="x", priority="p",
                   status="澄清中", project="demo", progress=""))
    orch.monitor()
    assert "zombie" in notifier.calls
    assert notifier.zombie_args["status"] == "澄清中"


def test_monitor_skips_clarify_with_card(tmp_path, monkeypatch):
    orch, store, notifier = _monitor_orch(tmp_path, monkeypatch, alive=False)
    # 澄清中且有 progress（卡已发）→ 正常等待用户，不告警
    store.add(Task(record_id="r1", description="x", priority="p",
                   status="澄清中", project="demo",
                   progress='{"round":1,"qa":[],"pending":[{"key":"k","ask":"a"}]}'))
    orch.monitor()
    assert "zombie" not in notifier.calls


# ---- advance: 僵尸/失败重试 -------------------------------------------------

def _retry_orch(tmp_path, status, **task_kw):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = _config(tmp_path, proj)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="x", priority="p", status=status,
                   project="demo", summary="s", task_title="t",
                   base_branch="main", **task_kw))
    fake_wt = type("WT", (), {
        "create": staticmethod(lambda p, b, r: "wt"),
        "remove": staticmethod(lambda p, r: None),
        "active_count": staticmethod(lambda p, x=None: 0),
        "gate_open": staticmethod(lambda a, l: a < l),
    })
    orch = Orchestrator(cfg, store, FakeNotifier(), ScriptedRouter({}), worktree=fake_wt)
    launched = []
    orch._launch_bg = lambda *a: launched.append(a)
    return orch, store, launched


def test_advance_retry_execute_from_stalled(tmp_path):
    """已停滞任务点重试执行 → 回到进行中并重新启动 execute worker。"""
    orch, store, launched = _retry_orch(tmp_path, "已停滞")
    orch.advance("r1", Decision("retry_execute", "r1", "execute"))
    assert store.get("r1").status == "进行中"
    assert ("execute", "r1") in launched


def test_advance_retry_execute_from_zombie_running(tmp_path):
    """进行中(僵尸)点重试执行 → 仍是进行中，重新启动 execute。"""
    orch, store, launched = _retry_orch(tmp_path, "进行中")
    orch.advance("r1", Decision("retry_execute", "r1", "execute"))
    assert store.get("r1").status == "进行中"
    assert ("execute", "r1") in launched


def test_advance_retry_plan_from_stalled(tmp_path):
    """规划阶段失败/卡死点重试 → 回到规划中并重启 plan worker。"""
    orch, store, launched = _retry_orch(tmp_path, "已停滞")
    orch.advance("r1", Decision("retry_plan", "r1", "plan"))
    assert store.get("r1").status == "规划中"
    assert ("plan", "r1") in launched


def test_advance_reclarify_restarts_clarify(tmp_path):
    """澄清无卡可点 → reclarify 重启澄清第 1 轮。"""
    orch, store, launched = _retry_orch(tmp_path, "澄清中", progress="")
    orch.clarify.predict = lambda *a, **k: __import__(
        "autocoder.core.clarify", fromlist=["Prediction"]).Prediction([], [])
    orch.advance("r1", Decision("reclarify", "r1", "clarify"))
    assert store.get("r1").status == "澄清中"
    assert "clarify" in orch.notifier.calls

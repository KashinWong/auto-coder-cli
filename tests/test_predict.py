from autocoder.core.orchestrator import Orchestrator
from autocoder.adapters.store import JsonTaskStore
from autocoder.core.clarify import Prediction


class _Cfg:
    def __init__(self, tmp_path, project_path):
        self.workspace_dir = str(tmp_path / "ws")
        self.concurrency_limit = 3
        self.default_engine = "fake"
        self.projects = {"demo": {"path": str(project_path), "engine": "fake"}}
        self.engines = {"fake": {"command": "true", "args": [], "timeout": 5, "env": {}}}
        self.feishu = {}

    def engine_spec(self, name):
        return self.engines[name]

    def match_project(self, text):
        return "demo" if "demo" in text else None


def _orch(tmp_path, project_path, capture):
    cfg = _Cfg(tmp_path, project_path)
    store = JsonTaskStore(cfg.workspace_dir)
    notifier = type("N", (), {})()
    router = type("R", (), {})()
    return Orchestrator(cfg, store, notifier, router,
                        worktree=type("WT", (), {})(),
                        engine_capture=capture)


def test_engine_predict_parses_json(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    out = '前言\n{"modules": ["a.py", "b.py"], "risks": ["并发风险"], ' \
          '"scope_hint": "只做悔棋确认", "acceptance_hint": "对方确认后才回退", ' \
          '"ready": false, "ready_reason": "超时行为未定", ' \
          '"questions": [' \
          '{"key": "timeout", "ask": "超时怎么处理？", "type": "single_select", ' \
          '"options": ["默认拒绝", "默认同意"]}, ' \
          '{"key": "limit", "ask": "拒绝后能再发起吗？", "type": "text"}' \
          ']}\n后语'
    orch = _orch(tmp_path, proj, lambda *a, **k: out)
    pred = orch._engine_predict("加悔棋确认", str(proj))
    assert pred.modules == ["a.py", "b.py"]
    assert pred.risks == ["并发风险"]
    assert pred.scope_hint == "只做悔棋确认"
    assert pred.acceptance_hint == "对方确认后才回退"
    assert pred.ready is False
    assert pred.ready_reason == "超时行为未定"
    assert len(pred.questions) == 2
    q0 = pred.questions[0]
    assert q0.key == "timeout"
    assert q0.ask == "超时怎么处理？"
    assert q0.type == "single_select"
    assert q0.options == ["默认拒绝", "默认同意"]
    assert pred.questions[1].type == "text"


def test_engine_predict_select_without_options_degrades_to_text(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    out = '{"questions": [{"key": "x", "ask": "选啥？", "type": "single_select"}]}'
    orch = _orch(tmp_path, proj, lambda *a, **k: out)
    pred = orch._engine_predict("加功能", str(proj))
    assert pred.questions[0].type == "text"


def test_engine_predict_caps_questions_at_three(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    qs = ", ".join(
        '{"key": "q%d", "ask": "问题%d", "type": "text"}' % (i, i)
        for i in range(5))
    out = '{"questions": [%s]}' % qs
    orch = _orch(tmp_path, proj, lambda *a, **k: out)
    pred = orch._engine_predict("加功能", str(proj))
    assert len(pred.questions) == 3


def test_engine_predict_degrades_on_garbage(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    orch = _orch(tmp_path, proj, lambda *a, **k: "这不是 JSON")
    pred = orch._engine_predict("加功能", str(proj))
    assert pred.modules == []
    assert pred.risks == []


def test_engine_predict_degrades_on_empty(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    orch = _orch(tmp_path, proj, lambda *a, **k: "")
    pred = orch._engine_predict("加功能", str(proj))
    assert isinstance(pred, Prediction)
    assert pred.modules == []


def test_engine_predict_no_path_returns_empty(tmp_path):
    orch = _orch(tmp_path, tmp_path / "proj", lambda *a, **k: "{}")
    pred = orch._engine_predict("加功能", "")
    assert pred.modules == []


def test_engine_predict_feeds_prior_form_into_prompt(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    captured = {}

    def capture(spec, working_dir, prompt, timeout=None):
        captured["prompt"] = prompt
        return "{}"

    orch = _orch(tmp_path, proj, capture)
    orch._engine_predict("加功能", str(proj),
                         prior_qa=[{"ask": "范围？", "answer": "只改前端"}])
    assert "只改前端" in captured["prompt"]

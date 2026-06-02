import builtins
from autocoder.adapters.router import CliRouter
from autocoder.models import Decision


def test_clarify_decision_collects_form(monkeypatch):
    # 模拟用户依次输入 5 个澄清维度的回答
    answers = iter(["只改前端", "auth.py", "能登录即可", "无 deadline", "无风险"])
    monkeypatch.setattr(builtins, "input", lambda *a: next(answers))
    d = CliRouter().await_decision("r1", stage="clarify")
    assert isinstance(d, Decision)
    assert d.action == "clarify_submit"
    assert d.form["scope"] == "只改前端"
    assert d.form["modules"] == "auth.py"


def test_charter_approve(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "1")
    d = CliRouter().await_decision("r1", stage="charter")
    assert d.action == "approve_charter"


def test_charter_reject(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "4")
    d = CliRouter().await_decision("r1", stage="charter")
    assert d.action == "reject_charter"


def test_plan_approve(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "1")
    d = CliRouter().await_decision("r1", stage="plan")
    assert d.action == "approve_plan"


def test_plan_revise_collects_input(monkeypatch):
    answers = iter(["2", "把第三个任务拆开"])
    monkeypatch.setattr(builtins, "input", lambda *a: next(answers))
    d = CliRouter().await_decision("r1", stage="plan")
    assert d.action == "revise_plan"
    assert d.input_text == "把第三个任务拆开"

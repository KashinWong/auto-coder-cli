from autocoder.models import Task, Decision


def test_task_defaults():
    t = Task(record_id="r1", description="加个登录按钮", priority="重要紧急")
    assert t.record_id == "r1"
    assert t.status == "待开始"
    assert t.summary == ""
    assert t.spec_dir is None


def test_task_branch_name():
    t = Task(record_id="abc", description="x", priority="p")
    assert t.branch_name() == "feature/auto-abc"


def test_task_stage_timestamps_default_none():
    t = Task(record_id="r1", description="x", priority="p")
    assert t.execute_started_at is None
    assert t.plan_started_at is None


def test_decision_holds_action_and_form():
    d = Decision(action="clarify_submit", record_id="r1",
                 form={"scope": "只改前端"})
    assert d.action == "clarify_submit"
    assert d.form["scope"] == "只改前端"

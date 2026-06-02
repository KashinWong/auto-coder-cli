from autocoder.core.clarify import ClarifyOrchestrator, Prediction


def test_predict_uses_injected_fn():
    fake = lambda desc, project_path: Prediction(
        modules=["auth.py", "session.py"], risks=["会话兼容性"])
    orch = ClarifyOrchestrator(predict_fn=fake)
    pred = orch.predict("加登录", "/tmp/proj")
    assert pred.modules == ["auth.py", "session.py"]
    assert pred.risks == ["会话兼容性"]


def test_ready_to_charter_when_form_filled():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    form = {"scope": "只改前端", "acceptance": "能登录",
            "constraints": "无", "modules": "auth", "risk_reply": "无"}
    assert orch.ready_to_charter(form, round_no=1) is True


def test_ready_to_charter_forced_at_round_3():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    assert orch.ready_to_charter({}, round_no=3) is True


def test_not_ready_when_empty_form_early_round():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    # 多维度留空且非末轮且需求非自描述 → 再问一轮
    assert orch.ready_to_charter({"scope": "", "acceptance": ""}, round_no=1) is False


def test_empty_form_but_trivial_task_charters_round_1():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    # 无 _form 但需求自描述（短、单文件文档改动）→ 立即立项
    assert orch.ready_to_charter(None, round_no=1, trivial=True) is True


def test_synthesize_statement():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    form = {"scope": "只改前端登录页", "acceptance": "点击能跳转"}
    stmt = orch.synthesize("加登录按钮", form)
    assert "加登录按钮" in stmt
    assert "只改前端登录页" in stmt
    assert "点击能跳转" in stmt

from autocoder.core.clarify import ClarifyOrchestrator, Prediction, Question


def test_predict_uses_injected_fn():
    fake = lambda desc, project_path: Prediction(
        modules=["auth.py", "session.py"], risks=["会话兼容性"])
    orch = ClarifyOrchestrator(predict_fn=fake)
    pred = orch.predict("加登录", "/tmp/proj")
    assert pred.modules == ["auth.py", "session.py"]
    assert pred.risks == ["会话兼容性"]


def test_ready_when_ai_says_ready():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    pred = Prediction([], [], ready=True, ready_reason="信息已足够")
    assert orch.ready_to_charter(pred, round_no=1) is True


def test_ready_when_ai_has_no_questions():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    # AI 未显式 ready，但已问不出任何问题 → 视为够了
    pred = Prediction([], [], ready=False, questions=[])
    assert orch.ready_to_charter(pred, round_no=1) is True


def test_not_ready_when_ai_still_has_questions():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    pred = Prediction([], [], ready=False,
                      questions=[Question(key="q", ask="超时如何处理？")])
    assert orch.ready_to_charter(pred, round_no=1) is False


def test_ready_forced_at_max_rounds():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    # 即使 AI 还想问，硬上限（5 轮）也强制立项，防引擎抽风死循环
    pred = Prediction([], [], questions=[Question(key="q", ask="还有问题")])
    assert orch.ready_to_charter(pred, round_no=5) is True


def test_ready_when_trivial():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    assert orch.ready_to_charter(None, round_no=1, trivial=True) is True


def test_failed_prediction_not_ready_even_without_questions():
    """Bug1 修复：预判失败降级（引擎超时/崩溃）时，即使 questions=[] 也不能立项。
    失败意味着“不知道”，与“AI 判定无需提问”有本质区别。
    仅由 _MAX_ROUNDS 收口兜底，避免跳过澄清。"""
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    pred = Prediction([], [], ready=False, questions=[], ok=False)
    assert orch.ready_to_charter(pred, round_no=1) is False


def test_failed_prediction_still_rejected_with_questions():
    """失败降级 + 刚好也带了问题（合并路径考虑）。"""
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    pred = Prediction([], [], ready=False,
                      questions=[Question(key="q", ask="超时？")],
                      ok=False)
    assert orch.ready_to_charter(pred, round_no=1) is False


def test_failed_prediction_still_forced_at_max_rounds():
    """MAX_ROUNDS 兜底：即使 ok=False，到第 5 轮也必须立项。"""
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    pred = Prediction([], [], questions=[], ok=False)
    assert orch.ready_to_charter(pred, round_no=5) is True


def test_ok_defaults_to_true_backward_compat():
    """未显式设 ok 的 Prediction 默认为 True，保持与修复前一致的行为。"""
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    # 正常预判——无问题，ready=true。
    pred = Prediction([], [], ready=True, ready_reason="够了")
    assert orch.ready_to_charter(pred, round_no=1) is True
    # AI 主动判定无需提问。
    pred2 = Prediction([], [], ready=False, questions=[])
    assert orch.ready_to_charter(pred2, round_no=1) is True


def test_synthesize_from_qa():
    orch = ClarifyOrchestrator(predict_fn=lambda d, p: Prediction([], []))
    qa = [
        {"ask": "范围边界？", "answer": "只改前端登录页"},
        {"ask": "验收标准？", "answer": "点击能跳转"},
        {"ask": "涉及模块？", "answer": ["a.js", "b.js"]},
    ]
    stmt = orch.synthesize("加登录按钮", qa)
    assert "加登录按钮" in stmt
    assert "只改前端登录页" in stmt
    assert "点击能跳转" in stmt
    assert "a.js、b.js" in stmt


def test_progress_roundtrip():
    qa = [{"ask": "Q1", "answer": "A1"}]
    pending = [{"key": "q2", "ask": "Q2", "type": "text", "options": []}]
    enc = ClarifyOrchestrator.encode_progress(2, qa, pending)
    dec = ClarifyOrchestrator.decode_progress(enc)
    assert dec["round"] == 2
    assert dec["qa"] == qa
    assert dec["pending"] == pending


def test_progress_decode_backward_compat_round_n():
    # 旧格式 "round:N" 仍可解码，qa/pending 退化为空
    dec = ClarifyOrchestrator.decode_progress("round:2")
    assert dec["round"] == 2
    assert dec["qa"] == []
    assert dec["pending"] == []


def test_progress_decode_empty_and_garbage():
    for bad in (None, "", "not json"):
        dec = ClarifyOrchestrator.decode_progress(bad)
        assert dec["round"] == 1
        assert dec["qa"] == []
        assert dec["pending"] == []

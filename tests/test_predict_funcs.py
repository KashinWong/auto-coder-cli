"""predict.py 纯函数级测试：prompt 构建（含角色框架）与输出解析。

这些用例锁定抽取后的行为契约，与 orchestrator 级的 test_predict.py 互补：
那边测 _engine_predict 端到端，这边测两个纯函数本身。
"""
from autocoder.core.predict import build_predict_prompt, parse_prediction
from autocoder.core.clarify import Role


def test_build_prompt_no_role_is_baseline():
    """role=None 时 prompt 与改造前一致：不含角色框架，以澄清助手开头。"""
    p = build_predict_prompt("加悔棋确认", None)
    assert p.startswith("你是需求澄清助手。")
    assert "你的身份是" not in p
    assert "需求：加悔棋确认" in p


def test_build_prompt_with_role_prepends_framing():
    """role 非空 → 开头追加角色身份与关注点，但主体 schema 保持不变。"""
    role = Role(name="产品经理", focus="需求范围边界、用户价值、验收标准")
    p = build_predict_prompt("加悔棋确认", None, role=role)
    assert p.startswith("你的身份是「产品经理」。")
    assert "需求范围边界、用户价值、验收标准" in p
    # 主体仍是同一份预判 schema。
    assert "你是需求澄清助手。" in p
    assert '"modules"' in p and '"questions"' in p


def test_build_prompt_includes_prior_qa():
    qa = [{"ask": "超时怎么处理？", "answer": "默认拒绝"}]
    p = build_predict_prompt("加悔棋", qa)
    assert "用户已在前几轮澄清中回答了以下问题" in p
    assert "默认拒绝" in p


def test_parse_strips_codeblock_and_surrounding_text():
    out = '前言\n```json\n{"modules": ["a.py"], "risks": [], "ready": true}\n```\n后语'
    pred = parse_prediction(out)
    assert pred.modules == ["a.py"]
    assert pred.ready is True


def test_parse_caps_questions_at_three():
    qs = ", ".join(
        '{"key": "q%d", "ask": "问题%d？", "type": "text"}' % (i, i)
        for i in range(5)
    )
    out = '{"modules": [], "risks": [], "questions": [%s]}' % qs
    pred = parse_prediction(out)
    assert len(pred.questions) == 3


def test_parse_select_without_options_degrades_to_text():
    out = ('{"modules": [], "risks": [], "questions": ['
           '{"key": "k", "ask": "选哪个？", "type": "single_select"}]}')
    pred = parse_prediction(out)
    assert pred.questions[0].type == "text"


def test_parse_legacy_string_questions():
    out = '{"modules": [], "risks": [], "questions": ["纯字符串问题？"]}'
    pred = parse_prediction(out)
    assert pred.questions[0].ask == "纯字符串问题？"
    assert pred.questions[0].type == "text"


def test_parse_empty_or_garbage_returns_empty_prediction():
    for bad in ["", "   ", "no json here", "{not valid}"]:
        pred = parse_prediction(bad)
        assert pred.modules == [] and pred.questions == []

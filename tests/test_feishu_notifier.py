from autocoder.adapters.feishu.client import render_card


def test_render_card_substitutes_tokens(tmp_path):
    tpl = tmp_path / "clarify.json"
    tpl.write_text('{"title": "{{TASK_TITLE}}", "round": "{{ROUND}}"}')
    out = render_card(str(tpl), TASK_TITLE="加登录", ROUND="1")
    assert '"title": "加登录"' in out
    assert '"round": "1"' in out


def test_render_card_escapes_newlines(tmp_path):
    tpl = tmp_path / "c.json"
    tpl.write_text('{"body": "{{SUMMARY}}"}')
    out = render_card(str(tpl), SUMMARY="第一行\n第二行")
    # 换行被 JSON 转义，渲染结果仍是合法 JSON
    import json
    json.loads(out)


def test_send_clarify_renders_ai_hints_into_valid_card():
    import json
    from autocoder.adapters.feishu.notifier import FeishuCardNotifier
    from autocoder.core.clarify import Prediction, Question
    from autocoder.models import Task

    sent = {}

    class FakeClient:
        def send_card(self, chat_id, card):
            sent["card"] = card
            return {"code": 0}

    notifier = FeishuCardNotifier({"notify_chat_id": "oc_x"}, client=FakeClient())
    task = Task(record_id="rec1", description="加悔棋确认", priority="重要紧急")
    pred = Prediction(modules=["game.js"], risks=["并发回退"],
                      scope_hint="只做悔棋确认", acceptance_hint="对方确认后回退",
                      questions=[Question(key="timeout", ask="超时如何处理？")])
    notifier.send_clarify(task, pred, round_no=2)

    card = json.loads(sent["card"])  # 渲染产物必须是合法 JSON
    raw = sent["card"]
    assert "只做悔棋确认" in raw
    assert "对方确认后回退" in raw
    assert "并发回退" in raw
    assert "超时如何处理？" in raw
    assert "game.js" in raw


def test_send_clarify_empty_prediction_still_valid_card():
    import json
    from autocoder.adapters.feishu.notifier import FeishuCardNotifier
    from autocoder.core.clarify import Prediction
    from autocoder.models import Task

    sent = {}

    class FakeClient:
        def send_card(self, chat_id, card):
            sent["card"] = card
            return {"code": 0}

    notifier = FeishuCardNotifier({"notify_chat_id": "oc_x"}, client=FakeClient())
    task = Task(record_id="rec1", description="加功能", priority="重要紧急")
    notifier.send_clarify(task, Prediction([], []), round_no=1)
    json.loads(sent["card"])  # 空预判也不能产出非法 JSON

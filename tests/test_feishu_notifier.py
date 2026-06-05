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


def test_send_clarify_select_question_has_other_and_global_supplement():
    """选项题须配 {key}__other 自定义框；整卡须有 __supplement 全局补充框，
    且 name 与 _merge_answers 解析的键严格对齐。"""
    import json
    from autocoder.adapters.feishu.notifier import FeishuCardNotifier
    from autocoder.core.clarify import Prediction, Question
    from autocoder.models import Task

    captured = {}

    class FakeClient:
        def send_card(self, chat_id, card):
            captured["card"] = card
            return {"code": 0}

    notifier = FeishuCardNotifier({"notify_chat_id": "oc_x"}, client=FakeClient())
    task = Task(record_id="rec1", description="加功能", priority="重要紧急")
    pred = Prediction([], [], questions=[
        Question(key="scope", ask="范围？", type="single_select",
                 options=["前端", "后端"])])
    notifier.send_clarify(task, pred, round_no=1)

    card = json.loads(captured["card"])
    raw = captured["card"]
    # 选项题下方有 scope__other 输入框
    assert '"scope__other"' in raw
    # 整卡级补充框，name 必须是 __supplement
    assert '"__supplement"' in raw
    assert "其他补充说明" in raw


def test_send_zombie_alert_execute_renders_retry_action():
    import json
    from autocoder.adapters.feishu.notifier import FeishuCardNotifier
    from autocoder.models import Task

    sent = {}

    class FakeClient:
        def send_card(self, chat_id, card):
            sent["card"] = card
            return {"code": 0}

    notifier = FeishuCardNotifier({"notify_chat_id": "oc_x"}, client=FakeClient())
    task = Task(record_id="rec1", description="加功能", priority="重要紧急",
                status="进行中", execute_started_at="2026-06-05T10:00:00")
    notifier.send_zombie_alert(task, "进行中", "execute", 60)

    raw = sent["card"]
    json.loads(raw)                      # 合法 JSON
    assert "{{" not in raw               # 无残留 token
    assert "retry_execute" in raw        # 进行中 → 重试执行
    assert "rec1" in raw


def test_send_zombie_alert_plan_uses_retry_plan():
    import json
    from autocoder.adapters.feishu.notifier import FeishuCardNotifier
    from autocoder.models import Task

    sent = {}

    class FakeClient:
        def send_card(self, chat_id, card):
            sent["card"] = card
            return {"code": 0}

    notifier = FeishuCardNotifier({"notify_chat_id": "oc_x"}, client=FakeClient())
    task = Task(record_id="rec2", description="x", priority="p", status="规划中")
    notifier.send_zombie_alert(task, "规划中", "plan", 20)

    raw = sent["card"]
    json.loads(raw)
    assert "{{" not in raw
    assert "retry_plan" in raw


def test_send_zombie_alert_clarify_uses_reclarify():
    import json
    from autocoder.adapters.feishu.notifier import FeishuCardNotifier
    from autocoder.models import Task

    sent = {}

    class FakeClient:
        def send_card(self, chat_id, card):
            sent["card"] = card
            return {"code": 0}

    notifier = FeishuCardNotifier({"notify_chat_id": "oc_x"}, client=FakeClient())
    task = Task(record_id="rec3", description="x", priority="p", status="澄清中")
    notifier.send_zombie_alert(task, "澄清中", "clarify", 0)

    raw = sent["card"]
    json.loads(raw)
    assert "{{" not in raw
    assert "reclarify" in raw


def test_send_failure_execute_stage_has_retry_execute_button():
    import json
    from autocoder.adapters.feishu.notifier import FeishuCardNotifier
    from autocoder.models import Task

    sent = {}

    class FakeClient:
        def send_card(self, chat_id, card):
            sent["card"] = card
            return {"code": 0}

    notifier = FeishuCardNotifier({"notify_chat_id": "oc_x"}, client=FakeClient())
    task = Task(record_id="rec1", description="x", priority="p")
    notifier.send_failure(task, "测试", "测试失败", "/tmp/log", "feature/auto-rec1")

    raw = sent["card"]
    json.loads(raw)
    assert "{{" not in raw
    assert "retry_execute" in raw   # 测试/构建/编码/推送阶段 → 重试执行


def test_send_failure_plan_stage_has_retry_plan_button():
    import json
    from autocoder.adapters.feishu.notifier import FeishuCardNotifier
    from autocoder.models import Task

    sent = {}

    class FakeClient:
        def send_card(self, chat_id, card):
            sent["card"] = card
            return {"code": 0}

    notifier = FeishuCardNotifier({"notify_chat_id": "oc_x"}, client=FakeClient())
    task = Task(record_id="rec2", description="x", priority="p")
    notifier.send_failure(task, "立项", "无法匹配目标项目", "", "")

    raw = sent["card"]
    json.loads(raw)
    assert "{{" not in raw
    assert "retry_plan" in raw   # 立项/规划阶段 → 重试规划

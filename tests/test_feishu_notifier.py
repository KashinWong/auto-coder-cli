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

"""澄清预判的 prompt 构建与输出解析（无状态纯函数）。

从 orchestrator 抽出，供单角色预判与多角色 fan-out（role_predict）共用。
抽到底层模块是为了让依赖单向：orchestrator → role_predict → predict，
避免 role_predict 反向 import orchestrator 造成循环依赖。
"""
import json as _json
import re

from autocoder.core.clarify import Prediction, Question


def build_predict_prompt(description, prior_qa, role=None):
    """构建预判 prompt。

    role 为 None 时输出与改造前 orchestrator._build_predict_prompt 逐字一致，
    保证单角色路径零回归。role 非 None（含 name/focus）时，在开头追加角色框架，
    让该角色从自己的关注点侧重分析。
    """
    lines = []
    if role is not None:
        lines += [
            f"你的身份是「{role.name}」。请站在该角色立场，"
            f"重点关注：{role.focus}。",
            "",
        ]
    lines += [
        "你是需求澄清助手。请先快速浏览当前项目代码结构，再对下面的需求做预判。",
        "",
        f"需求：{description}",
        "",
    ]
    if prior_qa:
        lines += [
            "用户已在前几轮澄清中回答了以下问题，请据此判断信息是否已足够立项；",
            "已答的不要再问，只针对仍然影响实现/验收的关键缺口提问：",
            _json.dumps(prior_qa, ensure_ascii=False),
            "",
        ]
    lines += [
        "只输出一段 JSON（不要任何额外文字、不要 markdown 代码块），字段：",
        '{',
        '  "modules": ["预判涉及的文件或模块名", ...],',
        '  "risks": ["实现该需求的技术风险点", ...],',
        '  "scope_hint": "一句话预判范围边界：做什么/明确不做什么",',
        '  "acceptance_hint": "一句话预判验收标准：怎样算完成",',
        '  "ready": true/false,',
        '  "ready_reason": "一句话说明为何够了/还缺什么",',
        '  "questions": [',
        '    {"key": "短标识", "ask": "要问用户的问题",',
        '     "type": "text | single_select | multi_select",',
        '     "options": ["仅 select 类型需要的候选项", ...]}',
        '  ]',
        '}',
        "要求：",
        "- modules 必须是项目里真实存在的文件/模块。",
        "- 只问真正阻断实现或验收的关键点，能从代码/需求推断的不要问。",
        "- 最多提 3 个问题；信息已足够时 ready=true 且 questions 为空数组。",
        "- 能用选项回答的尽量用 single_select/multi_select，减轻用户负担。",
    ]
    return "\n".join(lines)


def parse_prediction(out):
    """解析引擎输出的 JSON 为 Prediction。失败一律降级为空预判。

    与改造前 orchestrator._parse_prediction 行为一致：抓第一个 {...} 块、
    兼容旧式纯字符串问题、select 无候选项退化为 text、问题截断至 3 个。
    """
    if not out or not out.strip():
        return Prediction([], [], ok=False)
    # 引擎可能裹了 ```json ``` 或夹带前后文字，抓第一个 {...} 块。
    text = out.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return Prediction([], [], ok=False)
    try:
        data = _json.loads(m.group(0))
    except (ValueError, TypeError):
        return Prediction([], [], ok=False)
    if not isinstance(data, dict):
        return Prediction([], [], ok=False)

    def _as_list(v):
        if isinstance(v, list):
            return [str(x) for x in v if str(x).strip()]
        if isinstance(v, str) and v.strip():
            return [v.strip()]
        return []

    def _parse_questions(v):
        qs = []
        if not isinstance(v, list):
            return qs
        for i, item in enumerate(v):
            # 兼容引擎仍按旧格式返回纯字符串问题的情况。
            if isinstance(item, str):
                if item.strip():
                    qs.append(Question(key=f"q{i}", ask=item.strip()))
                continue
            if not isinstance(item, dict):
                continue
            ask = str(item.get("ask", "") or "").strip()
            if not ask:
                continue
            qtype = str(item.get("type", "text") or "text").strip()
            if qtype not in ("text", "single_select", "multi_select"):
                qtype = "text"
            opts = _as_list(item.get("options"))
            if qtype != "text" and not opts:
                qtype = "text"  # select 无候选项则退化为文本
            key = str(item.get("key", "") or f"q{i}").strip() or f"q{i}"
            qs.append(Question(key=key, ask=ask, type=qtype, options=opts))
        return qs[:3]  # 每张卡最多 3 个问题

    return Prediction(
        modules=_as_list(data.get("modules")),
        risks=_as_list(data.get("risks")),
        scope_hint=str(data.get("scope_hint", "") or "").strip(),
        acceptance_hint=str(data.get("acceptance_hint", "") or "").strip(),
        ready=bool(data.get("ready", False)),
        ready_reason=str(data.get("ready_reason", "") or "").strip(),
        questions=_parse_questions(data.get("questions")),
    )

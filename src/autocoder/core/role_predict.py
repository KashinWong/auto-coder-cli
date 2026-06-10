"""澄清预判的多角色并行 fan-out。

在编排层用线程池并行启动多个角色（产品经理/架构师/测试工程师……），
每个角色各自读项目代码产出一份预判，再用一次「不扫项目」的综合调用收敛成
单个 Prediction。综合失败则用确定性兜底合并。

依赖方向：orchestrator → role_predict → predict（单向，不反向 import）。

容错三级（绝不阻断澄清）：
  1. 单角色失败/超时 → 该角色记空、丢弃，不影响其他角色。
  2. 所有角色都空 → 返回空 Prediction（与现有「预判失败降级」一致）。
  3. 综合步失败/超时 → 走 merge_predictions 确定性合并，不丢角色成果。
"""
from concurrent.futures import ThreadPoolExecutor

from autocoder.core.clarify import Prediction, Question, Role
from autocoder.core.predict import build_predict_prompt, parse_prediction

# 只写 clarify_fanout: {enabled: true} 即用这套默认角色。
DEFAULT_ROLES = [
    Role(name="产品经理", focus="需求范围边界、用户价值、验收标准（做什么/不做什么）"),
    Role(name="架构师", focus="涉及的真实模块、集成点、技术风险与改动影响面"),
    Role(name="测试工程师", focus="边界条件、异常路径、判定完成的可验证验收用例"),
]


def load_roles(fanout_cfg):
    """从项目的 clarify_fanout 配置取角色集；未配 roles 用 DEFAULT_ROLES。

    返回空列表表示无可用角色，调用方应回退到单角色路径。
    """
    if not fanout_cfg:
        return []
    raw = fanout_cfg.get("roles")
    if not raw:
        return list(DEFAULT_ROLES)
    roles = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        focus = str(item.get("focus", "") or "").strip()
        if name and focus:
            roles.append(Role(name=name, focus=focus))
    return roles or list(DEFAULT_ROLES)


def _is_empty(pred):
    """角色无有效产出的判定：模块/风险/问题全空且无范围验收看法。"""
    return not (pred.modules or pred.risks or pred.questions
                or pred.scope_hint or pred.acceptance_hint)


def build_synthesis_prompt(description, role_preds):
    """综合步 prompt：只对各角色文本归并去重，明确不再扫描项目。"""
    import json as _json
    role_blocks = []
    for i, p in enumerate(role_preds):
        role_blocks.append(_json.dumps({
            "modules": p.modules,
            "risks": p.risks,
            "scope_hint": p.scope_hint,
            "acceptance_hint": p.acceptance_hint,
            "ready": p.ready,
            "ready_reason": p.ready_reason,
            "questions": [
                {"key": q.key, "ask": q.ask, "type": q.type, "options": q.options}
                for q in p.questions
            ],
        }, ensure_ascii=False))
    lines = [
        "你是需求澄清的综合者。下面是多个角色对同一需求各自给出的预判 JSON。",
        "不要再读项目代码，只对这些预判做归并：合并去重 modules 与 risks；",
        "scope_hint / acceptance_hint 取最具体的综合表述；",
        "questions 跨角色去重，按「是否真正阻断实现或验收」排序，最多保留 3 个；",
        "若各角色普遍认为信息已足够、且无真正阻断性问题，则 ready=true 且 questions 为空。",
        "",
        f"需求：{description}",
        "",
        "各角色预判：",
        *role_blocks,
        "",
        "只输出一段 JSON（不要任何额外文字、不要 markdown 代码块），字段与单角色预判一致：",
        '{"modules": [...], "risks": [...], "scope_hint": "...", '
        '"acceptance_hint": "...", "ready": true/false, "ready_reason": "...", '
        '"questions": [{"key": "...", "ask": "...", "type": "...", "options": [...]}]}',
    ]
    return "\n".join(lines)


def _dedup(seq):
    """保序去重。"""
    seen = set()
    out = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def merge_predictions(role_preds):
    """确定性兜底合并：综合步失败时用，保证不丢角色成果且守住 ≤3 问。

    - modules/risks：各角色保序并集去重。
    - scope_hint/acceptance_hint：按角色顺序取第一个非空（产品经理优先）。
    - ready：所有非空角色都 ready，或无人提问，才 ready（保守，倾向继续澄清）。
    - questions：按 ask 文本去重保序，截断至 3。
    """
    modules, risks = [], []
    scope_hint = acceptance_hint = ""
    ready_reasons = []
    questions = []
    seen_ask = set()
    for p in role_preds:
        modules += p.modules
        risks += p.risks
        if not scope_hint and p.scope_hint:
            scope_hint = p.scope_hint
        if not acceptance_hint and p.acceptance_hint:
            acceptance_hint = p.acceptance_hint
        if p.ready_reason:
            ready_reasons.append(p.ready_reason)
        for q in p.questions:
            if q.ask not in seen_ask:
                seen_ask.add(q.ask)
                questions.append(q)
    ready = bool(role_preds) and (all(p.ready for p in role_preds)
                                  or not questions)
    return Prediction(
        modules=_dedup(modules),
        risks=_dedup(risks),
        scope_hint=scope_hint,
        acceptance_hint=acceptance_hint,
        ready=ready,
        ready_reason="；".join(_dedup(ready_reasons)),
        questions=questions[:3],
    )


def fanout_predict(*, roles, description, prior_qa, spec, project_path,
                   timeout, engine_capture, synth_timeout=None):
    """多角色并行预判 + 综合，返回单个 Prediction。

    roles 为空时调用方不应进入此函数（应走单角色路径），这里防御性返回空预判。
    """
    if not roles:
        return Prediction([], [])

    def _run_role(role):
        # 单角色失败/超时：engine_capture 返回空串 → parse 得空预判；
        # 异常同样吞掉降级为空，绝不让单个角色拖垮整体。
        try:
            out = engine_capture(
                spec, project_path,
                build_predict_prompt(description, prior_qa, role=role),
                timeout)
            return parse_prediction(out)
        except Exception:
            return Prediction([], [])

    with ThreadPoolExecutor(max_workers=len(roles)) as ex:
        results = list(ex.map(_run_role, roles))

    role_preds = [p for p in results if not _is_empty(p)]
    if not role_preds:
        # 全部角色失败/超时 → 空预判降级（不阻断澄清）。
        return Prediction([], [])

    # 综合步：不扫项目，给更短超时。失败则确定性兜底合并。
    try:
        synth_out = engine_capture(
            spec, project_path,
            build_synthesis_prompt(description, role_preds),
            synth_timeout)
        synth = parse_prediction(synth_out)
    except Exception:
        synth = Prediction([], [])
    if _is_empty(synth):
        return merge_predictions(role_preds)
    return synth

"""role_predict 多角色 fan-out 测试：并行、合成、prior_qa 透传、容错三级。

全程用桩 engine_capture，不依赖真实引擎。桩按 prompt 中的角色名 / 综合标记分流，
返回不同 canned JSON。
"""
import json
import time

from autocoder.core.clarify import Role, Prediction, Question
from autocoder.core import role_predict
from autocoder.core.role_predict import (
    fanout_predict, merge_predictions, load_roles, DEFAULT_ROLES,
)

_SPEC = {"command": "true", "args": [], "timeout": 1800, "env": {}}
_ROLES = [
    Role(name="产品经理", focus="范围"),
    Role(name="架构师", focus="模块"),
    Role(name="测试工程师", focus="用例"),
]


def _role_json(modules, questions):
    qs = [{"key": k, "ask": a, "type": "text"} for k, a in questions]
    return json.dumps({"modules": modules, "risks": [], "scope_hint": "",
                       "acceptance_hint": "", "ready": False,
                       "ready_reason": "", "questions": qs}, ensure_ascii=False)


def _make_capture(role_outputs, synth_output, recorder=None):
    """构造桩：根据 prompt 内容判断是哪个角色 / 综合步，返回对应 JSON。

    role_outputs: {角色名: json串}；synth_output: 综合步返回的 json 串。
    recorder: 可选 list，记录每次调用的 (kind, prompt)。
    """
    def capture(spec, cwd, prompt, timeout=None):
        if "综合者" in prompt:
            if recorder is not None:
                recorder.append(("synth", prompt))
            return synth_output
        for name, out in role_outputs.items():
            if f"「{name}」" in prompt:
                if recorder is not None:
                    recorder.append((name, prompt))
                return out
        if recorder is not None:
            recorder.append(("?", prompt))
        return ""
    return capture


def test_load_roles_defaults_when_no_roles_key():
    assert load_roles({"enabled": True}) == list(DEFAULT_ROLES)


def test_load_roles_custom_override():
    cfg = {"enabled": True, "roles": [{"name": "安全", "focus": "越权"}]}
    roles = load_roles(cfg)
    assert len(roles) == 1 and roles[0].name == "安全"


def test_load_roles_empty_cfg_returns_empty():
    assert load_roles({}) == []
    assert load_roles(None) == []


def test_fanout_calls_n_plus_one_and_merges():
    rec = []
    capture = _make_capture(
        role_outputs={
            "产品经理": _role_json(["a.py"], [("scope", "范围到哪？")]),
            "架构师": _role_json(["b.py"], [("dep", "依赖哪个模块？")]),
            "测试工程师": _role_json(["a.py"], [("edge", "边界？")]),
        },
        synth_output=json.dumps({
            "modules": ["a.py", "b.py"], "risks": [],
            "scope_hint": "只做X", "acceptance_hint": "Y算完成",
            "ready": False, "ready_reason": "",
            "questions": [{"key": "scope", "ask": "范围到哪？", "type": "text"}],
        }, ensure_ascii=False),
        recorder=rec,
    )
    pred = fanout_predict(roles=_ROLES, description="需求", prior_qa=None,
                          spec=_SPEC, project_path="/p", timeout=180,
                          engine_capture=capture, synth_timeout=60)
    # N 角色 + 1 综合 = 4 次调用。
    assert len(rec) == 4
    kinds = [k for k, _ in rec]
    assert kinds.count("synth") == 1
    assert pred.modules == ["a.py", "b.py"]
    assert pred.scope_hint == "只做X"
    assert len(pred.questions) == 1


def test_each_role_prompt_carries_role_framing():
    rec = []
    capture = _make_capture(
        role_outputs={r.name: _role_json([], []) for r in _ROLES},
        synth_output=_role_json(["x"], []),
        recorder=rec,
    )
    fanout_predict(roles=_ROLES, description="需求", prior_qa=None,
                   spec=_SPEC, project_path="/p", timeout=180,
                   engine_capture=capture, synth_timeout=60)
    role_prompts = [p for k, p in rec if k in {r.name for r in _ROLES}]
    assert len(role_prompts) == 3
    for r in _ROLES:
        assert any(f"「{r.name}」" in p for p in role_prompts)


def test_prior_qa_reaches_every_role():
    rec = []
    qa = [{"ask": "超时？", "answer": "拒绝"}]
    capture = _make_capture(
        role_outputs={r.name: _role_json([], []) for r in _ROLES},
        synth_output=_role_json(["x"], []),
        recorder=rec,
    )
    fanout_predict(roles=_ROLES, description="需求", prior_qa=qa,
                   spec=_SPEC, project_path="/p", timeout=180,
                   engine_capture=capture, synth_timeout=60)
    role_prompts = [p for k, p in rec if k in {r.name for r in _ROLES}]
    for p in role_prompts:
        assert "超时？" in p and "拒绝" in p


def test_roles_run_in_parallel():
    """SC-002：带 sleep 的桩，墙钟应≈max(单角色)+综合，而非求和。"""
    def capture(spec, cwd, prompt, timeout=None):
        if "综合者" in prompt:
            return _role_json(["x"], [])
        time.sleep(0.2)
        for r in _ROLES:
            if f"「{r.name}」" in prompt:
                return _role_json(["m"], [])
        return ""
    t0 = time.monotonic()
    fanout_predict(roles=_ROLES, description="需求", prior_qa=None,
                   spec=_SPEC, project_path="/p", timeout=180,
                   engine_capture=capture, synth_timeout=60)
    elapsed = time.monotonic() - t0
    # 串行需 3*0.2=0.6s；并行应明显更短（留余量 < 0.45s）。
    assert elapsed < 0.45, f"并行未生效，用时 {elapsed:.2f}s"


# ---- 容错三级（US3）-------------------------------------------------------

def test_partial_role_failure_uses_survivors():
    """部分角色返回空串 → 用存活角色合成。"""
    rec = []
    capture = _make_capture(
        role_outputs={
            "产品经理": _role_json(["a.py"], []),
            "架构师": "",            # 失败
            "测试工程师": "",        # 失败
        },
        synth_output=_role_json(["a.py"], []),
        recorder=rec,
    )
    pred = fanout_predict(roles=_ROLES, description="需求", prior_qa=None,
                          spec=_SPEC, project_path="/p", timeout=180,
                          engine_capture=capture, synth_timeout=60)
    assert pred.modules == ["a.py"]


def test_all_roles_fail_returns_empty_prediction():
    capture = _make_capture(
        role_outputs={r.name: "" for r in _ROLES},
        synth_output=_role_json(["should-not-appear"], []),
    )
    pred = fanout_predict(roles=_ROLES, description="需求", prior_qa=None,
                          spec=_SPEC, project_path="/p", timeout=180,
                          engine_capture=capture, synth_timeout=60)
    assert pred.modules == [] and pred.questions == []


def test_synth_failure_falls_back_to_merge():
    """综合步返回空 → 走 merge_predictions（保留角色成果、≤3 问）。"""
    capture = _make_capture(
        role_outputs={
            "产品经理": _role_json(["a.py"], [("q1", "Q1?")]),
            "架构师": _role_json(["b.py"], [("q2", "Q2?")]),
            "测试工程师": _role_json(["c.py"], [("q3", "Q3?"), ("q4", "Q4?")]),
        },
        synth_output="",   # 综合失败
    )
    pred = fanout_predict(roles=_ROLES, description="需求", prior_qa=None,
                          spec=_SPEC, project_path="/p", timeout=180,
                          engine_capture=capture, synth_timeout=60)
    assert set(pred.modules) == {"a.py", "b.py", "c.py"}
    assert len(pred.questions) == 3   # 4 个问题截断至 3


def test_role_exception_is_swallowed():
    def capture(spec, cwd, prompt, timeout=None):
        if "综合者" in prompt:
            return _role_json(["x"], [])
        if "「架构师」" in prompt:
            raise RuntimeError("boom")
        return _role_json(["ok"], [])
    pred = fanout_predict(roles=_ROLES, description="需求", prior_qa=None,
                          spec=_SPEC, project_path="/p", timeout=180,
                          engine_capture=capture, synth_timeout=60)
    assert pred.modules == ["x"]   # 不抛异常，存活角色经综合产出


# ---- merge_predictions 单测（US3）----------------------------------------

def test_merge_dedups_and_caps_questions():
    preds = [
        Prediction(modules=["a", "b"], risks=["r1"], scope_hint="S1",
                   acceptance_hint="", ready=True, ready_reason="ok1",
                   questions=[Question("k1", "同一个问题？")]),
        Prediction(modules=["b", "c"], risks=["r1", "r2"], scope_hint="S2",
                   acceptance_hint="A2", ready=False, ready_reason="ok2",
                   questions=[Question("k2", "同一个问题？"),
                              Question("k3", "另一个？"),
                              Question("k4", "第三？"), Question("k5", "第四？")]),
    ]
    merged = merge_predictions(preds)
    assert merged.modules == ["a", "b", "c"]      # 去重保序
    assert merged.risks == ["r1", "r2"]
    assert merged.scope_hint == "S1"              # 首个非空（产品经理优先）
    assert merged.acceptance_hint == "A2"
    assert len(merged.questions) == 3             # 去重后截断至 3
    asks = [q.ask for q in merged.questions]
    assert asks.count("同一个问题？") == 1        # 跨角色去重


def test_merge_ready_is_conservative():
    # 有未答问题 → not ready，即使某角色 ready。
    preds = [
        Prediction([], [], ready=True, questions=[]),
        Prediction([], [], ready=False, questions=[Question("k", "缺口？")]),
    ]
    assert merge_predictions(preds).ready is False
    # 无人提问 → ready。
    preds2 = [Prediction([], [], ready=False, questions=[])]
    assert merge_predictions(preds2).ready is True

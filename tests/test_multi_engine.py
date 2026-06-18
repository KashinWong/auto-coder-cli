"""分阶段 / 分角色多引擎（多模型）解析测试。

覆盖：_engine_name_for 回退链与降级、stage_engines 阶段级、role.engine 角色级、
synth_engine 综合步、以及全向后兼容（只配 engine 时所有阶段/角色都解析到它）。
"""
from autocoder.core.orchestrator import Orchestrator
from autocoder.adapters.store import JsonTaskStore
from autocoder.core.clarify import Prediction
from autocoder.core import role_predict
from autocoder.core.role_predict import fanout_predict, load_roles
from autocoder.core.clarify import Role


# 多引擎配置桩：3 个引擎，每个用 env.CLAUDE_MODEL 区分模型。
_ENGINES = {
    "claude-opus": {"command": "claude", "args": [], "timeout": 1800,
                    "env": {"CLAUDE_MODEL": "opus"}},
    "claude-sonnet": {"command": "claude", "args": [], "timeout": 1800,
                      "env": {"CLAUDE_MODEL": "sonnet"}},
    "claude-haiku": {"command": "claude", "args": [], "timeout": 1800,
                     "env": {"CLAUDE_MODEL": "haiku"}},
}


class _MultiCfg:
    def __init__(self, tmp_path, project):
        self.workspace_dir = str(tmp_path / "ws")
        self.concurrency_limit = 3
        self.default_engine = "claude-opus"
        self.engines = dict(_ENGINES)
        self.projects = {"demo": project}
        self.feishu = {}

    def engine_spec(self, name):
        return self.engines[name]

    def match_project(self, text):
        return "demo" if "demo" in text else None


def _orch(tmp_path, project):
    cfg = _MultiCfg(tmp_path, project)
    store = JsonTaskStore(cfg.workspace_dir)
    return Orchestrator(cfg, store, type("N", (), {})(), type("R", (), {})(),
                        worktree=type("WT", (), {})())


# ---- _engine_name_for 回退链 ---------------------------------------------

def test_explicit_engine_wins(tmp_path):
    o = _orch(tmp_path, {"path": "/p", "engine": "claude-sonnet"})
    proj = o.cfg.projects["demo"]
    assert o._engine_name_for(proj, engine_name="claude-haiku") == "claude-haiku"


def test_stage_engine_over_project(tmp_path):
    proj = {"path": "/p", "engine": "claude-sonnet",
            "stage_engines": {"plan": "claude-haiku"}}
    o = _orch(tmp_path, proj)
    p = o.cfg.projects["demo"]
    assert o._engine_name_for(p, stage="plan") == "claude-haiku"
    # 未配的阶段回退项目引擎。
    assert o._engine_name_for(p, stage="execute") == "claude-sonnet"


def test_project_engine_fallback(tmp_path):
    o = _orch(tmp_path, {"path": "/p", "engine": "claude-sonnet"})
    p = o.cfg.projects["demo"]
    assert o._engine_name_for(p, stage="clarify") == "claude-sonnet"


def test_default_engine_when_no_project(tmp_path):
    o = _orch(tmp_path, {"path": "/p", "engine": "claude-sonnet"})
    assert o._engine_name_for(None, stage="plan") == "claude-opus"  # default


def test_unknown_engine_degrades_to_default(tmp_path):
    proj = {"path": "/p", "engine": "claude-sonnet",
            "stage_engines": {"plan": "不存在的引擎"}}
    o = _orch(tmp_path, proj)
    p = o.cfg.projects["demo"]
    # 拼错/不存在 → 跳过该候选，回退项目引擎（claude-sonnet），不抛错。
    assert o._engine_name_for(p, stage="plan") == "claude-sonnet"


def test_null_stage_engines_does_not_crash(tmp_path):
    """YAML 写 'stage_engines:' 不带值 → 解析成 None，不能崩，回退项目引擎。"""
    proj = {"path": "/p", "engine": "claude-sonnet", "stage_engines": None}
    o = _orch(tmp_path, proj)
    p = o.cfg.projects["demo"]
    assert o._engine_name_for(p, stage="plan") == "claude-sonnet"


def test_engine_spec_for_returns_spec_dict(tmp_path):
    proj = {"path": "/p", "engine": "claude-sonnet",
            "stage_engines": {"clarify": "claude-haiku"}}
    o = _orch(tmp_path, proj)
    p = o.cfg.projects["demo"]
    spec = o._engine_spec_for(p, stage="clarify")
    assert spec["env"]["CLAUDE_MODEL"] == "haiku"


# ---- load_roles 解析 role.engine -----------------------------------------

def test_load_roles_parses_engine():
    cfg = {"enabled": True, "roles": [
        {"name": "产品经理", "focus": "范围", "engine": "claude-sonnet"},
        {"name": "架构师", "focus": "模块"},  # 无 engine → None
    ]}
    roles = load_roles(cfg)
    assert roles[0].engine == "claude-sonnet"
    assert roles[1].engine is None


def test_default_roles_have_no_engine():
    roles = load_roles({"enabled": True})
    assert all(r.engine is None for r in roles)


# ---- fanout 各角色用各自引擎 spec ----------------------------------------

def test_fanout_routes_per_role_engine():
    """每个角色/综合步用各自 engine 的 spec：桩按 spec.env.CLAUDE_MODEL 记录分流。"""
    seen = []  # (role_or_synth, model)

    def capture(spec, cwd, prompt, timeout=None):
        model = spec.get("env", {}).get("CLAUDE_MODEL", "?")
        if "综合者" in prompt:
            seen.append(("synth", model))
            return ('{"modules": ["a"], "risks": [], "ready": false, '
                    '"questions": []}')
        for nm in ("产品经理", "架构师", "测试工程师"):
            if f"「{nm}」" in prompt:
                seen.append((nm, model))
        return '{"modules": ["a"], "risks": [], "ready": false, "questions": []}'

    roles = [
        Role("产品经理", "范围", engine="claude-sonnet"),
        Role("架构师", "模块", engine="claude-haiku"),
        Role("测试工程师", "用例"),  # 无 engine → 回退默认 spec
    ]
    default_spec = _ENGINES["claude-opus"]
    resolve = lambda name: _ENGINES.get(name)
    fanout_predict(roles=roles, description="需求", prior_qa=None,
                   spec=default_spec, project_path="/p", timeout=180,
                   engine_capture=capture, synth_timeout=60,
                   resolve_spec=resolve, synth_engine="claude-haiku")
    by = dict(seen)
    assert by["产品经理"] == "sonnet"
    assert by["架构师"] == "haiku"
    assert by["测试工程师"] == "opus"   # 回退默认 spec
    assert by["synth"] == "haiku"      # synth_engine


def test_fanout_backward_compat_without_resolver():
    """不传 resolve_spec → 所有角色/综合步用默认 spec（现有零回归行为）。"""
    models = []

    def capture(spec, cwd, prompt, timeout=None):
        models.append(spec.get("env", {}).get("CLAUDE_MODEL", "?"))
        return '{"modules": ["a"], "risks": [], "ready": false, "questions": []}'

    roles = [Role("产品经理", "范围", engine="claude-sonnet"),  # 即便带 engine
             Role("架构师", "模块")]
    fanout_predict(roles=roles, description="需求", prior_qa=None,
                   spec=_ENGINES["claude-opus"], project_path="/p", timeout=180,
                   engine_capture=capture, synth_timeout=60)
    # 无 resolver → role.engine 被忽略，全用默认 opus。
    assert set(models) == {"opus"}


# ---- 阶段级端到端：_engine_predict 用 clarify 阶段引擎 --------------------

def test_engine_predict_uses_clarify_stage_engine(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    captured = {}

    def capture(spec, cwd, prompt, timeout=None):
        captured["model"] = spec.get("env", {}).get("CLAUDE_MODEL")
        return '{"modules": [], "risks": [], "ready": true, "questions": []}'

    project = {"path": str(proj), "engine": "claude-opus",
               "stage_engines": {"clarify": "claude-sonnet"}}
    cfg = _MultiCfg(tmp_path, project)
    store = JsonTaskStore(cfg.workspace_dir)
    o = Orchestrator(cfg, store, type("N", (), {})(), type("R", (), {})(),
                     worktree=type("WT", (), {})(), engine_capture=capture)
    o._engine_predict("加功能", str(proj))
    assert captured["model"] == "sonnet"  # 用了 clarify 阶段引擎


def test_engine_predict_compat_only_engine(tmp_path):
    """只配 engine（无 stage_engines）→ 澄清用该引擎，完全向后兼容。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    captured = {}

    def capture(spec, cwd, prompt, timeout=None):
        captured["model"] = spec.get("env", {}).get("CLAUDE_MODEL")
        return '{"modules": [], "risks": [], "ready": true, "questions": []}'

    project = {"path": str(proj), "engine": "claude-haiku"}
    cfg = _MultiCfg(tmp_path, project)
    store = JsonTaskStore(cfg.workspace_dir)
    o = Orchestrator(cfg, store, type("N", (), {})(), type("R", (), {})(),
                     worktree=type("WT", (), {})(), engine_capture=capture)
    o._engine_predict("加功能", str(proj))
    assert captured["model"] == "haiku"


def test_all_candidates_invalid_degrades_to_default(tmp_path):
    """stage_engines 与 project engine 均无效 → 一路降级到 default_engine。"""
    proj = {"path": "/p", "engine": "也不存在",
            "stage_engines": {"plan": "不存在的引擎"}}
    o = _orch(tmp_path, proj)
    p = o.cfg.projects["demo"]
    assert o._engine_name_for(p, stage="plan") == "claude-opus"  # default


# ---- _plan 端到端：plan / execute 引擎独立解析、execute 写回 task.engine ----

def test_plan_resolves_plan_and_execute_engines_independently(tmp_path):
    """规划阶段：plan 引擎跑规划、execute 引擎写回 task.engine（二者可不同）。"""
    from autocoder.models import Task

    projdir = tmp_path / "proj"
    projdir.mkdir()
    project = {"path": str(projdir), "engine": "claude-opus", "base_branch": "main",
               "stage_engines": {"plan": "claude-sonnet", "execute": "claude-haiku"}}
    cfg = _MultiCfg(tmp_path, project)
    store = JsonTaskStore(cfg.workspace_dir)
    store.add(Task(record_id="r1", description="给 demo 加功能", priority="重要",
                   project="demo", task_title="加功能", summary="## 需求\n摘要",
                   status="规划中"))

    seen = {}

    def engine_run(spec, wd, prompt, log):
        from autocoder.core.engine_runner import EngineResult
        seen["plan_model"] = spec.get("env", {}).get("CLAUDE_MODEL")
        # 造出 tasks.md/spec.md 让后续不报错。
        from pathlib import Path as _P
        _P(wd).mkdir(parents=True, exist_ok=True)
        return EngineResult.SUCCESS

    notifier = type("N", (), {"send_plan": lambda *a, **k: None,
                              "send_failure": lambda *a, **k: None})()
    fake_wt = type("WT", (), {
        "create": staticmethod(lambda p, b, r: None),
        "branch_name": staticmethod(lambda r: "feature/auto-r1"),
    })
    o = Orchestrator(cfg, store, notifier, type("R", (), {})(),
                     worktree=fake_wt, engine_run=engine_run)
    o._plan(store.get("r1"), "demo", project,
            str(tmp_path / "wt"), "需求摘要")

    # 规划用 plan 引擎（sonnet）。
    assert seen["plan_model"] == "sonnet"
    # execute 引擎（haiku）写回 task.engine，供执行阶段直接读。
    assert store.get("r1").engine == "claude-haiku"


# ---- config 层 fail-fast：default 引擎必须存在 -----------------------------

def test_load_config_rejects_unknown_default_engine(tmp_path):
    import pytest
    import yaml
    from autocoder.config import load_config

    bad = {
        "adapters": {"store": "json", "notifier": "cli", "router": "cli"},
        "workspace_dir": str(tmp_path / "ws"),
        "concurrency": {"limit": 3},
        "projects": {},
        "engines": {"claude-code": {"command": "claude", "args": [], "timeout": 5,
                                    "env": {}},
                    "default": "不存在的引擎"},
    }
    cfgpath = tmp_path / "config.yaml"
    cfgpath.write_text(yaml.safe_dump(bad, allow_unicode=True))
    with pytest.raises(ValueError, match="engines.default"):
        load_config(str(cfgpath))

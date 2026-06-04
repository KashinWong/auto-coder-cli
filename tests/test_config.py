import pytest
from autocoder.config import load_config, Config

SAMPLE = """
adapters:
  store: json
  notifier: cli
  router: cli
workspace_dir: ./workspace/tasks
concurrency:
  limit: 3
projects:
  demo:
    path: /tmp/demo
    engine: claude-code
    match_keywords: ["demo"]
    base_branch: main
    test_command: "true"
    build_command: "true"
engines:
  claude-code:
    command: claude
    args: ["--print"]
    timeout: 1800
    env: {}
  default: claude-code
clarify_dimensions:
  - {key: scope, label: "范围", source: fixed}
"""


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return str(p)


def test_load_basic(tmp_path):
    cfg = load_config(_write(tmp_path, SAMPLE))
    assert isinstance(cfg, Config)
    assert cfg.adapters["store"] == "json"
    assert cfg.concurrency_limit == 3
    assert cfg.projects["demo"]["base_branch"] == "main"
    assert cfg.default_engine == "claude-code"


def test_engine_spec_lookup(tmp_path):
    cfg = load_config(_write(tmp_path, SAMPLE))
    spec = cfg.engine_spec("claude-code")
    assert spec["command"] == "claude"
    assert spec["timeout"] == 1800


def test_match_project_by_keyword(tmp_path):
    cfg = load_config(_write(tmp_path, SAMPLE))
    assert cfg.match_project("给 demo 加功能") == "demo"
    assert cfg.match_project("无关需求") is None


def test_match_project_is_case_insensitive(tmp_path):
    # 关键词配小写 demo，用户写 Demo/DEMO 也必须匹配——否则英文项目名
    # （如 Digital-Admin）会静默失配，导致 project_path 空、澄清卡空白。
    cfg = load_config(_write(tmp_path, SAMPLE))
    assert cfg.match_project("给 Demo 加功能") == "demo"
    assert cfg.match_project("「DEMO」月度报表") == "demo"


def test_missing_required_key_raises(tmp_path):
    bad = SAMPLE.replace("workspace_dir: ./workspace/tasks", "")
    with pytest.raises(ValueError, match="workspace_dir"):
        load_config(_write(tmp_path, bad))

import json
from pathlib import Path
from autocoder.cli import main


SAMPLE_CFG = """
adapters: {{store: json, notifier: cli, router: cli}}
workspace_dir: {ws}
concurrency: {{limit: 3}}
projects:
  demo:
    path: /tmp/demo
    engine: claude-code
    match_keywords: ["demo"]
    base_branch: main
    test_command: "true"
    build_command: "true"
engines:
  claude-code: {{command: claude, args: [], timeout: 1800, env: {{}}}}
  default: claude-code
clarify_dimensions: []
"""


def _cfg(tmp_path):
    ws = tmp_path / "ws"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CFG.format(ws=str(ws)))
    return str(cfg), str(ws)


def test_add_creates_task(tmp_path, capsys):
    cfg_path, ws = _cfg(tmp_path)
    rc = main(["add", "给 demo 加功能", "--priority", "重要紧急",
               "--config", cfg_path])
    assert rc == 0
    files = list(Path(ws).glob("*/task.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["description"] == "给 demo 加功能"
    assert data["status"] == "待开始"


def test_status_lists_tasks(tmp_path, capsys):
    cfg_path, ws = _cfg(tmp_path)
    main(["add", "需求A", "--config", cfg_path])
    capsys.readouterr()
    rc = main(["status", "--config", cfg_path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "需求A" in out
    assert "待开始" in out


def test_unknown_command_returns_error(tmp_path):
    cfg_path, _ = _cfg(tmp_path)
    rc = main(["frobnicate", "--config", cfg_path])
    assert rc != 0


def test_resume_invokes_orchestrator(tmp_path, monkeypatch):
    cfg_path, ws = _cfg(tmp_path)
    main(["add", "给 demo 加功能", "--config", cfg_path])
    rid = json.loads(next(Path(ws).glob("*/task.json")).read_text())["record_id"]

    called = {}
    import autocoder.cli as cli_mod
    monkeypatch.setattr(cli_mod.Orchestrator, "resume",
                        lambda self, record_id: called.setdefault("rid", record_id))
    rc = main(["resume", rid, "--config", cfg_path])
    assert rc == 0
    assert called["rid"] == rid


def test_monitor_invokes_orchestrator(tmp_path, monkeypatch):
    cfg_path, ws = _cfg(tmp_path)
    called = {}
    import autocoder.cli as cli_mod
    monkeypatch.setattr(cli_mod.Orchestrator, "monitor",
                        lambda self: called.setdefault("ran", True))
    rc = main(["monitor", "--config", cfg_path])
    assert rc == 0
    assert called.get("ran") is True

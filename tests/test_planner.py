from pathlib import Path
from autocoder.core.planner import next_spec_dir, count_tasks, slugify


def test_next_spec_dir_empty(tmp_path):
    assert next_spec_dir(str(tmp_path)) == "specs/001-feature"


def test_next_spec_dir_increments(tmp_path):
    (tmp_path / "specs" / "001-foo").mkdir(parents=True)
    (tmp_path / "specs" / "002-bar").mkdir(parents=True)
    assert next_spec_dir(str(tmp_path)) == "specs/003-feature"


def test_next_spec_dir_with_slug(tmp_path):
    assert next_spec_dir(str(tmp_path), "加登录按钮") == "specs/001-加登录按钮"


def test_count_tasks(tmp_path):
    tasks_md = tmp_path / "tasks.md"
    tasks_md.write_text(
        "# Tasks\n\n"
        "- [ ] T001 第一项\n"
        "- [ ] T002 第二项\n"
        "- [ ] T003 第三项\n"
    )
    assert count_tasks(str(tasks_md)) == 3


def test_count_tasks_missing_file(tmp_path):
    assert count_tasks(str(tmp_path / "nope.md")) == 0


def test_slugify():
    assert slugify("加登录按钮") == "加登录按钮"
    assert slugify("Add Login Button") == "add-login-button"
    assert slugify("") == "feature"

from autocoder.core import worktree as wt


def test_branch_name():
    assert wt.branch_name("abc") == "feature/auto-abc"


def test_count_active():
    porcelain = (
        "worktree /repo\nHEAD x\nbranch refs/heads/main\n\n"
        "worktree /repo/.worktrees/feature/auto-r1\nHEAD y\n"
        "branch refs/heads/feature/auto-r1\n\n"
        "worktree /repo/.worktrees/feature/auto-r2\nHEAD z\n"
        "branch refs/heads/feature/auto-r2\n"
    )
    assert wt.count_active(porcelain) == 2


def test_count_active_none():
    assert wt.count_active("worktree /repo\nbranch refs/heads/main\n") == 0


def test_count_active_excludes_self():
    porcelain = (
        "worktree /repo/.worktrees/feature/auto-r1\nbranch refs/heads/feature/auto-r1\n\n"
        "worktree /repo/.worktrees/feature/auto-r2\nbranch refs/heads/feature/auto-r2\n"
    )
    # 排除自己后只剩另一条在占用
    assert wt.count_active(porcelain, exclude_record_id="r1") == 1
    assert wt.count_active(porcelain) == 2  # 不排除时仍数全部


def test_gate_open():
    assert wt.gate_open(2, 3) is True
    assert wt.gate_open(3, 3) is False
    assert wt.gate_open(4, 3) is False

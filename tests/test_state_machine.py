import pytest
from autocoder.core import state_machine as sm


@pytest.mark.parametrize("frm,to", [
    ("待开始", "澄清中"),
    ("澄清中", "待立项"),
    ("澄清中", "澄清中"),
    ("待立项", "规划中"),
    ("待立项", "澄清中"),
    ("待立项", "已搁置"),
    ("已搁置", "待开始"),
    ("规划中", "待审批"),
    ("待审批", "进行中"),
    ("待审批", "规划中"),
    ("待审批", "澄清中"),
    ("进行中", "已完成"),
    ("进行中", "已停滞"),
])
def test_allowed_transitions(frm, to):
    assert sm.can_transition(frm, to) is True


@pytest.mark.parametrize("frm,to", [
    ("待开始", "待审批"),
    ("已完成", "进行中"),
    ("澄清中", "进行中"),
])
def test_forbidden_transitions(frm, to):
    assert sm.can_transition(frm, to) is False


@pytest.mark.parametrize("action,target", [
    ("reject_charter", "已搁置"),
    ("revise_charter", "澄清中"),
    ("rechat", "澄清中"),
    ("revise_plan", "规划中"),
    ("reunderstand", "澄清中"),
])
def test_rollback_target(action, target):
    assert sm.rollback_target(action) == target


def test_rollback_target_unknown():
    assert sm.rollback_target("nope") is None


def test_rollback_drops_worktree():
    assert sm.rollback_drops_worktree("reunderstand") is True
    assert sm.rollback_drops_worktree("revise_plan") is False
    assert sm.rollback_drops_worktree("reject_charter") is False

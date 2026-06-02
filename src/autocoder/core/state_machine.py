_TRANSITIONS = {
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
}

_ROLLBACK_TARGET = {
    "reject_charter": "已搁置",
    "revise_charter": "澄清中",
    "rechat": "澄清中",
    "revise_plan": "规划中",
    "reunderstand": "澄清中",
}


def can_transition(frm: str, to: str) -> bool:
    return (frm, to) in _TRANSITIONS


def rollback_target(action: str):
    return _ROLLBACK_TARGET.get(action)


def rollback_drops_worktree(action: str) -> bool:
    return action == "reunderstand"

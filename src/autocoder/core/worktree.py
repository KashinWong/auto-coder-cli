import subprocess
from pathlib import Path


def branch_name(record_id: str) -> str:
    return f"feature/auto-{record_id}"


def count_active(porcelain_text: str) -> int:
    return sum(
        1 for line in porcelain_text.splitlines()
        if line.startswith("branch refs/heads/feature/auto-")
    )


def gate_open(active: int, limit: int) -> bool:
    return active < limit


def active_count(project_path: str) -> int:
    result = subprocess.run(
        ["git", "-C", project_path, "worktree", "list", "--porcelain"],
        capture_output=True, text=True,
    )
    return count_active(result.stdout)


def create(project_path: str, base_branch: str, record_id: str) -> str:
    branch = branch_name(record_id)
    worktree_path = str(Path(project_path) / ".worktrees" / branch)
    Path(worktree_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", project_path, "fetch", "origin", base_branch],
        capture_output=True, text=True,
    )
    add_from_remote = subprocess.run(
        ["git", "-C", project_path, "worktree", "add", worktree_path,
         "-b", branch, f"origin/{base_branch}"],
        capture_output=True, text=True,
    )
    if add_from_remote.returncode != 0:
        fallback = subprocess.run(
            ["git", "-C", project_path, "worktree", "add", worktree_path, branch],
            capture_output=True, text=True,
        )
        if fallback.returncode != 0:
            raise RuntimeError(f"worktree create failed: {add_from_remote.stderr}")
    return worktree_path


def remove(project_path: str, record_id: str) -> None:
    branch = branch_name(record_id)
    worktree_path = str(Path(project_path) / ".worktrees" / branch)
    subprocess.run(
        ["git", "-C", project_path, "worktree", "remove", "--force", worktree_path],
        capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-C", project_path, "branch", "-D", branch],
        capture_output=True, text=True,
    )

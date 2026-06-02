import re
from pathlib import Path


def slugify(title: str) -> str:
    if not title:
        return "feature"
    s = title.strip().lower()
    # 英文转 kebab；非 ASCII（中文）原样保留
    if re.fullmatch(r"[\x00-\x7f]+", s):
        s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
        return s or "feature"
    return title.strip()


def next_spec_dir(worktree_path: str, title: str = "") -> str:
    specs = Path(worktree_path) / "specs"
    max_n = 0
    if specs.exists():
        for d in specs.iterdir():
            m = re.match(r"(\d{3})-", d.name)
            if d.is_dir() and m:
                max_n = max(max_n, int(m.group(1)))
    n = f"{max_n + 1:03d}"
    return f"specs/{n}-{slugify(title)}"


def count_tasks(tasks_md_path: str) -> int:
    p = Path(tasks_md_path)
    if not p.exists():
        return 0
    return sum(
        1 for line in p.read_text().splitlines()
        if re.match(r"\s*- \[[ x]\] ", line)
    )

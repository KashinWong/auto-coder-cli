import time
from datetime import datetime
from pathlib import Path

from autocoder.core import state_machine as sm
from autocoder.core import worktree as wt_mod
from autocoder.core import planner
from autocoder.core.clarify import ClarifyOrchestrator
from autocoder.core.engine_runner import run_engine, run_command, EngineResult


def _default_engine_run(spec, working_dir, prompt, log_file):
    return run_engine(spec, working_dir, prompt, log_file)


class _DefaultGit:
    @staticmethod
    def add_commit_push(worktree_path, commit_msg, branch):
        import subprocess
        subprocess.run(["git", "-C", worktree_path, "add", "-A"], check=False)
        stat = subprocess.run(
            ["git", "-C", worktree_path, "diff", "--cached", "--stat"],
            capture_output=True, text=True,
        ).stdout.strip().splitlines()
        change_stats = stat[-1] if stat else ""
        subprocess.run(["git", "-C", worktree_path, "commit", "-m", commit_msg],
                       capture_output=True, text=True)
        push = subprocess.run(
            ["git", "-C", worktree_path, "push", "-u", "origin", branch],
            capture_output=True, text=True,
        )
        return push.returncode == 0, change_stats


class Orchestrator:
    def __init__(self, config, store, notifier, router,
                 worktree=wt_mod, git_ops=None, engine_run=None,
                 predict_fn=None, resolve_worktree=None):
        self.cfg = config
        self.store = store
        self.notifier = notifier
        self.router = router
        self.wt = worktree
        self.git = git_ops or _DefaultGit
        self.engine_run = engine_run or _default_engine_run
        self.clarify = ClarifyOrchestrator(
            predict_fn=predict_fn or self._engine_predict)
        self._resolve_worktree = resolve_worktree
        self._lock_root = Path(config.workspace_dir) / ".locks"

    # ---- locking (record-level, atomic mkdir) -------------------------
    def _lock_path(self, record_id):
        return self._lock_root / f"{record_id}.lock"

    def _acquire_lock(self, record_id) -> bool:
        self._lock_root.mkdir(parents=True, exist_ok=True)
        p = self._lock_path(record_id)
        try:
            p.mkdir()
            return True
        except FileExistsError:
            age = time.time() - p.stat().st_mtime
            if age < 1800:
                return False
            # 锁已过期，尝试回收。rmdir+mkdir 非原子：若另一进程同时回收，
            # 这里的 mkdir 会再次抛 FileExistsError，让出锁返回 False。
            try:
                p.rmdir()
                p.mkdir()
                return True
            except (FileNotFoundError, FileExistsError):
                return False

    def _release_lock(self, record_id):
        p = self._lock_path(record_id)
        if p.exists():
            p.rmdir()

    # ---- prediction via engine (default) ------------------------------
    def _engine_predict(self, description, project_path):
        from autocoder.core.clarify import Prediction
        # 默认实现：不深入跑引擎做结构化预判（留给飞书/agent 场景增强），
        # CLI 默认给空预判，由用户在澄清回答里补全。
        return Prediction([], [])

    def _set_status(self, record_id, to):
        cur = self.store.get(record_id).status
        if sm.can_transition(cur, to):
            self.store.update_status(record_id, to)
        else:
            raise RuntimeError(f"非法状态转移 {cur} -> {to}")

    # ---- dispatch -----------------------------------------------------
    def dispatch_one(self):
        pending = self.store.fetch_pending()
        if not pending:
            return
        task = pending[0]
        rid = task.record_id
        self._set_status(rid, "澄清中")

        project_key = self.cfg.match_project(task.description)
        project_path = (self.cfg.projects[project_key]["path"]
                        if project_key else "")

        round_no = 1
        while True:
            pred = self.clarify.predict(task.description, project_path)
            self.notifier.send_clarify(task, pred.modules, pred.risks, round_no)
            decision = self.router.await_decision(rid, "clarify")
            trivial = not decision.form
            if self.clarify.ready_to_charter(decision.form, round_no, trivial):
                break
            if round_no >= 3:
                break
            round_no += 1

        summary = self.clarify.synthesize(task.description, decision.form)
        self._set_status(rid, "待立项")
        self.notifier.send_charter(task, summary)

        charter = self.router.await_decision(rid, "charter")
        if charter.action == "reject_charter":
            self._set_status(rid, "已搁置")
            return
        if charter.action in ("revise_charter", "rechat"):
            self._set_status(rid, "澄清中")
            return  # CLI 模式下交回用户重新触发；飞书模式由回调继续
        # approve_charter：建 worktree，进规划
        if not project_key:
            self.notifier.send_failure(task, "立项", "无法匹配目标项目", "", "")
            return
        proj = self.cfg.projects[project_key]
        wtpath = self.wt.create(proj["path"], proj["base_branch"], rid)
        self._set_status(rid, "规划中")
        self._plan(task, project_key, proj, wtpath, summary)

        # 方案审批
        plan_decision = self.router.await_decision(rid, "plan")
        if plan_decision.action == "approve_plan":
            active = self.wt.active_count(proj["path"])
            if self.wt.gate_open(active, self.cfg.concurrency_limit):
                self.store.update_status(rid, "进行中") if sm.can_transition(
                    self.store.get(rid).status, "进行中") else None
                self.execute(rid)
            else:
                self.store.set_queue_position(rid, active)
        elif plan_decision.action == "revise_plan":
            self._set_status(rid, "规划中")
        elif plan_decision.action == "reunderstand":
            self.wt.remove(proj["path"], rid)
            self._set_status(rid, "澄清中")

    def _plan(self, task, project_key, proj, worktree_path, requirement):
        spec_dir = planner.next_spec_dir(worktree_path, task.task_title or "")
        Path(worktree_path, spec_dir).mkdir(parents=True, exist_ok=True)
        engine_name = proj.get("engine", self.cfg.default_engine)
        spec = self.cfg.engine_spec(engine_name)
        prompt = (f"按 spec-kit 约定，在 {spec_dir}/ 下产出 spec.md、plan.md、tasks.md。\n"
                  f"需求：\n{requirement}\n严格按需求范围，不额外加功能。")
        log = str(Path(self.cfg.workspace_dir) / task.record_id / "plan.log")
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        self.engine_run(spec, worktree_path, prompt, log)
        tasks_md = str(Path(worktree_path, spec_dir, "tasks.md"))
        n = planner.count_tasks(tasks_md)
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        # 写回 task 元数据
        t = self.store.get(task.record_id)
        t.project = project_key
        t.engine = engine_name
        t.spec_dir = spec_dir
        t.base_branch = proj["base_branch"]
        t.plan_generated_at = now
        self.store._save(t)
        self.store.set_clarify_pointer(task.record_id, f"{spec_dir}/clarify.md")
        self._set_status(task.record_id, "待审批")
        self.notifier.send_plan(t, requirement, n, t.branch_name())

    # ---- execute ------------------------------------------------------
    def execute(self, record_id):
        if not self._acquire_lock(record_id):
            return
        try:
            self._execute_inner(record_id)
        finally:
            self._release_lock(record_id)

    def _resolve_wt(self, task):
        if self._resolve_worktree:
            return self._resolve_worktree(task)
        proj = self.cfg.projects[task.project]
        return str(Path(proj["path"]) / ".worktrees" / task.branch_name())

    def _execute_inner(self, record_id):
        task = self.store.get(record_id)
        start = time.time()
        wtpath = self._resolve_wt(task)
        spec = self.cfg.engine_spec(task.engine)
        log = str(Path(self.cfg.workspace_dir) / record_id / "execute.log")
        Path(log).parent.mkdir(parents=True, exist_ok=True)

        tasks_md = str(Path(wtpath, task.spec_dir, "tasks.md"))
        prompt = (f"按任务清单逐条实现，完成后通过测试。\n"
                  f"{Path(tasks_md).read_text() if Path(tasks_md).exists() else ''}")
        result = self.engine_run(spec, wtpath, prompt, log)
        if result != EngineResult.SUCCESS:
            stage = "编码超时" if result == EngineResult.TIMEOUT else "编码"
            self.notifier.send_failure(task, stage, "引擎执行失败", log, task.branch_name())
            self.store.update_status(record_id, "已停滞")
            return

        proj = self.cfg.projects.get(task.project, {})
        if not run_command(proj.get("test_command", ""), wtpath):
            self.notifier.send_failure(task, "测试", "测试失败", log, task.branch_name())
            self.store.update_status(record_id, "已停滞")
            return
        if not run_command(proj.get("build_command", ""), wtpath):
            self.notifier.send_failure(task, "构建", "构建失败", log, task.branch_name())
            self.store.update_status(record_id, "已停滞")
            return

        branch = task.branch_name()
        commit_msg = f"feat: {task.task_title}\n\nAuto-generated by auto-coder\nTask: {record_id}"
        pushed, change_stats = self.git.add_commit_push(wtpath, commit_msg, branch)
        if not pushed:
            self.notifier.send_failure(task, "推送", "push 失败", log, branch)
            self.store.update_status(record_id, "已停滞")
            return

        duration = f"{int((time.time() - start) // 60)} 分钟"
        timeline = f"{task.plan_generated_at or ''} 规划 → {datetime.now():%m-%d %H:%M} 已推送"
        self.store.complete(record_id, branch, "已完成并推送分支", timeline)
        self.notifier.send_complete(task, branch, change_stats, duration, timeline)

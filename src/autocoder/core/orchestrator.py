import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from autocoder.core import state_machine as sm
from autocoder.core import worktree as wt_mod
from autocoder.core import planner
from autocoder.core.clarify import ClarifyOrchestrator
from autocoder.core.predict import build_predict_prompt, parse_prediction
from autocoder.core.engine_runner import (
    run_engine, run_engine_capture, run_command, EngineResult,
)
from autocoder.models import Decision


# monitor 判定阈值（分钟）：每阶段允许的最长运行时间。
# execute 引擎自带 1800s(30min) 超时，留足余量取 60min；plan 预期更快取 20min。
_ZOMBIE_THRESHOLDS = {"进行中": ("execute", 60), "规划中": ("plan", 20)}


def is_zombie(stale: bool, alive: bool) -> bool:
    """僵尸判定核心：超过阈值(stale) 且 进程已死(not alive)。
    两个条件必须同时满足——只超时但进程还在 = 慢任务，不算僵尸。"""
    return stale and not alive



def _default_engine_run(spec, working_dir, prompt, log_file):
    return run_engine(spec, working_dir, prompt, log_file)


def _default_engine_capture(spec, working_dir, prompt, timeout=None):
    return run_engine_capture(spec, working_dir, prompt, timeout)


class _DefaultGit:
    @staticmethod
    def add_commit_push(worktree_path, commit_msg, branch, push=True):
        import subprocess
        subprocess.run(["git", "-C", worktree_path, "add", "-A"], check=False)
        stat = subprocess.run(
            ["git", "-C", worktree_path, "diff", "--cached", "--stat"],
            capture_output=True, text=True,
        ).stdout.strip().splitlines()
        change_stats = stat[-1] if stat else ""
        subprocess.run(["git", "-C", worktree_path, "commit", "-m", commit_msg],
                       capture_output=True, text=True)
        if not push:
            return True, change_stats
        pushed = subprocess.run(
            ["git", "-C", worktree_path, "push", "-u", "origin", branch],
            capture_output=True, text=True,
        )
        return pushed.returncode == 0, change_stats


class Orchestrator:
    def __init__(self, config, store, notifier, router,
                 worktree=wt_mod, git_ops=None, engine_run=None,
                 predict_fn=None, resolve_worktree=None, engine_capture=None):
        self.cfg = config
        self.store = store
        self.notifier = notifier
        self.router = router
        self.wt = worktree
        self.git = git_ops or _DefaultGit
        self.engine_run = engine_run or _default_engine_run
        self.engine_capture = engine_capture or _default_engine_capture
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
    def _engine_predict(self, description, project_path, prior_qa=None):
        """让编码引擎读项目代码，对需求做结构化预判。

        在 project_path 下跑引擎（claude --print），要求只输出一段 JSON：
        {modules, risks, scope_hint, acceptance_hint, ready, ready_reason, questions}。
        预判失败/超时/解析失败一律降级为空预判——绝不阻断澄清流程。
        prior_qa 非空时（第二轮起）把历轮问答喂回，让 AI 增量判断够没够。
        """
        from autocoder.core.clarify import Prediction
        if not project_path:
            return Prediction([], [], ok=False)
        spec = self._predict_engine_spec(project_path)
        if not spec:
            return Prediction([], [], ok=False)
        timeout = min(spec.get("timeout", 1800), 180)
        # 项目级 opt-in：配了 clarify_fanout.enabled 且有可用角色 → 多角色并行预判。
        # 否则走原有单次单角色路径（零额外引擎调用、零回归）。
        proj = self._predict_project_cfg(project_path)
        fanout_cfg = (proj or {}).get("clarify_fanout") or {}
        if fanout_cfg.get("enabled"):
            from autocoder.core import role_predict
            roles = role_predict.load_roles(fanout_cfg)
            if roles:
                # 各角色/综合步按各自 engine 名解析 spec，回退到项目澄清引擎。
                resolve = lambda name: self._engine_spec_for(proj, engine_name=name)
                return role_predict.fanout_predict(
                    roles=roles, description=description, prior_qa=prior_qa,
                    spec=spec, project_path=project_path, timeout=timeout,
                    engine_capture=self.engine_capture,
                    synth_timeout=min(spec.get("timeout", 1800), 180),
                    resolve_spec=resolve,
                    synth_engine=fanout_cfg.get("synth_engine"))
        prompt = build_predict_prompt(description, prior_qa)
        out = self.engine_capture(spec, project_path, prompt, timeout)
        return parse_prediction(out)

    def _predict_project_cfg(self, project_path):
        """按 project_path 反查项目配置 dict；找不到返回 None。"""
        for proj in self.cfg.projects.values():
            if proj.get("path") == project_path:
                return proj
        return None

    def _predict_engine_spec(self, project_path):
        """按 project_path 反查澄清阶段的引擎 spec；找不到用默认引擎。"""
        proj = self._predict_project_cfg(project_path)
        return self._engine_spec_for(proj, stage="clarify")

    def _engine_name_for(self, proj, stage=None, engine_name=None):
        """解析某阶段/显式引擎的引擎名。回退链：
        engine_name → proj.stage_engines[stage] → proj.engine → default_engine。
        不存在的引擎名一律降级为 default（容错优先，不阻断流程）。"""
        candidates = [
            engine_name,
            ((proj or {}).get("stage_engines") or {}).get(stage) if stage else None,
            (proj or {}).get("engine"),
            self.cfg.default_engine,
        ]
        for name in candidates:
            if name and name in self.cfg.engines:
                return name
        return self.cfg.default_engine

    def _engine_spec_for(self, proj, stage=None, engine_name=None):
        """解析某阶段/显式引擎的 spec dict；解析不到引擎返回 None。"""
        name = self._engine_name_for(proj, stage=stage, engine_name=engine_name)
        if not name:
            return None
        return self.cfg.engine_spec(name)

    def _set_status(self, record_id, to):
        cur = self.store.get(record_id).status
        if sm.can_transition(cur, to):
            self.store.update_status(record_id, to)
        else:
            raise RuntimeError(f"非法状态转移 {cur} -> {to}")

    # ---- dispatch / resume --------------------------------------------
    def dispatch_one(self):
        """取一条「待开始」需求，从澄清阶段开始驱动。"""
        pending = self.store.fetch_pending()
        if not pending:
            return
        rid = pending[0].record_id
        self._set_status(rid, "澄清中")
        self._drive(rid)

    def resume(self, record_id):
        """从需求当前状态续跑。供 CLI 模式下回退态（澄清中/规划中）的需求
        重新进入流程——线性 dispatch 在驳回后会停在回退态，靠本方法续上，
        而非依赖飞书回调。"""
        self._drive(record_id)

    # ---- feishu stateless advance (hermes skill model) ----------------
    def dispatch_feishu(self):
        """飞书模式：取一条「待开始」需求，发澄清卡后立即返回（不阻塞）。
        由 hermes cron/消息触发；后续每步靠卡片按钮 → advance() 推进。"""
        # 兜底促活：worker 在促活前崩溃会留下僵死队列，每次触发先扫一遍。
        for project_key in self.cfg.projects:
            self._wake_next_queued(project_key)
        pending = self.store.fetch_pending()
        if not pending:
            return
        task = pending[0]
        rid = task.record_id
        _, project_path = self._project_for(task)
        pred = self.clarify.predict(task.description, project_path)
        # 先发卡：失败会抛错，此时状态仍为「待开始」，可安全重试，
        # 避免任务被孤立在「澄清中」却没有卡片可点。
        self.notifier.send_clarify(task, pred, round_no=1)
        self._set_status(rid, "澄清中")
        # 持久化本轮发出的问题，下次 advance 才能把答案配回问题文本。
        pending_qs = [vars(q) for q in pred.questions]
        t = self.store.get(rid)
        t.progress = self.clarify.encode_progress(1, [], pending_qs)
        self.store._save(t)

    @staticmethod
    def _merge_answers(pending_qs: list, form: dict) -> list:
        """把卡片 form 答案按 question.key 配回问题文本，产出 qa 条目。
        无 pending（旧格式/trivial）时，退化为直接收编 form 的键值。

        两个增强字段：
        - `{key}__other`：选项题的自由补充。与选中值合并进同一答案；
          多选(list)→追加为一项，单选(str)→「值（补充：xxx）」，
          无选中值→补充即答案。
        - `__supplement`：整卡级补充说明，作为独立 qa 条目，不挂任何问题。"""
        form = form or {}
        qa = []
        if pending_qs:
            for q in pending_qs:
                key = q.get("key")
                ans = form.get(key)
                other = (form.get(f"{key}__other") or "").strip()
                if other:
                    if isinstance(ans, list):
                        ans = ans + [other]
                    elif ans:
                        ans = f"{ans}（补充：{other}）"
                    else:
                        ans = other
                qa.append({"ask": q.get("ask", key), "answer": ans})
        else:
            for k, v in form.items():
                if k == "__supplement" or k.endswith("__other"):
                    continue
                qa.append({"ask": k, "answer": v})
        supplement = (form.get("__supplement") or "").strip()
        if supplement:
            qa.append({"ask": "其他补充说明", "answer": supplement})
        return qa


    def advance_async(self, record_id: str, decision: Decision):
        """hermes 入口：把决策派给后台 worker 后立即返回。

        hermes 的同步 terminal 窗口只有 120s，而 advance 内部的 predict/
        synthesize 可能跑满 180s——若同步执行，进程会在发卡前被砍，任务卡死。
        故此处只做 fire-and-forget 启动（与 plan/execute 同构），真正的状态推进
        和发卡在脱离父进程组的后台 worker 里完成，不受 120s 限制。"""
        import json as _json
        self._launch_bg(
            "advance-worker", record_id, decision.action,
            "--stage", decision.stage or "",
            "--form", _json.dumps(decision.form or {}, ensure_ascii=False),
            "--input", decision.input_text or "",
        )

    def advance(self, record_id: str, decision: Decision):
        """处理一次卡片点击决策，发出下一张卡后返回。
        在后台 worker 进程里同步执行（由 advance_async 启动），不受 hermes 120s 限制。"""
        status = self.store.get(record_id).status
        stage = decision.stage
        action = decision.action
        # 重试/重澄清来自僵尸告警卡或失败卡，是运维恢复操作：按 action 路由，
        # 不受当前状态机出边约束（已停滞无出边，需特判放行）。
        if action in ("retry_execute", "retry_plan", "reclarify"):
            self._advance_retry(record_id, decision)
        elif status == "澄清中" and stage == "clarify":
            self._advance_clarify(record_id, decision)
        elif status in ("待立项", "澄清中") and stage == "charter":
            self._advance_charter(record_id, decision)
        elif status == "待审批" and stage == "plan":
            self._advance_plan(record_id, decision)
        else:
            raise ValueError(f"无法 advance: status={status}, stage={stage}")

    def _advance_retry(self, rid: str, decision: Decision):
        """僵尸/失败恢复：直接置回工作态并重启对应 worker。
        直写状态跳过状态机校验——重试是人工运维动作，源状态可能是已停滞/进行中
        /规划中/澄清中等多种，统一放行。"""
        task = self.store.get(rid)
        action = decision.action
        if action == "retry_execute":
            self.store.update_status(rid, "进行中")
            self._launch_bg("execute", rid)
        elif action == "retry_plan":
            self.store.update_status(rid, "规划中")
            self._launch_bg("plan", rid)
        elif action == "reclarify":
            self.store.update_status(rid, "澄清中")
            self._restart_clarify(rid, task)

    def _advance_clarify(self, rid: str, decision: Decision):
        task = self.store.get(rid)
        _, project_path = self._project_for(task)
        state = self.clarify.decode_progress(task.progress)
        round_no = state["round"]
        # 把本轮答案配回上一卡的问题，累加进历史问答。
        new_qa = self._merge_answers(state["pending"], decision.form)
        qa = state["qa"] + new_qa
        # 把答案喂回引擎，让 AI 判断够没够、还该问什么。
        trivial = not decision.form and not qa
        pred = self.clarify.predict(task.description, project_path, prior_qa=qa) \
            if not trivial else None
        if self.clarify.ready_to_charter(pred, round_no, trivial):
            summary = self.clarify.synthesize(task.description, qa)
            self.store.update_summary(rid, summary, "")
            if not task.task_title:
                t = self.store.get(rid)
                t.task_title = t.description
                self.store._save(t)
            self._set_status(rid, "待立项")
            task = self.store.get(rid)
            self.notifier.send_charter(task, task.summary)
        else:
            next_round = round_no + 1
            self.notifier.send_clarify(task, pred, round_no=next_round)
            pending_qs = [vars(q) for q in pred.questions]
            t = self.store.get(rid)
            t.progress = self.clarify.encode_progress(next_round, qa, pending_qs)
            self.store._save(t)

    def _advance_charter(self, rid: str, decision: Decision):
        task = self.store.get(rid)
        action = decision.action
        if action == "reject_charter":
            self._set_status(rid, "已搁置")
            return
        if action in ("revise_charter", "rechat"):
            self._set_status(rid, "澄清中")
            self._restart_clarify(rid, task)
            return
        # approve_charter
        project_key, _ = self._project_for(task)
        if not project_key:
            self.notifier.send_failure(task, "立项", "无法匹配目标项目", "", "")
            return
        proj = self.cfg.projects[project_key]
        self.wt.create(proj["path"], proj["base_branch"], rid)
        self._set_status(rid, "规划中")
        self._launch_bg("plan", rid)

    def _advance_plan(self, rid: str, decision: Decision):
        task = self.store.get(rid)
        project_key, _ = self._project_for(task)
        proj = self.cfg.projects[project_key]
        action = decision.action
        if action == "approve_plan":
            active = self.wt.active_count(proj["path"], rid)
            if self.wt.gate_open(active, self.cfg.concurrency_limit):
                self.store.update_status(rid, "进行中")
                self._launch_bg("execute", rid)
            else:
                self._enqueue(rid, task, active)
        elif action == "revise_plan":
            notes = (decision.form or {}).get("revision_notes", "").strip()
            if notes:
                t = self.store.get(rid)
                t.revise_notes = notes
                self.store._save(t)
            self._set_status(rid, "规划中")
            self._launch_bg("plan", rid)
        elif action == "reunderstand":
            self.wt.remove(proj["path"], rid)
            self._set_status(rid, "澄清中")
            self._restart_clarify(rid, task)

    def _restart_clarify(self, rid, task):
        """回退到澄清第 1 轮：重新预判、发卡、清空累积问答。"""
        _, project_path = self._project_for(task)
        pred = self.clarify.predict(task.description, project_path)
        self.notifier.send_clarify(task, pred, round_no=1)
        pending_qs = [vars(q) for q in pred.questions]
        t = self.store.get(rid)
        t.progress = self.clarify.encode_progress(1, [], pending_qs)
        self.store._save(t)

    def _enqueue(self, rid, task, active):
        """并发槽已满：记排队位置 + 批准时间（FIFO 键），发「已排队」卡。
        状态停在「待审批」，由 queue_position 是否为 None 区分
        「待用户批准」与「已批准待槽位」两种语义。"""
        t = self.store.get(rid)
        t.queue_position = active
        t.approved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.store._save(t)
        self.notifier.send_queued(task, active)

    def _wake_next_queued(self, project_key):
        """并发槽释放后，按 approved_at FIFO 促活该项目下排队最久的一个。
        一次只起一个；其 execute 完成后会再次调用本方法，链式排空队列。
        并发安全由 execute 的重入锁兜底。"""
        proj = self.cfg.projects.get(project_key)
        if not proj:
            return
        queued = [
            t for t in self.store.fetch_by_status("待审批")
            if t.queue_position is not None
            and (t.project or self.cfg.match_project(t.description)) == project_key
        ]
        queued.sort(key=lambda t: t.approved_at or "")
        for t in queued:
            active = self.wt.active_count(proj["path"], t.record_id)
            if not self.wt.gate_open(active, self.cfg.concurrency_limit):
                break
            self.store.update_status(t.record_id, "进行中")
            tt = self.store.get(t.record_id)
            tt.queue_position = None
            self.store._save(tt)
            self._launch_bg("execute", t.record_id)
            break

    # ---- monitor: 僵尸任务巡检 ----------------------------------------
    def _started_at(self, task, kind):
        """取该阶段的开始时间戳，解析为 datetime；缺失或不可解析返回 None。"""
        raw = getattr(task, f"{kind}_started_at", None)
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S")
        except (ValueError, TypeError):
            return None

    def monitor(self):
        """扫描非终态任务，按时间戳+进程双信号判定僵尸，命中发告警卡。
        由独立 monitor cron 周期触发。不自动恢复——只告警，恢复靠人工点卡片按钮。

        覆盖：
        - 进行中/规划中：超阈值 且 worker 进程已死 → 僵尸告警
        - 澄清中且 progress 为空：发卡前崩溃，无卡可点 → 告警
        待开始/待审批(排队) 由 dispatch 兜底，终态(已完成/已搁置/已停滞)跳过。"""
        from autocoder.core import process_probe as probe
        now = datetime.now()
        for status, (kind, minutes) in _ZOMBIE_THRESHOLDS.items():
            for t in self.store.fetch_by_status(status):
                started = self._started_at(t, kind)
                # 无时间戳=老任务或写入前崩溃 → 视为已超时（可疑）。
                stale = started is None or (now - started).total_seconds() > minutes * 60
                alive = probe.worker_alive(t.record_id, kind)
                if is_zombie(stale, alive):
                    self.notifier.send_zombie_alert(t, status, kind, minutes)
        # 澄清中但无 progress：发澄清卡前就崩了，用户没有卡可点，会永久卡住。
        for t in self.store.fetch_by_status("澄清中"):
            if not (t.progress or "").strip():
                self.notifier.send_zombie_alert(t, "澄清中", "clarify", 0)

    def _launch_bg(self, *args):
        """以 `python -m autocoder.cli <args>` 后台启动，脱离父进程组，
        hermes 不会带走它。用 sys.executable + -m 而非裸 `auto-coder`：
        复用当前 venv 的解释器与已装包，绕开「auto-coder 不在子进程 PATH」。"""
        subprocess.Popen(
            [sys.executable, "-m", "autocoder.cli", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    # ---- feishu plan sub-command (called detached from _advance_charter) --
    def run_plan_and_notify(self, record_id: str):
        """规划引擎完整流程：建 spec/plan/tasks，发方案卡。
        由 `auto-coder plan <rid>` 后台调用（不交互）。"""
        task = self.store.get(record_id)
        project_key, _ = self._project_for(task)
        proj = self.cfg.projects[project_key]
        wtpath = self._resolve_wt_by_key(project_key, record_id)
        self._plan(task, project_key, proj, wtpath, task.summary,
                   revise_notes=task.revise_notes)

    def _drive(self, rid):
        """按当前状态路由到对应阶段，顺序贯穿到下一个卡点或终态。"""
        status = self.store.get(rid).status
        if status == "已搁置":
            self._set_status(rid, "待开始")
            self._set_status(rid, "澄清中")
            status = "澄清中"
        if status == "澄清中":
            if not self._run_clarify(rid):
                return
            status = "待立项"
        if status == "待立项":
            if not self._run_charter(rid):
                return
            status = "规划中"
        if status == "规划中":
            if not self._run_plan(rid):
                return
            status = "待审批"
        if status == "待审批":
            self._run_plan_approval(rid)

    def _project_for(self, task):
        key = task.project or self.cfg.match_project(task.description)
        path = self.cfg.projects[key]["path"] if key else ""
        return key, path

    def _run_clarify(self, rid) -> bool:
        """澄清循环（CLI 同步模式）。返回 True 表示进入待立项。
        轮次由 AI 决断（pred.ready / 无问题），仅以 clarify 的硬上限兜底。"""
        task = self.store.get(rid)
        _, project_path = self._project_for(task)
        round_no = 1
        qa = []
        while True:
            pred = self.clarify.predict(task.description, project_path,
                                        prior_qa=qa or None)
            if self.clarify.ready_to_charter(pred, round_no, trivial=False):
                break
            self.notifier.send_clarify(task, pred, round_no)
            decision = self.router.await_decision(rid, "clarify",
                                                  questions=pred.questions)
            qa += self._merge_answers([vars(q) for q in pred.questions],
                                      decision.form)
            round_no += 1
        summary = self.clarify.synthesize(task.description, qa)
        # 持久化摘要，使后续阶段（含 resume）无需重跑澄清即可取回上下文。
        self.store.update_summary(rid, summary, task.progress or "")
        # title 缺失时用 description 兜底，避免 commit message 出现 "feat: None"。
        if not task.task_title:
            t = self.store.get(rid)
            t.task_title = t.description
            self.store._save(t)
        self._set_status(rid, "待立项")
        return True

    def _run_charter(self, rid) -> bool:
        """立项审批。返回 True 表示进入规划中；False 表示停在回退/终态。"""
        task = self.store.get(rid)
        self.notifier.send_charter(task, task.summary)
        charter = self.router.await_decision(rid, "charter")
        if charter.action == "reject_charter":
            self._set_status(rid, "已搁置")
            return False
        if charter.action in ("revise_charter", "rechat"):
            self._set_status(rid, "澄清中")
            return False  # 停在回退态，由 resume 续跑
        project_key, _ = self._project_for(task)
        if not project_key:
            self.notifier.send_failure(task, "立项", "无法匹配目标项目", "", "")
            return False
        proj = self.cfg.projects[project_key]
        self.wt.create(proj["path"], proj["base_branch"], rid)
        self._set_status(rid, "规划中")
        return True

    def _run_plan(self, rid) -> bool:
        """跑规划引擎，产出 spec/plan/tasks。返回 True 表示进入待审批。"""
        task = self.store.get(rid)
        project_key, _ = self._project_for(task)
        proj = self.cfg.projects[project_key]
        wtpath = self._resolve_wt_by_key(project_key, rid)
        self._plan(task, project_key, proj, wtpath, task.summary,
                   revise_notes=task.revise_notes)
        return True

    def _run_plan_approval(self, rid):
        """方案审批。批准→入队或执行；退回→设回退态由 resume 续跑。"""
        task = self.store.get(rid)
        project_key, _ = self._project_for(task)
        proj = self.cfg.projects[project_key]
        plan_decision = self.router.await_decision(rid, "plan")
        if plan_decision.action == "approve_plan":
            active = self.wt.active_count(proj["path"], rid)
            if self.wt.gate_open(active, self.cfg.concurrency_limit):
                if sm.can_transition(self.store.get(rid).status, "进行中"):
                    self.store.update_status(rid, "进行中")
                self.execute(rid)
            else:
                self._enqueue(rid, task, active)
        elif plan_decision.action == "revise_plan":
            self._set_status(rid, "规划中")
        elif plan_decision.action == "reunderstand":
            self.wt.remove(proj["path"], rid)
            self._set_status(rid, "澄清中")

    def _resolve_wt_by_key(self, project_key, rid):
        proj = self.cfg.projects[project_key]
        return str(Path(proj["path"]) / ".worktrees" / wt_mod.branch_name(rid))


    def _plan(self, task, project_key, proj, worktree_path, requirement,
              revise_notes=None):
        # 阶段开始时间：供 monitor 判定规划是否卡死（双信号之一）。
        ts = self.store.get(task.record_id)
        ts.plan_started_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self.store._save(ts)
        spec_dir = planner.next_spec_dir(worktree_path, task.task_title or "")
        Path(worktree_path, spec_dir).mkdir(parents=True, exist_ok=True)
        # plan 与 execute 各自解析引擎：plan 引擎跑规划，execute 引擎此刻定下、
        # 写回 task.engine 供执行阶段直接读（execute 端无需再解析）。
        plan_engine = self._engine_name_for(proj, stage="plan")
        execute_engine = self._engine_name_for(proj, stage="execute")
        spec = self.cfg.engine_spec(plan_engine)
        prompt = (f"按 spec-kit 约定，在 {spec_dir}/ 下产出 spec.md、plan.md、tasks.md。\n"
                  f"需求：\n{requirement}\n严格按需求范围，不额外加功能。")
        if revise_notes:
            prompt += f"\n\n用户对上一版方案的修改意见（请在新方案中体现）：\n{revise_notes}"
        log = str(Path(self.cfg.workspace_dir) / task.record_id / "plan.log")
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        result = self.engine_run(spec, worktree_path, prompt, log)
        if result != EngineResult.SUCCESS:
            stage = "规划超时" if result == EngineResult.TIMEOUT else "规划"
            self.notifier.send_failure(task, stage, "引擎执行失败", log,
                                       task.branch_name())
            self.store.update_status(task.record_id, "已停滞")
            return
        tasks_md_path = Path(worktree_path, spec_dir, "tasks.md")
        spec_md_path = Path(worktree_path, spec_dir, "spec.md")
        n = planner.count_tasks(str(tasks_md_path))
        spec_content = self._read_spec_content(spec_md_path, tasks_md_path)
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        # 写回 task 元数据
        t = self.store.get(task.record_id)
        t.project = project_key
        t.engine = execute_engine
        t.spec_dir = spec_dir
        t.base_branch = proj["base_branch"]
        t.plan_generated_at = now
        self.store._save(t)
        self.store.set_clarify_pointer(task.record_id, f"{spec_dir}/clarify.md")
        self._set_status(task.record_id, "待审批")
        self.notifier.send_plan(t, requirement, n, t.branch_name(), spec_content)

    @staticmethod
    def _read_spec_content(spec_md_path, tasks_md_path, max_len=4000):
        """读 spec.md 和 tasks.md，拼成供卡片展示的内容，总长度截断到 max_len。"""
        parts = []
        for label, path in [("任务清单", tasks_md_path), ("方案设计", spec_md_path)]:
            if Path(path).exists():
                text = Path(path).read_text().strip()
                if text:
                    parts.append(f"## {label}\n{text}")
        if not parts:
            return ""
        combined = "\n\n---\n\n".join(parts)
        if len(combined) > max_len:
            combined = combined[:max_len] + "\n\n…（内容已截断）"
        return combined

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
        # 阶段开始时间：供 monitor 判定执行是否卡死（双信号之一）。
        task.execute_started_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self.store._save(task)
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
        # task_title 缺失时（如 resume/抢救路径跳过了 clarify 兜底）用 description，
        # 避免 commit message 出现 "feat: None"。
        title = task.task_title or task.description
        commit_msg = f"feat: {title}\n\nAuto-generated by auto-coder\nTask: {record_id}"
        push = proj.get("push", True)
        pushed, change_stats = self.git.add_commit_push(wtpath, commit_msg, branch, push)
        if not pushed:
            self.notifier.send_failure(task, "推送", "push 失败", log, branch)
            self.store.update_status(record_id, "已停滞")
            return

        duration = f"{int((time.time() - start) // 60)} 分钟"
        done_label = "已推送" if push else "已提交(未推送)"
        timeline = f"{task.plan_generated_at or ''} 规划 → {datetime.now():%m-%d %H:%M} {done_label}"
        self.store.complete(record_id, branch, f"已完成并{done_label}分支", timeline)
        self.notifier.send_complete(task, branch, change_stats, duration, timeline)
        # 已推送 → worktree 使命完成，回收并发槽并促活下一个排队任务。
        # 未推送(push:false)时 worktree 是改动的唯一载体，保留不删。
        if push:
            self.wt.remove(proj["path"], record_id)
            self._wake_next_queued(task.project)

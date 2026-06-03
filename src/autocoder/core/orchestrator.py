import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from autocoder.core import state_machine as sm
from autocoder.core import worktree as wt_mod
from autocoder.core import planner
from autocoder.core.clarify import ClarifyOrchestrator
from autocoder.core.engine_runner import (
    run_engine, run_engine_capture, run_command, EngineResult,
)
from autocoder.models import Decision


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
            return Prediction([], [])
        spec = self._predict_engine_spec(project_path)
        if not spec:
            return Prediction([], [])
        prompt = self._build_predict_prompt(description, prior_qa)
        # 预判要快，给较短超时，避免卡澄清。引擎自带 timeout 可能是 1800s。
        timeout = min(spec.get("timeout", 1800), 180)
        out = self.engine_capture(spec, project_path, prompt, timeout)
        return self._parse_prediction(out)

    def _predict_engine_spec(self, project_path):
        """按 project_path 反查它配的引擎 spec；找不到用默认引擎。"""
        for proj in self.cfg.projects.values():
            if proj.get("path") == project_path:
                name = proj.get("engine", self.cfg.default_engine)
                return self.cfg.engine_spec(name)
        if self.cfg.default_engine:
            return self.cfg.engine_spec(self.cfg.default_engine)
        return None

    def _build_predict_prompt(self, description, prior_qa):
        import json as _json
        lines = [
            "你是需求澄清助手。请先快速浏览当前项目代码结构，再对下面的需求做预判。",
            "",
            f"需求：{description}",
            "",
        ]
        if prior_qa:
            lines += [
                "用户已在前几轮澄清中回答了以下问题，请据此判断信息是否已足够立项；",
                "已答的不要再问，只针对仍然影响实现/验收的关键缺口提问：",
                _json.dumps(prior_qa, ensure_ascii=False),
                "",
            ]
        lines += [
            "只输出一段 JSON（不要任何额外文字、不要 markdown 代码块），字段：",
            '{',
            '  "modules": ["预判涉及的文件或模块名", ...],',
            '  "risks": ["实现该需求的技术风险点", ...],',
            '  "scope_hint": "一句话预判范围边界：做什么/明确不做什么",',
            '  "acceptance_hint": "一句话预判验收标准：怎样算完成",',
            '  "ready": true/false,',
            '  "ready_reason": "一句话说明为何够了/还缺什么",',
            '  "questions": [',
            '    {"key": "短标识", "ask": "要问用户的问题",',
            '     "type": "text | single_select | multi_select",',
            '     "options": ["仅 select 类型需要的候选项", ...]}',
            '  ]',
            '}',
            "要求：",
            "- modules 必须是项目里真实存在的文件/模块。",
            "- 只问真正阻断实现或验收的关键点，能从代码/需求推断的不要问。",
            "- 最多提 3 个问题；信息已足够时 ready=true 且 questions 为空数组。",
            "- 能用选项回答的尽量用 single_select/multi_select，减轻用户负担。",
        ]
        return "\n".join(lines)

    def _parse_prediction(self, out):
        import json as _json
        import re
        from autocoder.core.clarify import Prediction, Question
        if not out or not out.strip():
            return Prediction([], [])
        # 引擎可能裹了 ```json ``` 或夹带前后文字，抓第一个 {...} 块。
        text = out.strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return Prediction([], [])
        try:
            data = _json.loads(m.group(0))
        except (ValueError, TypeError):
            return Prediction([], [])
        if not isinstance(data, dict):
            return Prediction([], [])

        def _as_list(v):
            if isinstance(v, list):
                return [str(x) for x in v if str(x).strip()]
            if isinstance(v, str) and v.strip():
                return [v.strip()]
            return []

        def _parse_questions(v):
            qs = []
            if not isinstance(v, list):
                return qs
            for i, item in enumerate(v):
                # 兼容引擎仍按旧格式返回纯字符串问题的情况。
                if isinstance(item, str):
                    if item.strip():
                        qs.append(Question(key=f"q{i}", ask=item.strip()))
                    continue
                if not isinstance(item, dict):
                    continue
                ask = str(item.get("ask", "") or "").strip()
                if not ask:
                    continue
                qtype = str(item.get("type", "text") or "text").strip()
                if qtype not in ("text", "single_select", "multi_select"):
                    qtype = "text"
                opts = _as_list(item.get("options"))
                if qtype != "text" and not opts:
                    qtype = "text"  # select 无候选项则退化为文本
                key = str(item.get("key", "") or f"q{i}").strip() or f"q{i}"
                qs.append(Question(key=key, ask=ask, type=qtype, options=opts))
            return qs[:3]  # 每张卡最多 3 个问题

        return Prediction(
            modules=_as_list(data.get("modules")),
            risks=_as_list(data.get("risks")),
            scope_hint=str(data.get("scope_hint", "") or "").strip(),
            acceptance_hint=str(data.get("acceptance_hint", "") or "").strip(),
            ready=bool(data.get("ready", False)),
            ready_reason=str(data.get("ready_reason", "") or "").strip(),
            questions=_parse_questions(data.get("questions")),
        )

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
        无 pending（旧格式/trivial）时，退化为直接收编 form 的键值。"""
        qa = []
        if pending_qs:
            for q in pending_qs:
                key = q.get("key")
                ans = (form or {}).get(key)
                qa.append({"ask": q.get("ask", key), "answer": ans})
        else:
            for k, v in (form or {}).items():
                qa.append({"ask": k, "answer": v})
        return qa


    def advance(self, record_id: str, decision: Decision):
        """处理一次卡片点击决策，发出下一张卡后返回。
        供 hermes auto-coder-agent skill 调用：每次卡片点击 = 一次 advance 调用。"""
        status = self.store.get(record_id).status
        stage = decision.stage
        if status == "澄清中" and stage == "clarify":
            self._advance_clarify(record_id, decision)
        elif status in ("待立项", "澄清中") and stage == "charter":
            self._advance_charter(record_id, decision)
        elif status == "待审批" and stage == "plan":
            self._advance_plan(record_id, decision)
        else:
            raise ValueError(f"无法 advance: status={status}, stage={stage}")

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
                self.store.set_queue_position(rid, active)
        elif action == "revise_plan":
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
        self._plan(task, project_key, proj, wtpath, task.summary)

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
        self._plan(task, project_key, proj, wtpath, task.summary)
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
                self.store.set_queue_position(rid, active)
        elif plan_decision.action == "revise_plan":
            self._set_status(rid, "规划中")
        elif plan_decision.action == "reunderstand":
            self.wt.remove(proj["path"], rid)
            self._set_status(rid, "澄清中")

    def _resolve_wt_by_key(self, project_key, rid):
        proj = self.cfg.projects[project_key]
        return str(Path(proj["path"]) / ".worktrees" / wt_mod.branch_name(rid))


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

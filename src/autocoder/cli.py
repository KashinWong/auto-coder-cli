import argparse
import json
import sys
import uuid

from autocoder.factory import build
from autocoder.core.orchestrator import Orchestrator
from autocoder.models import Decision, Task


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    # --config 可出现在子命令前或后：放进公共 parent parser 给每个子命令继承。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="config.yaml")

    parser = argparse.ArgumentParser(prog="auto-coder", parents=[common])
    sub = parser.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", parents=[common], help="新增需求")
    p_add.add_argument("description")
    p_add.add_argument("--priority", default="重要不紧急")

    sub.add_parser("dispatch", parents=[common], help="拉取并处理一条 pending 需求（CLI 阻塞模式）")
    sub.add_parser("dispatch-feishu", parents=[common], help="飞书模式：发澄清卡后立即返回")

    p_resume = sub.add_parser("resume", parents=[common],
                              help="续跑回退态需求（澄清中/规划中）")
    p_resume.add_argument("record_id")

    p_exec = sub.add_parser("execute", parents=[common], help="执行指定需求")
    p_exec.add_argument("record_id")

    p_plan = sub.add_parser("plan", parents=[common], help="（后台）运行规划引擎并发方案卡")
    p_plan.add_argument("record_id")

    p_advance = sub.add_parser("advance", parents=[common],
                               help="处理一次飞书卡片点击决策（供 hermes skill 调用）")
    p_advance.add_argument("record_id")
    p_advance.add_argument("action")
    p_advance.add_argument("--stage", default="")
    p_advance.add_argument("--form", default="{}", help="JSON 字符串，澄清表单")
    p_advance.add_argument("--input", dest="input_text", default="",
                           help="单行文本输入（改方案说明等）")

    sub.add_parser("status", parents=[common], help="列出所有需求状态")

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # 未知子命令/参数错误：返回非零而非抛出，便于编程式调用。
        return e.code if isinstance(e.code, int) else 2
    if args.cmd is None:
        parser.print_help()
        return 1

    cfg, store, notifier, router = build(args.config)

    if args.cmd == "add":
        rid = uuid.uuid4().hex[:12]
        task = Task(record_id=rid, description=args.description,
                    priority=args.priority)
        # FeishuBaseStore.add 返回 bitable 生成的 record_id
        result = store.add(task)
        actual_rid = result if isinstance(result, str) else rid
        print(f"已添加需求 {actual_rid}: {args.description}")
        return 0

    if args.cmd == "dispatch":
        Orchestrator(cfg, store, notifier, router).dispatch_one()
        return 0

    if args.cmd == "dispatch-feishu":
        Orchestrator(cfg, store, notifier, router).dispatch_feishu()
        return 0

    if args.cmd == "resume":
        Orchestrator(cfg, store, notifier, router).resume(args.record_id)
        return 0

    if args.cmd == "execute":
        Orchestrator(cfg, store, notifier, router).execute(args.record_id)
        return 0

    if args.cmd == "plan":
        Orchestrator(cfg, store, notifier, router).run_plan_and_notify(args.record_id)
        return 0

    if args.cmd == "advance":
        try:
            form = json.loads(args.form) if args.form and args.form.strip() != "{}" else {}
        except json.JSONDecodeError as e:
            print(f"--form 解析失败: {e}", file=sys.stderr)
            return 2
        decision = Decision(
            action=args.action,
            record_id=args.record_id,
            stage=args.stage,
            form=form,
            input_text=args.input_text,
        )
        Orchestrator(cfg, store, notifier, router).advance(args.record_id, decision)
        return 0

    if args.cmd == "status":
        for t in store.fetch_pending() or []:
            print(f"[{t.status}] {t.record_id}  {t.description}  ({t.priority})")
        # fetch_pending 只列待开始；status 应列全部
        if hasattr(store, "_all"):
            for t in store._all():
                if t.status != "待开始":
                    print(f"[{t.status}] {t.record_id}  {t.description}")
        return 0

    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())

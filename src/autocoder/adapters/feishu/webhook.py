import queue

from autocoder.adapters.router import EventRouter
from autocoder.models import Decision


class FeishuWebhookRouter(EventRouter):
    """进阶用法：替代 hermes 网关，用一个常驻 HTTP 服务接住飞书卡片回调。

    原系统里按钮点击经飞书事件订阅回到网关，网关合成命令调起 agent。开源
    场景无网关，需自建：跑 serve() 起 FastAPI 接 /feishu/callback，把
    回调里的 ac_action/record_id/_form 投进队列；await_decision 阻塞取队列。

    NOTE: 这要求 dispatch 以常驻服务模式运行（非一次性 CLI 调用）。
    """

    def __init__(self, feishu_config: dict):
        self._queues: dict[str, queue.Queue] = {}

    def _q(self, record_id: str) -> queue.Queue:
        return self._queues.setdefault(record_id, queue.Queue())

    def deliver(self, record_id: str, decision: Decision):
        self._q(record_id).put(decision)

    def await_decision(self, record_id: str, stage: str) -> Decision:
        return self._q(record_id).get()

    def serve(self, host="0.0.0.0", port=8000):  # pragma: no cover
        from fastapi import FastAPI, Request
        import uvicorn
        app = FastAPI()

        @app.post("/feishu/callback")
        async def callback(req: Request):
            body = await req.json()
            value = body.get("action", {}).get("value", {})
            self.deliver(value.get("record_id", ""), Decision(
                action=value.get("ac_action", ""),
                record_id=value.get("record_id", ""),
                stage=value.get("stage", ""),
                form=body.get("action", {}).get("form_value", {})))
            return {"code": 0}

        uvicorn.run(app, host=host, port=port)

from autocoder.config import load_config
from autocoder.adapters.store import JsonTaskStore
from autocoder.adapters.notifier import CliNotifier
from autocoder.adapters.router import CliRouter


def build(config_path: str):
    cfg = load_config(config_path)
    store = _build_store(cfg)
    notifier = _build_notifier(cfg)
    router = _build_router(cfg)
    return cfg, store, notifier, router


def _build_store(cfg):
    kind = cfg.adapters.get("store", "json")
    if kind == "json":
        return JsonTaskStore(cfg.workspace_dir)
    if kind == "feishu":
        from autocoder.adapters.feishu.store import FeishuBaseStore
        return FeishuBaseStore(cfg.feishu)
    raise ValueError(f"unknown store adapter: {kind}")


def _build_notifier(cfg):
    kind = cfg.adapters.get("notifier", "cli")
    if kind == "cli":
        return CliNotifier()
    if kind == "feishu":
        from autocoder.adapters.feishu.notifier import FeishuCardNotifier
        return FeishuCardNotifier(cfg.feishu)
    raise ValueError(f"unknown notifier adapter: {kind}")


def _build_router(cfg):
    kind = cfg.adapters.get("router", "cli")
    if kind == "cli":
        return CliRouter()
    if kind == "feishu_webhook":
        from autocoder.adapters.feishu.webhook import FeishuWebhookRouter
        return FeishuWebhookRouter(cfg.feishu)
    raise ValueError(f"unknown router adapter: {kind}")

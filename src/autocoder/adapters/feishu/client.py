import json
import os
import time
from pathlib import Path

_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"


def render_card(template_path: str, **tokens) -> str:
    """读卡片模板，替换 {{TOKEN}}。值做 JSON 转义以免换行/引号破坏卡片。"""
    content = Path(template_path).read_text()
    for key, val in tokens.items():
        escaped = json.dumps(str(val), ensure_ascii=False)[1:-1]
        content = content.replace("{{" + key + "}}", escaped)
    return content


class FeishuClient:
    """用 hermes 网关同款 app 凭证发交互卡片。凭证从环境变量读，不硬编码。

    SAME-APP 规则：交互卡片按钮回调只会回到「发卡的那个 app」。若用别的
    app 发卡，所有按钮点击都会失联。务必用接收事件的同一 app 凭证。

    requests 是可选依赖（pip install auto-coder-oss[feishu]），延迟导入，
    使纯函数 render_card 在未安装时仍可用。
    """

    def __init__(self, app_id=None, app_secret=None):
        self.app_id = app_id or os.environ.get("FEISHU_APP_ID")
        self.app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET")
        self._token = None
        self._token_exp = 0

    def _tenant_token(self) -> str:
        import requests
        now = time.time()
        if self._token and now < self._token_exp:
            return self._token
        resp = requests.post(_TOKEN_URL, json={
            "app_id": self.app_id, "app_secret": self.app_secret})
        data = resp.json()
        self._token = data["tenant_access_token"]
        self._token_exp = now + 6000  # ~100min
        return self._token

    def send_card(self, chat_id: str, card_json: str) -> bool:
        import requests
        token = self._tenant_token()
        resp = requests.post(_MSG_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"receive_id": chat_id, "msg_type": "interactive",
                  "content": card_json})
        return resp.json().get("code") == 0

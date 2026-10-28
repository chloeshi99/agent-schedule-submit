"""真实模型客户端（OpenAI 兼容协议）。

本文件在本次提交中**未被执行**（仓库不包含任何密钥，默认离线运行），
但代码路径完整：配置 .env 后即可切换，用于验证真实模型的 ReAct 决策。

协议约定：模型每次只返回一个 JSON 对象，字段见 `Action`。
"""

from __future__ import annotations

import json
import urllib.request

from .base import Action

SYSTEM_PROMPT = (
    "你是素材审核与发布排期助手。你只能输出一个 JSON 对象，"
    "格式为 {\"action\":\"tool\"|\"finish\", \"tool\":名字, \"args\":{}, \"rationale\":理由, \"final\":{}}。"
    "可用工具见 tool_specs。禁止输出真实发布动作。"
)


class OpenAICompatClient:
    """通过 OpenAI 兼容 /chat/completions 接口调用真实模型。"""

    name = "openai-compat"

    def __init__(self, base_url: str, api_key: str, model: str, temperature: float = 0.0, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    def decide(self, messages: list[dict], tool_specs: list[dict]) -> Action:
        system = SYSTEM_PROMPT + "\n\n可用工具:\n" + json.dumps(tool_specs, ensure_ascii=False, indent=2)
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}] + messages,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        return Action.from_json(content)

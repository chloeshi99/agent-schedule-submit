"""配置加载：环境变量 + 默认值。

设计要点：
- 无需任何环境变量即可离线运行（离线 Mock 模型）。
- 真实模型通过 .env 中的 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 启用。
- MAX_TOOL_CALLS 为执行步数上限，达到即返回明确的未完成状态。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

DEFAULT_MAX_TOOL_CALLS = 8


def _load_dotenv(path: Path) -> None:
    """极简 .env 读取，避免为离线运行引入额外依赖。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass
class Config:
    root: Path = ROOT
    data_dir: Path = DATA_DIR
    db_path: Path = field(default_factory=lambda: DATA_DIR / "drafts.sqlite3")
    offline: bool = True
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    temperature: float = 0.0
    trace_dir: Path = field(default_factory=lambda: ROOT / "examples" / "traces")

    @classmethod
    def load(cls, env: dict | None = None) -> "Config":
        _load_dotenv(ROOT / ".env")
        env = dict(os.environ if env is None else env)

        max_calls = env.get("MAX_TOOL_CALLS")
        try:
            max_calls_int = int(max_calls) if max_calls else DEFAULT_MAX_TOOL_CALLS
        except ValueError:
            max_calls_int = DEFAULT_MAX_TOOL_CALLS

        api_key = env.get("LLM_API_KEY", "")
        # 只有显式提供 API Key 才认为可以走真实模型；否则一律离线 Mock。
        offline = env.get("AGENT_OFFLINE", "").strip().lower() not in ("0", "false", "no")
        if not api_key:
            offline = True

        return cls(
            offline=offline,
            llm_base_url=env.get("LLM_BASE_URL", cls.llm_base_url),
            llm_api_key=api_key,
            llm_model=env.get("LLM_MODEL", cls.llm_model),
            max_tool_calls=max(1, max_calls_int),
            temperature=float(env.get("LLM_TEMPERATURE", "0")),
        )

    def load_json(self, name: str) -> dict:
        return json.loads((self.data_dir / name).read_text(encoding="utf-8"))

"""提交前自检：扫描仓库中是否包含真实密钥或隐私数据。

题目要求「不提交真实密钥或隐私数据」。本脚本用于提交前自查，
也可作为 CI 步骤接入。

用法：
    python -m scripts.check_secrets
退出码：0 = 未发现风险；1 = 发现疑似泄漏。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 常见密钥格式
PATTERNS = [
    (r"sk-[A-Za-z0-9]{16,}", "OpenAI 风格密钥"),
    (r"ghp_[A-Za-z0-9]{20,}", "GitHub Personal Access Token"),
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key"),
    (r"AIza[0-9A-Za-z_\-]{30,}", "Google API Key"),
    (r"xox[baprs]-[0-9A-Za-z\-]{10,}", "Slack Token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥"),
]

# 赋值形式的密钥（要求等号右侧有实际值且非空）
ASSIGN = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|access[_-]?key)\b\s*[:=]\s*[\"']?([^\s\"'#]{8,})"
)

# 明确安全的上下文：这些写法是从环境变量取值，不是硬编码
SAFE_CONTEXT = re.compile(
    r"(?i)(env\.get|environ|getenv|os\.environ|config\.|self\.|\.llm_api_key|api_key=api_key|"
    r"api_key=self|api_key=config|api_key=client)"
)

# 允许的假值
ALLOWED = {
    "", "none", "null", "your_api_key", "changeme", "xxx", "example",
    "placeholder", "dummy", "fake", "test_key", "your-key-here",
    "sk-xxx", "sk-yourkey",
}

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}
SKIP_EXT = {".sqlite3", ".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".ico"}


def _is_allowed(value: str) -> bool:
    v = value.strip().strip("\"'")
    if v.lower() in ALLOWED:
        return True
    # 形如 <...> 或 {…} 的占位符
    if v.startswith("<") or v.startswith("{") or v.startswith("${"):
        return True
    # 形如 your_xxx 的占位符
    if re.fullmatch(r"(?i)(your|my|the)?[_\- ]?[a-z0-9_\-]*(key|token|secret)[a-z0-9_\-]*", v):
        return True
    return False


def main() -> int:
    findings: list[tuple[str, int, str, str]] = []

    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            path = Path(dirpath) / fn
            if path.suffix.lower() in SKIP_EXT:
                continue
            if path.name == ".env":  # .env 本身是本地文件，已被 .gitignore 排除
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = path.relative_to(ROOT)
            for lineno, line in enumerate(text.splitlines(), 1):
                for pattern, desc in PATTERNS:
                    if re.search(pattern, line):
                        findings.append((str(rel), lineno, desc, line.strip()[:100]))
                m = ASSIGN.search(line)
                if m and not _is_allowed(m.group(2)) and not SAFE_CONTEXT.search(line):
                    findings.append((str(rel), lineno, "疑似硬编码密钥", line.strip()[:100]))

    # 隐私数据粗查：手机号 / 身份证
    privacy = re.compile(r"(?<!\d)(1[3-9]\d{9}|\d{17}[\dXx])(?!\d)")
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            path = Path(dirpath) / fn
            if path.suffix.lower() in SKIP_EXT:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if privacy.search(line):
                    findings.append((str(path.relative_to(ROOT)), lineno, "疑似手机号/身份证", line.strip()[:100]))

    if findings:
        print("发现疑似风险内容：")
        for rel, lineno, desc, snippet in findings:
            print(f"  {rel}:{lineno}  [{desc}]  {snippet}")
        print(f"\n共 {len(findings)} 处。请处理后再提交。")
        return 1

    print(f"未发现真实密钥或隐私数据（已扫描 {ROOT}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""LLM 结构化输出的健壮 JSON 解析工具。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict


def parse_json_block(raw: str) -> Dict[str, Any]:
    """从 LLM 输出中提取并解析 JSON 对象。

    支持：
    - 纯 JSON 文本
    - ```json ... ``` 代码块包裹
    - 前后夹杂解释性文字
    - 首尾多余括号容错
    """
    text = raw.strip()
    block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if block:
        text = block.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"输出中未找到 JSON 对象: {raw[:200]}")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("输出不是 JSON 对象")
    return data


def safe_json(raw: str) -> Dict[str, Any]:
    """同 parse_json_block，但失败时返回空字典（不抛异常）。"""
    try:
        return parse_json_block(raw)
    except (ValueError, json.JSONDecodeError):
        return {}

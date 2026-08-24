"""DeepSeek 人民币计费与成本控制。

价格依据用户提供的 2026-08-22 DeepSeek 官方价格表，单位均为元 / 1M tokens：

| 模型 | 时段 | 输入缓存命中 | 输入缓存未命中 | 输出 |
| --- | --- | ---: | ---: | ---: |
| deepseek-v4-flash | 空闲 / 高峰 | 0.05 / 0.10 | 1.50 / 3.00 | 4.50 / 9.00 |
| deepseek-v4-pro | 空闲 / 高峰 | 0.15 / 0.30 | 4.50 / 9.00 | 13.50 / 27.00 |

高峰时段为北京时间 09:00–12:00、14:00–18:00；其余时间为空闲时段。
"""

from __future__ import annotations

from datetime import datetime, time as clock_time
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import threading
import time
from typing import Any, Dict, List, Optional


BEIJING = ZoneInfo("Asia/Shanghai")

#: 每 1M tokens 的人民币价格；预算、实际账本和前端预测共用此数据源。
PRICING_CNY: Dict[str, Dict[str, Dict[str, float]]] = {
    "deepseek-v4-flash": {
        "off_peak": {"input_hit": 0.05, "input_miss": 1.50, "output": 4.50},
        "peak": {"input_hit": 0.10, "input_miss": 3.00, "output": 9.00},
    },
    "deepseek-v4-pro": {
        "off_peak": {"input_hit": 0.15, "input_miss": 4.50, "output": 13.50},
        "peak": {"input_hit": 0.30, "input_miss": 9.00, "output": 27.00},
    },
}

# 兼容旧模型名：统一按 Flash 计价。
for _legacy in ("deepseek-chat", "deepseek-reasoner"):
    PRICING_CNY[_legacy] = PRICING_CNY["deepseek-v4-flash"]


def pricing_period(at: Optional[datetime] = None) -> str:
    """返回当前北京时间的 ``peak`` 或 ``off_peak``。"""
    now = at or datetime.now(BEIJING)
    now = now.replace(tzinfo=BEIJING) if now.tzinfo is None else now.astimezone(BEIJING)
    current = now.time()
    return "peak" if (clock_time(9) <= current < clock_time(12)
                      or clock_time(14) <= current < clock_time(18)) else "off_peak"


def price_for(model: str, at: Optional[datetime] = None) -> Optional[Dict[str, float]]:
    """返回模型在指定北京时间的有效人民币单价。"""
    prices = PRICING_CNY.get(model)
    return dict(prices[pricing_period(at)]) if prices else None


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：中文约 0.8 token/字，其他文本约 4 字符/token。"""
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return int(cjk * 0.8 + (len(text) - cjk) / 4) + 8


def estimate_cost_cny(model: str, input_chars: int = 0,
                      output_tokens: int = 0,
                      at: Optional[datetime] = None) -> float:
    """保守估算一次调用：输入按缓存未命中、输出按上限。"""
    price = price_for(model, at) or price_for("deepseek-v4-flash", at)
    return (input_chars / 4 / 1_000_000 * price["input_miss"]
            + output_tokens / 1_000_000 * price["output"])


class CostTracker:
    """人民币成本追踪器（线程安全，可选原子持久化账本）。"""

    def __init__(self, budget_cny: Optional[float] = None,
                 model_hint: str = "deepseek-v4-flash",
                 storage_path: Optional[str | Path] = None) -> None:
        self.budget_cny = budget_cny
        self.model_hint = model_hint
        self.entries: List[Dict[str, Any]] = []
        self.lock = threading.Lock()
        self._rejected = 0
        self.storage_path = Path(storage_path) if storage_path else None
        self._load()
        # 显式传入的预算优先于历史账本；None 保留账本中的预算，调用方
        # 可随后用 set_budget(None) 明确取消预算。
        if budget_cny is not None:
            self.budget_cny = budget_cny

    def _load(self) -> None:
        """从账本恢复非敏感成本数据；损坏账本按空账本处理。"""
        if self.storage_path is None or not self.storage_path.is_file():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            entries = data.get("entries", [])
            if isinstance(entries, list):
                self.entries = [entry for entry in entries
                                if isinstance(entry, dict)]
            budget = data.get("budget_cny")
            self.budget_cny = (float(budget) if budget is not None else None)
            self._rejected = max(0, int(data.get("rejected", 0)))
        except (OSError, ValueError, TypeError):
            self.entries = []
            self._rejected = 0

    def _persist_locked(self) -> None:
        """在持锁状态下原子写账本，避免 MCP 读取到半份 JSON。"""
        if self.storage_path is None:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "currency": "CNY",
            "budget_cny": self.budget_cny,
            "rejected": self._rejected,
            "entries": self.entries,
            "updated_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
        }
        tmp = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.storage_path)

    def set_budget(self, budget_cny: Optional[float]) -> None:
        with self.lock:
            self.budget_cny = budget_cny
            self._persist_locked()

    def clear(self) -> int:
        with self.lock:
            count = len(self.entries)
            self.entries = []
            self._rejected = 0
            self._persist_locked()
            return count

    def record(self, provider: str, model: str, prompt_tokens: int,
               completion_tokens: int, purpose: str = "",
               cached_prompt_tokens: int = 0) -> float:
        """记录一次调用，按 API 返回的缓存 token（如有）准确拆分计费。"""
        period = pricing_period()
        prompt_tokens = max(0, int(prompt_tokens))
        completion_tokens = max(0, int(completion_tokens))
        cached_prompt_tokens = min(prompt_tokens, max(0, int(cached_prompt_tokens)))
        if provider == "ollama":
            price = {"input_hit": 0.0, "input_miss": 0.0, "output": 0.0}
        else:
            price = (price_for(model) or price_for(self.model_hint)
                     or price_for("deepseek-v4-flash"))
        miss_tokens = prompt_tokens - cached_prompt_tokens
        cost = (cached_prompt_tokens / 1_000_000 * price["input_hit"]
                + miss_tokens / 1_000_000 * price["input_miss"]
                + completion_tokens / 1_000_000 * price["output"])
        with self.lock:
            self.entries.append({
                "time": time.strftime("%H:%M:%S"),
                "recorded_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
                "provider": provider, "model": model,
                "prompt_tokens": prompt_tokens,
                "cached_prompt_tokens": cached_prompt_tokens,
                "completion_tokens": completion_tokens,
                "price_period": period,
                "cost_cny": round(cost, 6), "purpose": purpose,
            })
            self._persist_locked()
        return cost

    def estimate_call(self, model: str, input_chars: int,
                      max_output_tokens: int = 1024) -> float:
        return estimate_cost_cny(model, input_chars, max_output_tokens)

    def guard(self, model: str, input_chars: int,
              max_output_tokens: int = 1024) -> bool:
        if self.budget_cny is None:
            return True
        estimate = self.estimate_call(model, input_chars, max_output_tokens)
        with self.lock:
            over = self.total_cny() + estimate > self.budget_cny
            if over:
                self._rejected += 1
                self._persist_locked()
            return not over

    def total_cny(self) -> float:
        return round(sum(entry["cost_cny"] for entry in self.entries), 6)

    def to_dict(self) -> Dict[str, Any]:
        with self.lock:
            total = round(sum(entry["cost_cny"] for entry in self.entries), 6)
            providers: Dict[str, Dict[str, Any]] = {}
            models: Dict[str, Dict[str, Any]] = {}
            for entry in self.entries:
                for bucket, name in ((providers, entry.get("provider") or "unknown"),
                                     (models, entry.get("model") or "unknown")):
                    summary = bucket.setdefault(name, {
                        "name": name, "calls": 0, "cost_cny": 0.0,
                        "prompt_tokens": 0, "cached_prompt_tokens": 0,
                        "completion_tokens": 0,
                    })
                    summary["calls"] += 1
                    summary["cost_cny"] += entry["cost_cny"]
                    summary["prompt_tokens"] += entry["prompt_tokens"]
                    summary["cached_prompt_tokens"] += entry["cached_prompt_tokens"]
                    summary["completion_tokens"] += entry["completion_tokens"]
            for summary in list(providers.values()) + list(models.values()):
                summary["cost_cny"] = round(summary["cost_cny"], 6)
            return {
                "currency": "CNY", "total_cny": total,
                "entries": list(reversed(self.entries[-50:]),), "calls": len(self.entries),
                "budget_cny": self.budget_cny,
                "budget_remaining": (round(self.budget_cny - total, 6)
                                     if self.budget_cny is not None else None),
                "budget_usage_ratio": (round(total / self.budget_cny, 4)
                                       if self.budget_cny not in (None, 0) else None),
                "rejected": self._rejected,
                "providers": sorted(providers.values(), key=lambda item: item["calls"], reverse=True),
                "models": sorted(models.values(), key=lambda item: item["calls"], reverse=True),
            }

    def summary_str(self) -> str:
        data = self.to_dict()
        budget = (f" / 预算 ¥{data['budget_cny']:.2f}"
                  if data["budget_cny"] is not None else "")
        return f"LLM 成本 ¥{data['total_cny']:.4f}{budget}（{data['calls']} 次调用）"


def format_cny(value: float) -> str:
    """格式化人民币金额：小额保留 4 位，大额保留 2 位。"""
    return f"¥{value:.4f}" if abs(value) < 0.01 else f"¥{value:.2f}"

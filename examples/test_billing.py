"""计费模块测试（纯本地计算，无网络）。"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core import CostTracker, estimate_cost_cny, format_cny, price_for
from agent.core.billing import estimate_tokens, pricing_period


def expect(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def main() -> None:
    peak = datetime(2026, 8, 22, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    off_peak = datetime(2026, 8, 22, 13, tzinfo=ZoneInfo("Asia/Shanghai"))
    print("== 用例 1：价格表 ==")
    p = price_for("deepseek-v4-flash", peak)
    expect("V4-Flash 高峰输入 miss ¥3.00", p["input_miss"] == 3.00)
    expect("V4-Flash 高峰输出 ¥9.00", p["output"] == 9.00)
    expect("V4-Pro 空闲输入 ¥4.50", price_for("deepseek-v4-pro", off_peak)["input_miss"] == 4.50)
    expect("旧名映射到 Flash", price_for("deepseek-chat", peak)["input_miss"] == 3.00)
    expect("未知模型返回 None", price_for("gpt-x") is None)
    expect("高峰时段识别", pricing_period(peak) == "peak")
    expect("空闲时段识别", pricing_period(off_peak) == "off_peak")

    print("== 用例 2：token 估算 ==")
    expect("英文 4 字符/token", estimate_tokens("abcd" * 10) >= 10)
    expect("中文按字估算", estimate_tokens("中文" * 100) >= 100)

    print("== 用例 3：成本估算 ==")
    # 100 万字符 ≈ 25 万 tokens；按当前时段 cache miss 保守估算。
    est = estimate_cost_cny("deepseek-v4-flash", input_chars=1_000_000, at=peak)
    expect("高峰估算 ¥0.75", abs(est - 0.75) < 1e-9)

    print("== 用例 4：CostTracker 记录 ==")
    t = CostTracker()
    cost = t.record("deepseek", "deepseek-v4-flash", 100_000, 50_000,
                    purpose="摘要")
    expect("成本计算正确", cost > 0)
    expect("总额累计", abs(t.total_cny() - cost) < 1e-6)
    expect("调用次数", t.to_dict()["calls"] == 1)
    expect("条目含用途", t.to_dict()["entries"][0]["purpose"] == "摘要")
    expect("按服务商汇总", t.to_dict()["providers"][0]["name"] == "deepseek")
    expect("按模型汇总", t.to_dict()["models"][0]["calls"] == 1)

    print("== 用例 5：预算守卫 ==")
    t2 = CostTracker(budget_cny=0.01)
    # 一次 10 万字符调用估计远超 ¥0.01
    expect("超预算拒绝", t2.guard("deepseek-v4-flash", 1_000_000) is False)
    expect("拒绝计数", t2.to_dict()["rejected"] == 1)
    # 小调用不超
    expect("小调用放行", t2.guard("deepseek-v4-flash", 100) is True)
    # 无限预算
    t3 = CostTracker(budget_cny=None)
    expect("无限预算放行", t3.guard("deepseek-v4-flash", 10 ** 9) is True)

    print("== 用例 6：会话记录清空 ==")
    expect("清空返回调用数", t.clear() == 1)
    expect("清空后调用为零", t.to_dict()["calls"] == 0)

    print("== 用例 7：预算动态调整 ==")
    t2.set_budget(None)
    expect("取消预算后放行", t2.guard("deepseek-v4-flash", 1_000_000) is True)

    print("== 用例 8：成本账本持久化 ==")
    ledger = Path(tempfile.mkdtemp()) / "cost_ledger.json"
    persistent = CostTracker(storage_path=ledger)
    persistent.set_budget(2.5)
    persistent.record("deepseek", "deepseek-v4-flash", 2000, 1000,
                      purpose="MCP 测试")
    restored = CostTracker(storage_path=ledger)
    expect("账本自动落盘", ledger.is_file())
    expect("重新加载调用记录", restored.to_dict()["calls"] == 1)
    expect("重新加载预算", restored.to_dict()["budget_cny"] == 2.5)
    expect("账本不含 API Key", "api_key" not in ledger.read_text(encoding="utf-8"))

    print("== 用例 9：金额格式化 ==")
    expect("小金额 4 位", format_cny(0.0012) == "¥0.0012")
    expect("大金额 2 位", format_cny(3.4) == "¥3.40")

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

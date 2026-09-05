"""第五阶段-1:服务商模板市场测试。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core.provider_profiles import (
    DEFAULT_PROVIDER_PROFILES, PROVIDER_GROUPS, default_provider_profiles,
    providers_by_region,
)

cn_or_self = {"cn", "self"}


def expect(name, cond, got=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + ("" if cond else f" -> {got}"))
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def main() -> None:
    print("== 用例 1：第五阶段 5 个国内服务商全部注册 ==")
    expected_new = {"siliconflow", "zhipu", "dashscope", "volcengine", "oneapi"}
    ids = {p["id"] for p in DEFAULT_PROVIDER_PROFILES}
    expect("5 个新预设全部存在", expected_new.issubset(ids), expected_new - ids)
    expect("总预设数 ≥ 9(原 4 + 新 5)", len(DEFAULT_PROVIDER_PROFILES) >= 9,
           len(DEFAULT_PROVIDER_PROFILES))

    print("== 用例 2：每个新预设携带 region/tier/tags ==")
    for pid in expected_new:
        profile = next(p for p in DEFAULT_PROVIDER_PROFILES if p["id"] == pid)
        expect(f"{pid} region 在 {cn_or_self} 之一",
               profile.get("region") in cn_or_self, profile.get("region"))
        expect(f"{pid} 有 base_url 且以 http 开头",
               str(profile.get("base_url", "")).startswith("http"),
               profile.get("base_url"))
        expect(f"{pid} api_key_env 符合变量名规则",
               bool(profile.get("api_key_env"))
               and profile["api_key_env"].isupper()
               and profile["api_key_env"].replace("_", "").isalnum(),
               profile.get("api_key_env"))
        expect(f"{pid} 有非空 tags", isinstance(profile.get("tags"), list)
               and len(profile["tags"]) >= 2, profile.get("tags"))
        if profile.get("models"):
            expect(f"{pid} 模型列表非空(用于默认体验)",
                   len(profile["models"]) >= 2, len(profile.get("models", [])))

    print("== 用例 3：PROVIDER_GROUPS 4 组 ==")
    group_ids = {g["id"] for g in PROVIDER_GROUPS}
    expect("分组含 cn/intl/local/self",
           group_ids == {"cn", "intl", "local", "self"}, group_ids)

    print("== 用例 4：providers_by_region 分布 ==")
    by = providers_by_region()
    expect("cn 组含 5 个(DeepSeek + 4 个新预设)",
           len(by.get("cn", [])) == 5, len(by.get("cn", [])))
    expect("intl 组含 2 个(OpenAI / OpenRouter)",
           len(by.get("intl", [])) == 2, len(by.get("intl", [])))
    expect("local 组含 1 个(Ollama)",
           len(by.get("local", [])) == 1, len(by.get("local", [])))
    expect("self 组含 1 个(OneAPI)",
           len(by.get("self", [])) == 1, len(by.get("self", [])))

    print("== 用例 5：default_provider_profiles 是深拷贝 ==")
    a = default_provider_profiles()
    b = default_provider_profiles()
    a[0]["models"].append("__test_marker__")
    expect("修改 a 不影响 b(深拷贝)",
           "__test_marker__" not in b[0]["models"])

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

"""双模式 LLM（Ollama / DeepSeek）测试（模拟探测与响应，无真实网络）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.core.llm as llm_mod
from agent.core import CostTracker
from agent.core.llm import LLMClient, LLMError


def expect(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def main() -> None:
    print("== 用例 1：强制 Ollama ==")
    llm_mod.ollama_reachable = lambda *a, **k: True
    c = LLMClient(provider="ollama")
    expect("provider 为 ollama", c.provider == "ollama")
    expect("端点含 /v1", c.base_url.endswith("/v1"))
    expect("无 Key 也可用", c.available is True)
    expect("默认模型", c.model  # 允许默认或配置值
           in ("gemma4:e4b", llm_mod.config.get("OLLAMA_MODEL")))
    st = c.status()
    expect("状态含本地零 API 成本说明", "零 API 成本" in st["reason"])

    print("== 用例 2：强制 DeepSeek ==")
    c = LLMClient(provider="deepseek", api_key="sk-test")
    expect("provider 为 deepseek", c.provider == "deepseek")
    expect("有 Key 可用", c.available is True)
    expect("状态含模型", c.status()["model"] == c.model)
    expect("状态含价格表", "price" in c.status())

    print("== 用例 3：无 Key DeepSeek 不可用 ==")
    c = LLMClient(provider="deepseek", api_key="")
    expect("不可用", c.available is False)
    expect("提示未配置", "未配置" in c.status()["reason"])

    print("== 用例 4：auto 模式探测顺序 ==")
    llm_mod.ollama_reachable = lambda *a, **k: True
    c = LLMClient(provider="auto")
    expect("auto+Ollama可达 → ollama", c.provider == "ollama")

    llm_mod.ollama_reachable = lambda *a, **k: False
    c = LLMClient(provider="auto", api_key="sk-x")
    expect("auto+Ollama不可达+有Key → deepseek", c.provider == "deepseek")
    expect("已选择 DeepSeek 模型", c.model.startswith("deepseek-"))

    print("== 用例 5：chat 记录真实 usage ==")
    llm_mod.ollama_reachable = lambda *a, **k: False
    tracker = CostTracker()
    c = LLMClient(provider="deepseek", api_key="sk-x",
                  cost_tracker=tracker, model="deepseek-v4-flash")

    def fake_post(payload):
        return {"choices": [{"message": {"content": " 好的 "}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}
    c._post = fake_post
    out = c.chat("hi", purpose="测试")
    expect("返回文本", out == "好的")
    d = tracker.to_dict()
    expect("记录 1 次调用", d["calls"] == 1)
    expect("输入 tokens 记录", d["entries"][0]["prompt_tokens"] == 1000)
    expect("用途标签", d["entries"][0]["purpose"] == "测试")
    # 1000*0.14/1M + 500*0.28/1M = 0.00014 + 0.00014 = 0.00028
    expect("成本为人民币", d["total_cny"] > 0)

    print("== 用例 5a：空 JSON 输出自动重试 ==")
    replies = iter([
        {"choices": [{"message": {"content": ""}}],
         "usage": {"prompt_tokens": 10, "completion_tokens": 0}},
        {"choices": [{"message": {"content": '{"ok": true}'}}],
         "usage": {"prompt_tokens": 10, "completion_tokens": 4}},
    ])
    attempts = []

    def retry_post(payload):
        attempts.append(payload)
        return next(replies)

    c._post = retry_post
    expect("重试后返回 JSON", c.chat("hi", json_mode=True) == '{"ok": true}')
    expect("空输出后重试一次", len(attempts) == 2)

    print("== 用例 5b：连续空 JSON 给出清晰错误 ==")
    c._post = lambda payload: {
        "choices": [{"message": {"content": ""}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 0}}
    try:
        c.chat("hi", json_mode=True)
        expect("连续空输出应抛错", False)
    except Exception as err:  # noqa: BLE001
        expect("错误说明重试次数", "连续 3 次" in str(err))

    print("== 用例 5c：Ollama 不计云端费用 ==")
    local_tracker = CostTracker()
    llm_mod.ollama_reachable = lambda *a, **k: True
    local = LLMClient(provider="ollama", cost_tracker=local_tracker)
    local._post = fake_post
    local.chat("hi", purpose="本地测试")
    expect("本地调用成本为零", local_tracker.to_dict()["total_cny"] == 0)

    print("== 用例 6：预算拦截 ==")
    tracker2 = CostTracker(budget_cny=0.0001)
    c2 = LLMClient(provider="deepseek", api_key="sk-x",
                   cost_tracker=tracker2, model="deepseek-v4-flash")
    c2._post = fake_post
    try:
        c2.chat("x" * 100_000, purpose="大调用")
        expect("应被拦截", False)
    except Exception as err:  # noqa: BLE001
        expect("超预算抛出", "预算" in str(err))
    expect("拒绝计数", tracker2.to_dict()["rejected"] == 1)

    print("== 用例 7：Ollama 模型列表（模拟）==")
    c3 = LLMClient(provider="ollama")

    def fake_tags():
        return {"models": [{"name": "qwen2.5:7b", "size": 4.7e9},
                           {"name": "llama3:8b", "size": 5.0e9}]}
    import requests
    orig_get = requests.get
    requests.get = lambda url, **kw: type("R", (), {
        "status_code": 200,
        "json": lambda self: fake_tags(),
        "raise_for_status": lambda self: None})()
    try:
        models = c3.list_ollama_models()
        expect("列出 2 个模型", len(models) == 2)
        expect("含大小", abs(models[0]["size_gb"] - 4.7) < 0.1)
    finally:
        requests.get = orig_get

    print("== 用例 8：自定义 OpenAI 服务商不读 .env 的 Key ==")
    # 模拟 Web 自定义 profile（如 DashScope）：api_key_env 为空时，即便 .env
    # 配了 DeepSeek 的 LLM_API_KEY，也不应误用到该 profile 上（避免 401）。
    custom = LLMClient(provider="custom-xyz", provider_type="openai",
                       provider_name="自定义", requires_api_key=True,
                       model="qwen3-max", api_key="")
    expect("api_key 不读 .env", custom.api_key == "")
    expect("无 Key 不可用", custom.available is False)
    expect("状态提示未配置", "未配置" in custom.status()["reason"])
    custom2 = LLMClient(provider="custom-xyz", provider_type="openai",
                        requires_api_key=True, model="qwen3-max",
                        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                        api_key="sk-real")
    expect("填 Key 后可用", custom2.available is True)
    expect("endpoint 正确",
           custom2._endpoint
           == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")

    print("== 用例 9：云端自动重试、故障切换与真实探测 ==")
    trace = []
    primary = LLMClient(provider="primary", provider_type="openai",
                        model="primary-model", base_url="https://primary.example/v1",
                        api_key="sk-primary", event_callback=trace.append)
    backup = LLMClient(provider="backup", provider_type="openai",
                       model="backup-model", base_url="https://backup.example/v1",
                       api_key="sk-backup", event_callback=trace.append)
    primary._post = lambda payload: (_ for _ in ()).throw(
        LLMError("LLM 请求失败: HTTP 503 temporary"))
    backup._post = lambda payload: {
        "choices": [{"message": {"content": "fallback ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    primary.set_failovers([backup])
    expect("503 后自动切换备用服务商",
           primary.chat("hi", purpose="故障切换") == "fallback ok")
    expect("轨迹记录故障切换", any(item["kind"] == "failover"
                                 for item in trace))

    import requests
    original_post, original_get = requests.post, requests.get
    post_calls = []

    class FakeResponse:
        def __init__(self, status, data=None, text=""):
            self.status_code = status
            self._data = data or {}
            self.text = text

        def json(self):
            return self._data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"HTTP {self.status_code}")

    try:
        responses = iter([
            FakeResponse(503, text="busy"),
            FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]}),
        ])
        requests.post = lambda *args, **kwargs: (post_calls.append(kwargs) or
                                                  next(responses))
        original_sleep = llm_mod.time.sleep
        llm_mod.time.sleep = lambda _: None
        retry_client = LLMClient(
            provider="retry", provider_type="openai", model="retry-model",
            base_url="https://retry.example/v1", api_key="sk-retry",
            event_callback=trace.append)
        expect("503 请求在同一服务商自动重试",
               retry_client._post({"model": "retry-model", "messages": []})[
                   "choices"][0]["message"]["content"] == "ok" and
               len(post_calls) == 2)
        expect("轨迹记录重试", any(item["kind"] == "retry" for item in trace))

        probe_calls = []
        requests.post = lambda *args, **kwargs: (
            probe_calls.append((args, kwargs)) or FakeResponse(
                200, {"choices": [{"message": {"content": "OK"}}]}))
        probe = retry_client.probe(verify_model=True)
        expect("显式真实探测返回验证状态",
               probe["checked"] is True and probe["verified"] is True and
               probe["stages"][-1]["id"] == "model")
        expect("探测真实调用当前模型且最多 1 token",
               probe_calls[0][1]["json"]["model"] == "retry-model" and
               probe_calls[0][1]["json"]["max_tokens"] == 1)

        requests.post = lambda *args, **kwargs: FakeResponse(401)
        denied = retry_client.probe(verify_model=True)
        expect("真实探测返回可诊断的凭据错误",
               denied["verified"] is False and "API Key" in denied["reason"])
    finally:
        requests.post, requests.get = original_post, original_get
        llm_mod.time.sleep = original_sleep

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

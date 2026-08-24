"""轻量 LLM 客户端（双模式：DeepSeek / Ollama，OpenAI 兼容协议）。

模式选择（LLM_PROVIDER=auto|deepseek|ollama）：
- auto（默认）：Ollama 本地可达 → 用 Ollama（零成本）；否则 DeepSeek（需 Key）
- deepseek：强制 DeepSeek（OpenAI 兼容端点）
- ollama：强制本地 Ollama（无需 Key）

成本控制：每次调用前做预算预检，调用后记录真实 usage 到 CostTracker。
"""

from __future__ import annotations

import time
import json
from typing import Any, Dict, List, Optional

import requests

from . import config
from .billing import CostTracker, price_for
from .json_utils import parse_json_block

#: Ollama 可达性探测缓存（避免每次构造都探测）
_OLLAMA_PROBE: Dict[str, Any] = {"ok": None, "at": 0.0}
_OLLAMA_PROBE_TTL = 30.0  # 秒


def ollama_reachable(base_url: Optional[str] = None,
                     timeout: float = 1.5) -> bool:
    """探测本地 Ollama 是否可达（带 TTL 缓存）。"""
    base = (base_url or config.get("OLLAMA_BASE_URL")
            or "http://localhost:11434").rstrip("/")
    now = time.time()
    if (base != _OLLAMA_PROBE.get("base")
            or _OLLAMA_PROBE.get("at", 0) + _OLLAMA_PROBE_TTL < now):
        try:
            resp = requests.get(f"{base}/api/tags", timeout=timeout)
            _OLLAMA_PROBE["ok"] = resp.status_code == 200
        except requests.RequestException:
            _OLLAMA_PROBE["ok"] = False
        _OLLAMA_PROBE["at"] = now
        _OLLAMA_PROBE["base"] = base
    return bool(_OLLAMA_PROBE["ok"])


def detect_provider() -> Optional[str]:
    """解析当前生效的 provider：'deepseek' | 'ollama' | None。"""
    pref = (config.get("LLM_PROVIDER") or "auto").strip().lower()
    if pref == "deepseek":
        return "deepseek" if config.get("LLM_API_KEY") else None
    if pref == "ollama":
        return "ollama" if ollama_reachable() else None
    # auto：Ollama 优先（零成本），否则 DeepSeek
    if ollama_reachable():
        return "ollama"
    if config.get("LLM_API_KEY"):
        return "deepseek"
    return None


class LLMError(RuntimeError):
    """LLM 调用失败。"""


class LLMClient:
    """极简 OpenAI 兼容 Chat Completions 客户端（双 provider）。"""

    def __init__(self, api_key: Optional[str] = None,
                 base_url: Optional[str] = None,
                 model: Optional[str] = None,
                 timeout: Optional[int] = None,
                 provider: Optional[str] = None,
                 cost_tracker: Optional[CostTracker] = None,
                 budget_cny: Optional[float] = None) -> None:
        # ---- provider 解析 ----
        self.provider = (provider or config.get("LLM_PROVIDER")
                         or "auto").strip().lower()
        if self.provider == "auto":
            self.provider = detect_provider() or "deepseek"

        if self.provider == "ollama":
            self.api_key = ""
            self.base_url = (base_url or config.get("OLLAMA_BASE_URL")
                             or "http://localhost:11434").rstrip("/")
            # Ollama 的 OpenAI 兼容端点
            if not self.base_url.endswith("/v1"):
                self.base_url = f"{self.base_url}/v1"
            # 避免界面切换 provider 后把 DeepSeek 模型名带到 Ollama。
            local_model = model if not (model or "").startswith("deepseek-") else None
            self.model = (local_model or config.get("OLLAMA_MODEL")
                          or "gemma4:e4b")
        else:  # deepseek
            # 空字符串是一个有意义的显式值：可用于临时禁用 .env 中的
            # Key（测试、配置检查与桌面端切换时均需要此行为）。
            self.api_key = (api_key if api_key is not None
                            else config.get("LLM_API_KEY") or "")
            self.base_url = (base_url or config.get("LLM_BASE_URL")
                             or "https://api.deepseek.com").rstrip("/")
            if not self.base_url.endswith("/v1"):
                self.base_url = f"{self.base_url}/v1"
            # 同理，云端 provider 不接受本地模型标签。
            cloud_model = model if (model or "").startswith("deepseek-") else None
            configured_cloud = config.get("DEEPSEEK_MODEL")
            legacy_cloud = config.get("LLM_MODEL")
            # LLM_MODEL 曾是单 provider 配置；只接受明确的 DeepSeek 名称，
            # 避免用户的本地模型（如 gemma / qwen）误发往云端 API。
            if not (configured_cloud or "").startswith("deepseek-"):
                configured_cloud = None
            if not (legacy_cloud or "").startswith("deepseek-"):
                legacy_cloud = None
            self.model = (cloud_model or configured_cloud or legacy_cloud
                          or "deepseek-v4-flash")

        self.timeout = timeout or config.get_int("LLM_TIMEOUT", 90)

        # ---- 成本追踪 ----
        if cost_tracker is not None:
            self.cost_tracker = cost_tracker
        elif budget_cny is not None:
            self.cost_tracker = CostTracker(budget_cny=budget_cny,
                                            model_hint=self.model)
        else:
            self.cost_tracker = None

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """是否已具备可用的凭据。"""
        if self.provider == "ollama":
            return ollama_reachable(self.base_url.rstrip("/v1"))
        return bool(self.api_key)

    def status(self) -> Dict[str, Any]:
        """当前运行状态（供 UI 展示）。"""
        if self.provider == "ollama":
            return {
                "provider": "ollama",
                "model": self.model,
                "endpoint": self.base_url,
                "available": self.available,
                "reason": "本地 Ollama（零成本）",
                # 模型列表通过独立 /api/models 按需读取，避免每次 UI 刷新
                # 都阻塞在本地服务探测上。
                "models": [],
            }
        return {
            "provider": "deepseek",
            "model": self.model,
            "endpoint": self.base_url,
            "available": self.available,
            "reason": ("DeepSeek API"
                       + ("（按量计费）" if self.api_key else "（未配置 API Key）")),
            "price": price_for(self.model),
        }

    def list_ollama_models(self) -> List[Dict[str, Any]]:
        """列出本地 Ollama 已拉取的模型。"""
        if self.provider != "ollama":
            return []
        try:
            resp = requests.get(
                f"{self.base_url.rstrip('/v1')}/api/tags", timeout=3)
            resp.raise_for_status()
            return [{"name": m.get("name"), "size_gb": round(
                (m.get("size") or 0) / 1e9, 1)}
                for m in resp.json().get("models", [])]
        except requests.RequestException:
            return []

    # ------------------------------------------------------------------
    @property
    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def chat(self, user: str, system: Optional[str] = None,
             temperature: float = 0.0, json_mode: bool = False,
             max_tokens: Optional[int] = None,
             purpose: str = "") -> str:
        """发送一轮对话，返回助手回复文本。

        Args:
            purpose: 调用用途标签（用于成本追踪展示）。
        """
        if not self.available:
            raise LLMError(self._unavailable_message())

        # 不传 max_tokens 时仍使用受控上限；否则规划器这类轻量调用会
        # 绕过预算预检，并可能生成远超预期的输出。
        output_limit = max_tokens or config.get_int("LLM_MAX_OUTPUT_TOKENS", 2048)

        # Ollama 在本地推理，不消耗云端预算；DeepSeek 按 cache-miss
        # （最贵输入）+ 最大输出量预检，宁可提前阻止也不允许超预算。
        if self.provider == "deepseek" and self.cost_tracker is not None:
            est_chars = len(user) + len(system or "")
            if not self.cost_tracker.guard(self.model, est_chars,
                                           output_limit):
                raise LLMError(
                    f"预算超限已拦截本次调用（purpose={purpose or '未知'}）。"
                    f"当前成本 ¥{self.cost_tracker.total_cny():.4f}，"
                    f"预算 ¥{self.cost_tracker.budget_cny:.2f}。"
                    "请提高预算或改用本地 Ollama。")

        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        payload["max_tokens"] = output_limit

        # DeepSeek 偶发会以 HTTP 200 返回空 content，或忽略 JSON 模式。
        # 结构化调用最多额外重试两次；每次响应仍会照实记入成本。
        attempts = 3 if json_mode else 1
        last_error = ""
        for attempt in range(attempts):
            # 重试同样可能产生费用，必须重新通过预算守卫。
            if (attempt and self.provider == "deepseek"
                    and self.cost_tracker is not None
                    and not self.cost_tracker.guard(self.model, est_chars,
                                                   output_limit)):
                raise LLMError("结构化输出重试因预算上限被拦截。")
            request_payload = dict(payload)
            if attempt:
                retry_note = (
                    "\n\n上一次输出为空或不是合法 JSON。请只返回一个完整的 JSON 对象，"
                    "不要包含解释、Markdown 或代码围栏。")
                request_payload["messages"] = list(messages) + [{
                    "role": "user", "content": retry_note}]

            data = self._post(request_payload)
            try:
                content = data["choices"][0]["message"]["content"]
                text = str(content or "").strip()
            except (KeyError, IndexError, AttributeError) as err:
                raise LLMError(f"LLM 响应格式异常: {data}") from err

            # 无论内容是否可用，都要记录服务商已实际执行的调用。
            if self.cost_tracker is not None:
                usage = data.get("usage") or {}
                self.cost_tracker.record(
                    provider=self.provider,
                    model=self.model,
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    cached_prompt_tokens=int(
                        usage.get("prompt_cache_hit_tokens")
                        or usage.get("cached_tokens") or 0),
                    purpose=purpose)

            if not json_mode:
                return text
            try:
                parse_json_block(text)
                return text
            except (ValueError, json.JSONDecodeError) as err:
                last_error = str(err)

        raise LLMError(
            f"模型连续 {attempts} 次未返回可用 JSON（{last_error or '空内容'}）。"
            "已停止重试，本阶段将使用降级结果。")

    def _unavailable_message(self) -> str:
        if self.provider == "ollama":
            return ("Ollama 不可达。请先启动本地 Ollama"
                    "（ollama serve），并确认已拉取模型，如："
                    "ollama pull gemma4:e4b")
        return ("未配置 LLM：请在项目根目录 .env 设置 LLM_API_KEY，"
                "或改用本地 Ollama（LLM_PROVIDER=ollama）。")

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # json_mode 某些端点不支持：首次失败（400/422）则去掉重试一次
        for attempt in range(2):
            try:
                resp = requests.post(
                    self._endpoint, json=payload, headers=headers,
                    timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.json()
                # 400/422 且当前带 response_format → 去掉重试
                if (attempt == 0 and resp.status_code in (400, 422)
                        and "response_format" in payload):
                    payload.pop("response_format", None)
                    continue
                resp.raise_for_status()
            except requests.RequestException as err:
                raise LLMError(f"LLM 请求失败: {err}") from err
        raise LLMError("LLM 请求失败（未知错误）")

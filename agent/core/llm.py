"""Lightweight OpenAI-compatible LLM client used by Paper Studio.

模式选择（LLM_PROVIDER=auto|deepseek|ollama）：
- auto（默认）：Ollama 本地可达 → 用 Ollama（零成本）；否则 DeepSeek（需 Key）
- deepseek：强制 DeepSeek（OpenAI 兼容端点）
- ollama：强制本地 Ollama（无需 Key）

Besides the legacy DeepSeek/Ollama modes, ``provider_type`` allows the Web and
desktop apps to route the same research workflow through user-defined provider
profiles. DeepSeek cost accounting remains available internally for backwards
compatibility, but provider selection is no longer hard-coded to two vendors.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

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
    """Minimal OpenAI-compatible Chat Completions client."""

    def __init__(self, api_key: Optional[str] = None,
                 base_url: Optional[str] = None,
                 model: Optional[str] = None,
                 timeout: Optional[int] = None,
                 provider: Optional[str] = None,
                 provider_type: Optional[str] = None,
                 provider_name: Optional[str] = None,
                 requires_api_key: Optional[bool] = None,
                 cost_tracker: Optional[CostTracker] = None,
                 budget_cny: Optional[float] = None,
                 event_callback: Optional[Any] = None) -> None:
        # ---- provider 解析 ----
        # provider 是服务商标识（deepseek / ollama / custom-xxx），仅用于计费
        # 语义与 .env 兜底；运行时行为只按 provider_type（协议）区分，不再为
        # deepseek 维护独立分支，确保它与自定义服务商行为一致。
        self.provider = (provider or config.get("LLM_PROVIDER")
                         or "auto").strip().lower()
        if self.provider == "auto":
            self.provider = detect_provider() or "deepseek"
        self.provider_type = (provider_type or (
            "ollama" if self.provider == "ollama" else "openai")).strip().lower()
        if self.provider_type not in {"ollama", "openai"}:
            self.provider_type = "openai"
        self.provider_name = (provider_name or self.provider).strip()
        self.requires_api_key = (self.provider_type != "ollama"
                                 if requires_api_key is None
                                 else bool(requires_api_key))

        if self.provider_type == "ollama":
            self.api_key = ""
            self.base_url = self._normalize_base_url(
                base_url or config.get("OLLAMA_BASE_URL")
                or "http://localhost:11434", force_v1=True)
            # 避免界面切换 provider 后把云端模型名带到本地 Ollama。
            chosen = model if not (model or "").startswith("deepseek-") else None
            self.model = (chosen or config.get("OLLAMA_MODEL") or "gemma4:e4b")
        else:
            # OpenAI 兼容协议：DeepSeek / OpenAI / OpenRouter / 自定义服务商
            # 一视同仁。base_url、model、api_key 一律以显式参数为准；仅当未提
            # 供且 provider 为内置 deepseek 时，才回退到 .env 兜底。自定义服务
            # 商绝不读 .env 的 LLM_API_KEY，避免用错误 Key 误发请求（401）。
            if api_key is not None:
                self.api_key = api_key
            elif self.provider == "deepseek":
                self.api_key = config.get("LLM_API_KEY") or ""
            else:
                self.api_key = ""
            if base_url:
                self.base_url = self._normalize_base_url(base_url)
            elif self.provider == "deepseek":
                self.base_url = self._normalize_base_url(
                    config.get("LLM_BASE_URL") or "https://api.deepseek.com")
            else:
                self.base_url = ""
            if model:
                self.model = model
            elif self.provider == "deepseek":
                # 仅 CLI/.env 兜底场景：拒绝把本地模型名误发往云端 API。
                raw = (config.get("DEEPSEEK_MODEL") or config.get("LLM_MODEL")
                       or "deepseek-v4-flash")
                self.model = (raw if raw.startswith("deepseek-")
                              else "deepseek-v4-flash")
            else:
                self.model = config.get("LLM_MODEL") or ""

        self.timeout = timeout or config.get_int("LLM_TIMEOUT", 90)
        # The Web/App runner supplies a small, JSON-safe observer here.  It is
        # deliberately opt-in so CLI/library callers retain their old API.
        # Never put credentials in an event; only request metadata and text
        # that the user already asked the research task to process are kept.
        self.event_callback = event_callback
        self._fallback_clients: List["LLMClient"] = []

        # ---- 成本追踪 ----
        if cost_tracker is not None:
            self.cost_tracker = cost_tracker
        elif budget_cny is not None:
            self.cost_tracker = CostTracker(budget_cny=budget_cny,
                                            model_hint=self.model)
        else:
            self.cost_tracker = None

    def set_failovers(self, clients: List["LLMClient"]) -> None:
        """Configure compatible cloud fallbacks for transient service faults.

        A fallback is only attempted for retryable network/rate-limit/server
        failures.  Authentication and malformed-request errors stay visible
        to the user instead of silently routing a request elsewhere.
        """
        self._fallback_clients = [
            client for client in clients
            if client is not self and client.available
        ]

    def _trace(self, kind: str, title: str, detail: str = "",
               data: Optional[Dict[str, Any]] = None) -> None:
        if self.event_callback is None:
            return
        try:
            self.event_callback({
                "kind": kind,
                "title": title,
                "detail": detail,
                "data": data or {},
            })
        except Exception:
            # Observability must never make an otherwise usable model fail.
            pass

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """Whether the profile has enough configuration to make a request."""
        if self.provider_type == "ollama":
            root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
            return bool(self.model) and ollama_reachable(root)
        return bool(self.base_url and self.model
                    and (self.api_key or not self.requires_api_key))

    def status(self) -> Dict[str, Any]:
        """当前运行状态（供 UI 展示）。"""
        if self.provider_type == "ollama":
            return {
                "provider": self.provider,
                "provider_name": self.provider_name,
                "provider_type": self.provider_type,
                "model": self.model,
                "endpoint": self.base_url,
                "available": self.available,
                "reason": f"{self.provider_name} 本地服务（零 API 成本）",
                # 模型列表通过独立 /api/models 按需读取，避免每次 UI 刷新
                # 都阻塞在本地服务探测上。
                "models": [],
            }
        status = {
            "provider": self.provider,
            "provider_name": self.provider_name,
            "provider_type": self.provider_type,
            "model": self.model,
            "endpoint": self.base_url,
            "available": self.available,
            "reason": (f"{self.provider_name} · 配置就绪" if self.available
                       else f"{self.provider_name} · "
                       + ("尚未选择模型" if not self.model
                          else "未配置 API Key" if self.requires_api_key
                          else "配置不完整")),
        }
        if self.provider == "deepseek":
            status["price"] = price_for(self.model)
        return status

    def probe(self, *, verify_model: bool = False) -> Dict[str, Any]:
        """Run one bounded, real inference request against this exact model.

        A successful ``GET /models`` only proves that an endpoint can list
        models.  It does *not* prove that a key can invoke the selected model
        (or that the model is available to the account).  The explicit UI
        action therefore always sends a tiny non-streaming Chat Completions
        request.  It uses at most one output token and is never called by the
        background status polling path.

        ``verify_model`` remains for backwards-compatible callers.  The
        method is deliberately an actual model check in either mode.
        """
        del verify_model  # Real verification is the contract of this method.
        base = self.status()
        stages: List[Dict[str, str]] = []
        if not self.model:
            base.update({
                "available": False, "checked": True, "verified": False,
                "reason": f"{self.provider_name} 尚未选择模型。",
                "stages": [{"id": "configuration", "label": "配置检查",
                            "state": "error", "detail": "请填写要测试的模型名称。"}],
            })
            return base
        if not self.base_url:
            base.update({
                "available": False, "checked": True, "verified": False,
                "reason": f"{self.provider_name} 未配置 API 地址。",
                "stages": [{"id": "configuration", "label": "配置检查",
                            "state": "error", "detail": "请填写 API Base URL。"}],
            })
            return base
        if self.requires_api_key and not self.api_key:
            base.update({
                "available": False, "checked": True, "verified": False,
                "reason": f"{self.provider_name} 尚未配置 API Key。",
                "stages": [{"id": "configuration", "label": "凭据检查",
                            "state": "error", "detail": "此服务需要 API Key。"}],
            })
            return base

        stages.append({"id": "configuration", "label": "配置检查",
                       "state": "success", "detail": "地址、凭据和目标模型已就绪。"})
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        started = time.monotonic()
        try:
            # This is intentionally a direct request, rather than ``chat()``:
            # connection verification must not enter research traces or cost
            # accounting, while still exercising the identical model endpoint.
            response = requests.post(
                self._endpoint,
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                    "max_tokens": 1,
                    "temperature": 0,
                    "stream": False,
                },
                headers=headers,
                timeout=self.timeout,
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            if not 200 <= status_code < 300:
                raise LLMError(self._probe_http_error(status_code))
            try:
                data = response.json()
            except ValueError as err:
                raise LLMError("模型接口返回了非 JSON 响应，请确认接口协议。") from err
            choices = data.get("choices") if isinstance(data, dict) else None
            if not isinstance(choices, list) or not choices:
                raise LLMError(
                    "模型接口未返回 Chat Completions 响应，请确认 API 地址和协议。")
            stages.extend([
                {"id": "endpoint", "label": "推理端点",
                 "state": "success", "detail": "已建立认证请求。"},
                {"id": "model", "label": "模型推理",
                 "state": "success", "detail": f"{self.model} 已返回响应。"},
            ])
            base.update({
                "available": True, "checked": True, "verified": True,
                "reason": f"{self.provider_name} 的 {self.model} 已通过真实推理验证。",
                "stages": stages,
            })
        except LLMError as err:
            stages.append({"id": "model", "label": "模型推理",
                           "state": "error", "detail": str(err)})
            base.update({"available": False, "checked": True,
                         "verified": False,
                         "reason": f"{self.provider_name} 实际检测失败：{err}",
                         "stages": stages})
        except requests.Timeout:
            detail = (f"请求超过 {self.timeout} 秒仍未完成。可在“运行与下载”中"
                      "提高模型请求超时后重试。")
            stages.append({"id": "model", "label": "模型推理",
                           "state": "error", "detail": detail})
            base.update({"available": False, "checked": True,
                         "verified": False,
                         "reason": f"{self.provider_name} 实际检测超时。",
                         "stages": stages})
        except requests.RequestException as err:
            detail = self._probe_network_error(err)
            stages.append({"id": "endpoint", "label": "推理端点",
                           "state": "error", "detail": detail})
            base.update({"available": False, "checked": True,
                         "verified": False,
                         "reason": f"{self.provider_name} 实际检测失败：{detail}",
                         "stages": stages})
        base["latency_ms"] = int((time.monotonic() - started) * 1000)
        return base

    @staticmethod
    def _probe_http_error(status_code: int) -> str:
        """Map a transport status to a concise, actionable check result."""
        reasons = {
            400: "请求被服务商拒绝，请检查 API 地址、模型名称或接口协议。",
            401: "API Key 无效、已过期或未被服务商识别。",
            403: "当前 API Key 没有调用该模型的权限。",
            404: "未找到推理端点或模型，请检查 Base URL 与模型名称。",
            408: "服务商请求超时，请稍后重试或提高超时设置。",
            429: "服务商限流或余额不足，请稍后重试并检查账户状态。",
            500: "服务商内部错误，请稍后重试。",
            502: "服务商网关暂不可用，请稍后重试。",
            503: "服务商暂不可用，请稍后重试。",
            504: "服务商网关超时，请稍后重试或提高超时设置。",
        }
        return reasons.get(status_code, f"服务商返回 HTTP {status_code}。")

    @staticmethod
    def _probe_network_error(error: requests.RequestException) -> str:
        """Keep diagnostics useful without exposing request credentials."""
        if isinstance(error, requests.ConnectionError):
            return "无法连接到服务地址，请检查网络、地址和本地服务状态。"
        return f"网络请求失败：{type(error).__name__}。"

    def embedding(self, text: str, *, model: Optional[str] = None) -> List[float]:
        """对单条文本计算 embedding,返回浮点向量。"""
        if not text:
            raise LLMError("embedding 输入不能为空")
        return self.embeddings([text], model=model)[0]

    def embeddings(self, texts: List[str], *,
                   model: Optional[str] = None,
                   batch_size: int = 32) -> List[List[float]]:
        """批量计算 embedding。

        按协议分批:Ollama 逐条调用 ``/api/embeddings``;OpenAI 兼容端点一次
        传入多条并按 ``batch_size`` 切片。失败抛 :class:`LLMError`。
        """
        if not self.available:
            raise LLMError(self._unavailable_message())
        if not texts:
            return []
        target = model or self.model
        if not target:
            raise LLMError("尚未选择 embedding 模型")

        if self.provider_type == "ollama":
            root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
            url = f"{root}/api/embeddings"
            headers = {"Content-Type": "application/json"}
            vectors: List[List[float]] = []
            for index, prompt in enumerate(texts):
                try:
                    resp = requests.post(
                        url, json={"model": target, "prompt": prompt},
                        headers=headers, timeout=self.timeout)
                    if resp.status_code != 200:
                        raise LLMError(self._probe_http_error(resp.status_code))
                    data = resp.json()
                    vector = data.get("embedding")
                    if not isinstance(vector, list):
                        raise LLMError("Ollama embedding 响应缺少 embedding 字段")
                    vectors.append([float(value) for value in vector])
                except requests.RequestException as err:
                    raise LLMError(f"Ollama embedding 请求失败: {err}") from err
            return vectors

        # OpenAI 兼容端点
        url = f"{self.base_url}/embeddings"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        vectors_by_index: Dict[int, List[float]] = {}
        for start in range(0, len(texts), batch_size):
            chunk = texts[start:start + batch_size]
            payload = {"input": chunk, "model": target}
            try:
                resp = requests.post(
                    url, json=payload, headers=headers, timeout=self.timeout)
                if resp.status_code != 200:
                    raise LLMError(self._probe_http_error(resp.status_code))
                rows = resp.json().get("data") or []
                if not isinstance(rows, list) or len(rows) != len(chunk):
                    raise LLMError("embedding 响应数量与请求不一致")
                for offset, row in enumerate(rows):
                    vector = row.get("embedding") if isinstance(row, dict) else None
                    if not isinstance(vector, list):
                        raise LLMError("embedding 响应缺少 embedding 字段")
                    vectors_by_index[start + offset] = [
                        float(value) for value in vector]
            except requests.RequestException as err:
                raise LLMError(f"embedding 请求失败: {err}") from err
        return [vectors_by_index[i] for i in range(len(texts))]

    def list_ollama_models(self) -> List[Dict[str, Any]]:
        """列出本地 Ollama 已拉取的模型。"""
        if self.provider_type != "ollama":
            return []
        try:
            root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
            resp = requests.get(
                f"{root}/api/tags", timeout=3)
            resp.raise_for_status()
            return [{"name": m.get("name"), "size_gb": round(
                (m.get("size") or 0) / 1e9, 1)}
                for m in resp.json().get("models", [])]
        except requests.RequestException:
            return []

    def list_models(self) -> List[Dict[str, Any]]:
        """Discover models from Ollama or a standard ``GET /models`` API."""
        if self.provider_type == "ollama":
            return self.list_ollama_models()
        if self.requires_api_key and not self.api_key:
            return []
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = requests.get(
                f"{self.base_url}/models", headers=headers,
                timeout=min(10, self.timeout))
            response.raise_for_status()
            data = response.json().get("data", [])
            names = sorted({str(item.get("id") or "").strip()
                            for item in data if isinstance(item, dict)
                            and item.get("id")})
            return [{"name": name} for name in names[:500]]
        except (requests.RequestException, ValueError, AttributeError):
            return []

    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_base_url(url: str, *, force_v1: bool = False) -> str:
        """归一化 OpenAI 兼容 base_url：去尾斜杠；裸官方域名补 /v1。

        - 已带路径段（/v1、/compatible-mode/v1、/api/v1 等）原样保留，兼容
          私有网关或非标准版本段，不再盲目追加 /v1；
        - 仅对无路径的裸域名（如 https://api.deepseek.com）补 /v1；
        - force_v1 用于 Ollama：其 OpenAI 兼容端点固定为 {root}/v1。
        """
        raw = str(url or "").strip()
        if not raw:
            return ""
        raw = raw.rstrip("/")
        if raw.endswith("/chat/completions"):
            return raw
        path = urlparse(raw).path
        if force_v1:
            return raw if path.endswith("/v1") else f"{raw}/v1"
        if not path or path == "/":
            return f"{raw}/v1"
        return raw

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

        self._trace(
            "model_input", "模型输入",
            f"{self.provider_name} · {self.model} · {purpose or '通用推理'}",
            {"provider": self.provider, "provider_name": self.provider_name,
             "model": self.model, "purpose": purpose, "json_mode": json_mode,
             "max_tokens": max_tokens,
             "system": (system or "")[:6000], "input": user[:12000],
             "input_truncated": len(user) > 12000 or len(system or "") > 6000},
        )

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

            try:
                data = self._post(request_payload)
            except LLMError as err:
                fallback = self._next_failover(str(err))
                if fallback is not None:
                    return fallback.chat(
                        user=user, system=system, temperature=temperature,
                        json_mode=json_mode, max_tokens=max_tokens,
                        purpose=purpose)
                self._trace("model_failure", "模型请求失败", str(err), {
                    "provider": self.provider, "model": self.model,
                    "purpose": purpose,
                })
                raise
            try:
                content = data["choices"][0]["message"]["content"]
                text = str(content or "").strip()
            except (KeyError, IndexError, AttributeError) as err:
                raise LLMError(f"LLM 响应格式异常: {data}") from err

            # 无论内容是否可用，都要记录服务商已实际执行的调用。
            if (self.cost_tracker is not None
                    and self.provider in {"deepseek", "ollama"}):
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
                self._trace("model_output", "模型输出", "已收到模型响应", {
                    "provider": self.provider, "model": self.model,
                    "purpose": purpose, "output": text[:16000],
                    "output_truncated": len(text) > 16000,
                })
                return text
            try:
                parse_json_block(text)
                self._trace("model_output", "模型输出", "已收到结构化模型响应", {
                    "provider": self.provider, "model": self.model,
                    "purpose": purpose, "output": text[:16000],
                    "output_truncated": len(text) > 16000,
                })
                return text
            except (ValueError, json.JSONDecodeError) as err:
                last_error = str(err)
                if attempt + 1 < attempts:
                    self._trace("retry", "模型输出将重试", last_error, {
                        "provider": self.provider, "model": self.model,
                        "purpose": purpose, "attempt": attempt + 1,
                        "max_attempts": attempts, "reason": last_error,
                    })

        message = (f"模型连续 {attempts} 次未返回可用 JSON（{last_error or '空内容'}）。"
                   "已停止重试，本阶段将使用降级结果。")
        self._trace("model_failure", "模型结构化输出失败", message, {
            "provider": self.provider, "model": self.model,
            "purpose": purpose, "attempts": attempts,
        })
        raise LLMError(message)

    def _next_failover(self, error: str) -> Optional["LLMClient"]:
        """Return one alternate provider only for transient service errors."""
        lower = error.lower()
        retryable = any(marker in lower for marker in (
            "timeout", "timed out", "connection", "temporar", "429",
            "500", "502", "503", "504", "rate limit", "网络"))
        if not retryable or not self._fallback_clients:
            return None
        fallback = self._fallback_clients.pop(0)
        fallback.set_failovers(self._fallback_clients)
        self._trace("failover", "服务商故障切换", (
            f"{self.provider_name} 暂不可用，已切换至 {fallback.provider_name}"
        ), {
            "from_provider": self.provider, "from_model": self.model,
            "to_provider": fallback.provider, "to_model": fallback.model,
            "reason": error[:600],
        })
        return fallback

    def _unavailable_message(self) -> str:
        if self.provider_type == "ollama":
            return ("Ollama 不可达。请先启动本地 Ollama"
                    "（ollama serve），并确认已拉取模型，如："
                    "ollama pull gemma4:e4b")
        if not self.model:
            return f"{self.provider_name} 尚未选择模型，请先在设置中添加模型。"
        if self.requires_api_key and not self.api_key:
            return f"{self.provider_name} 尚未配置 API Key。"
        return f"{self.provider_name} 配置不完整，请检查 API 地址和模型。"

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Some OpenAI-compatible gateways reject response_format.  Retry
        # without it once; transient transport/rate-limit/server errors get
        # up to two bounded exponential retries before failover is considered.
        request_payload = dict(payload)
        for format_attempt in range(2):
            for attempt in range(3):
                try:
                    resp = requests.post(
                        self._endpoint, json=request_payload, headers=headers,
                        timeout=self.timeout)
                    if resp.status_code == 200:
                        try:
                            return resp.json()
                        except ValueError as err:
                            raise LLMError("LLM 返回了非 JSON 响应") from err
                    if (format_attempt == 0 and resp.status_code in (400, 422)
                            and "response_format" in request_payload):
                        self._trace("retry", "兼容模式重试", (
                            f"服务商不支持 response_format（HTTP {resp.status_code}）"
                        ), {"provider": self.provider, "model": self.model,
                              "attempt": 1, "max_attempts": 1,
                              "reason": f"HTTP {resp.status_code}"})
                        request_payload.pop("response_format", None)
                        break
                    retryable = resp.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
                    if retryable and attempt < 2:
                        wait = round(0.4 * (2 ** attempt), 1)
                        self._trace("retry", "云端服务重试", (
                            f"HTTP {resp.status_code}，{wait:g} 秒后重试"
                        ), {"provider": self.provider, "model": self.model,
                              "attempt": attempt + 1, "max_attempts": 3,
                              "reason": f"HTTP {resp.status_code}",
                              "delay_seconds": wait})
                        time.sleep(wait)
                        continue
                    raise LLMError(f"LLM 请求失败: HTTP {resp.status_code} {resp.text[:500]}")
                except requests.RequestException as err:
                    if attempt < 2:
                        wait = round(0.4 * (2 ** attempt), 1)
                        self._trace("retry", "云端服务重试", (
                            f"{err.__class__.__name__}，{wait:g} 秒后重试"
                        ), {"provider": self.provider, "model": self.model,
                              "attempt": attempt + 1, "max_attempts": 3,
                              "reason": str(err)[:500], "delay_seconds": wait})
                        time.sleep(wait)
                        continue
                    raise LLMError(f"LLM 请求失败: {err}") from err
        raise LLMError("LLM 请求失败（未知错误）")

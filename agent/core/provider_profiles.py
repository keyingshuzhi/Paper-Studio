"""Model-provider profile definitions and validation for Paper Studio.

Profiles deliberately store only non-secret connection metadata. API keys are
held in memory by the Web backend and, in the desktop build, persisted through
Electron ``safeStorage``.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Dict, Iterable, List, Optional


_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_PROFILE_KINDS = frozenset({"ollama", "openai"})


DEFAULT_PROVIDER_PROFILES: List[Dict[str, Any]] = [
    {
        "id": "ollama", "name": "Ollama", "kind": "ollama",
        "base_url": "http://localhost:11434/v1",
        "models": ["gemma4:e4b"], "default_model": "gemma4:e4b",
        "requires_api_key": False, "api_key_env": "",
        "builtin": True, "accent": "cyan",
        "region": "local", "tier": "local",
    },
    {
        "id": "deepseek", "name": "DeepSeek", "kind": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "default_model": "deepseek-v4-flash",
        "requires_api_key": True, "api_key_env": "LLM_API_KEY",
        "builtin": True, "accent": "violet",
        "region": "cn", "tier": "direct",
    },
    {
        "id": "openai", "name": "OpenAI", "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "models": [], "default_model": "",
        "requires_api_key": True, "api_key_env": "OPENAI_API_KEY",
        "builtin": True, "accent": "green",
        "region": "intl", "tier": "direct",
    },
    {
        "id": "openrouter", "name": "OpenRouter", "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [], "default_model": "",
        "requires_api_key": True, "api_key_env": "OPENROUTER_API_KEY",
        "builtin": True, "accent": "amber",
        "region": "intl", "tier": "aggregator",
    },
    # ---- 国内服务商模板市场(第五阶段) ----
    {
        "id": "siliconflow", "name": "硅基流动 (SiliconFlow)", "kind": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": [
            "Qwen/Qwen2.5-72B-Instruct", "Qwen/Qwen2.5-32B-Instruct",
            "deepseek-ai/DeepSeek-V2.5", "meta-llama/Meta-Llama-3.1-70B-Instruct",
            "01-ai/Yi-1.5-34B-Chat",
        ],
        "default_model": "Qwen/Qwen2.5-72B-Instruct",
        "requires_api_key": True, "api_key_env": "SILICONFLOW_API_KEY",
        "builtin": True, "accent": "blue",
        "region": "cn", "tier": "direct",
        "tags": ["国产", "开源模型丰富", "OpenAI 兼容", "高并发"],
    },
    {
        "id": "zhipu", "name": "智谱 (Zhipu)", "kind": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-air", "glm-4-flash", "glm-4-long"],
        "default_model": "glm-4-flash",
        "requires_api_key": True, "api_key_env": "ZHIPU_API_KEY",
        "builtin": True, "accent": "rose",
        "region": "cn", "tier": "direct",
        "tags": ["国产", "GLM 系列", "长上下文", "OpenAI 兼容"],
    },
    {
        "id": "dashscope", "name": "阿里百炼 (DashScope)", "kind": "openai",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max",
                   "qwen-long", "qwen2.5-72b-instruct"],
        "default_model": "qwen-plus",
        "requires_api_key": True, "api_key_env": "DASHSCOPE_API_KEY",
        "builtin": True, "accent": "orange",
        "region": "cn", "tier": "direct",
        "tags": ["国产", "通义千问", "OpenAI 兼容", "企业级"],
    },
    {
        "id": "volcengine", "name": "火山方舟 (Volcengine Ark)", "kind": "openai",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": ["doubao-pro-32k", "doubao-lite-32k", "doubao-pro-128k",
                   "deepseek-r1", "deepseek-v3"],
        "default_model": "doubao-lite-32k",
        "requires_api_key": True, "api_key_env": "VOLCENGINE_API_KEY",
        "builtin": True, "accent": "magenta",
        "region": "cn", "tier": "direct",
        "tags": ["国产", "豆包/DeepSeek", "推理接入点", "OpenAI 兼容"],
    },
    {
        "id": "oneapi", "name": "OneAPI / OpenAI 兼容网关", "kind": "openai",
        "base_url": "http://localhost:3000/v1",
        "models": [], "default_model": "",
        "requires_api_key": True, "api_key_env": "ONEAPI_API_KEY",
        "builtin": True, "accent": "teal",
        "region": "self", "tier": "gateway",
        "tags": ["统一网关", "多模型聚合", "企业自部署", "需自行搭建"],
    },
]


# 服务商分组(供 UI 分类展示「国内/国际/本地/网关」)
PROVIDER_GROUPS: List[Dict[str, Any]] = [
    {"id": "cn", "name": "国内服务商", "icon": "🇨🇳",
     "blurb": "国内云厂商与模型平台,OpenAI 兼容协议,直接接入。"},
    {"id": "intl", "name": "国际服务商", "icon": "🌐",
     "blurb": "OpenAI 与 OpenRouter 等海外平台,需可访问海外网络。"},
    {"id": "local", "name": "本地服务", "icon": "💻",
     "blurb": "Ollama 等本地运行的大模型,无需 API Key。"},
    {"id": "self", "name": "自部署网关", "icon": "🛰️",
     "blurb": "OneAPI 等统一网关,聚合多种后端模型,自行搭建。"},
]


def providers_by_region() -> Dict[str, List[Dict[str, Any]]]:
    """按 region 分组的预设清单(浅拷贝,UI 可直接渲染)。"""
    groups: Dict[str, List[Dict[str, Any]]] = {
        "cn": [], "intl": [], "local": [], "self": [],
    }
    for profile in default_provider_profiles():
        region = profile.get("region") or "intl"
        groups.setdefault(region, []).append({
            "id": profile["id"], "name": profile["name"],
            "accent": profile.get("accent", "blue"),
            "tags": profile.get("tags", []),
            "default_model": profile.get("default_model", ""),
            "requires_api_key": profile.get("requires_api_key", True),
        })
    return groups


def default_provider_profiles() -> List[Dict[str, Any]]:
    """Return a mutable copy of the built-in profile catalog."""
    return deepcopy(DEFAULT_PROVIDER_PROFILES)


def _clean_models(values: Any) -> List[str]:
    if isinstance(values, str):
        values = values.splitlines()
    if not isinstance(values, list):
        values = []
    models: List[str] = []
    seen = set()
    for raw in values[:100]:
        model = str(raw or "").strip()
        key = model.casefold()
        if model and len(model) <= 200 and key not in seen:
            models.append(model)
            seen.add(key)
    return models


def _clean_profile(raw: Dict[str, Any], *, builtin: bool = False,
                   fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fallback = fallback or {}
    profile_id = str(raw.get("id") or fallback.get("id") or "").strip().lower()
    if not _PROFILE_ID.fullmatch(profile_id):
        raise ValueError("服务商 ID 只能包含小写字母、数字、下划线或连字符")
    name = str(raw.get("name") or fallback.get("name") or profile_id).strip()
    if not name or len(name) > 80:
        raise ValueError(f"服务商 {profile_id} 的名称必须为 1-80 个字符")
    kind = str(raw.get("kind") or fallback.get("kind") or "openai").strip().lower()
    if kind not in _PROFILE_KINDS:
        raise ValueError(f"服务商 {name} 仅支持 Ollama 或 OpenAI 兼容协议")
    base_url = str(raw.get("base_url") or fallback.get("base_url") or "").strip()
    if not base_url.startswith(("http://", "https://")) or len(base_url) > 500:
        raise ValueError(f"服务商 {name} 需要有效的 HTTP(S) API 地址")
    base_url = base_url.rstrip("/")
    models = _clean_models(raw.get("models", fallback.get("models", [])))
    default_model = str(
        raw.get("default_model") or fallback.get("default_model") or "").strip()
    if len(default_model) > 200:
        raise ValueError(f"服务商 {name} 的默认模型名称过长")
    if default_model and default_model.casefold() not in {
            item.casefold() for item in models}:
        models.insert(0, default_model)
    api_key_env = str(
        raw.get("api_key_env") or fallback.get("api_key_env") or "").strip()
    if api_key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", api_key_env):
        raise ValueError(f"服务商 {name} 的环境变量名无效")
    return {
        "id": profile_id,
        "name": name,
        "kind": kind,
        "base_url": base_url,
        "models": models,
        "default_model": default_model,
        "requires_api_key": bool(raw.get(
            "requires_api_key", fallback.get("requires_api_key", kind != "ollama"))),
        "api_key_env": api_key_env,
        "builtin": bool(builtin or fallback.get("builtin")),
        "accent": str(raw.get("accent") or fallback.get("accent") or "blue")[:20],
    }


def sanitize_provider_profiles(raw_profiles: Any) -> List[Dict[str, Any]]:
    """Validate profiles while ensuring every built-in preset remains present."""
    incoming: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw_profiles, list):
        for raw in raw_profiles[:24]:
            if not isinstance(raw, dict):
                continue
            profile_id = str(raw.get("id") or "").strip().lower()
            if profile_id and profile_id not in incoming:
                incoming[profile_id] = raw

    profiles: List[Dict[str, Any]] = []
    builtin_ids = set()
    for preset in DEFAULT_PROVIDER_PROFILES:
        builtin_ids.add(preset["id"])
        profiles.append(_clean_profile(
            incoming.pop(preset["id"], {}), builtin=True, fallback=preset))
    for profile_id, raw in incoming.items():
        if profile_id not in builtin_ids:
            profiles.append(_clean_profile(raw))
    return profiles


def profile_by_id(profiles: Iterable[Dict[str, Any]],
                  profile_id: str) -> Optional[Dict[str, Any]]:
    target = str(profile_id or "").strip().lower()
    return next((profile for profile in profiles
                 if profile.get("id") == target), None)


def persistent_profiles(profiles: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip runtime-only status fields before writing settings to disk."""
    allowed = {
        "id", "name", "kind", "base_url", "models", "default_model",
        "requires_api_key", "api_key_env", "builtin", "accent",
    }
    return [{key: deepcopy(value) for key, value in profile.items()
             if key in allowed} for profile in profiles]

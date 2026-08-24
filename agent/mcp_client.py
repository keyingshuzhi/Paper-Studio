"""Paper Studio 的外部 MCP Client 连接中心。

连接配置只保存环境变量的“名称引用”，不保存凭据值。stdio
传输直接传递 argv，不经过 shell。所有外部 Tool 调用均由 Web/App
权限令牌层在进入本模块前确认。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import urlparse

import anyio
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from .read_service import resolve_data_dir


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
_SERVER_ID = re.compile(r"^mcp-[a-z0-9][a-z0-9-]{0,47}-[0-9a-f]{8}$")
_CATEGORIES = frozenset({
    "literature", "knowledge", "filesystem", "institution", "custom",
})
_TRANSPORTS = frozenset({"stdio", "streamable_http"})
_PERMISSION_OPERATIONS = frozenset({"trust", "delete", "call_tool"})
_MAX_CONFIG_BYTES = 1024 * 1024
_MAX_RESULT_BYTES = 2 * 1024 * 1024


class MCPClientError(RuntimeError):
    """外部 MCP 连接、协议或权限错误。"""


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _one_line(error: BaseException) -> str:
    return " ".join(str(error).split())[:500] or type(error).__name__


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (text or "server")[:40].strip("-") or "server"


def _json_model(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        data = value.model_dump(mode="json", by_alias=True, exclude_none=True)
    elif isinstance(value, dict):
        data = value
    else:
        data = {"value": value}
    return data if isinstance(data, dict) else {"value": data}


def _bounded_payload(value: Dict[str, Any],
                     max_bytes: int = _MAX_RESULT_BYTES) -> Dict[str, Any]:
    """避免外部 Server 用超大文本或 blob 撑满 Web/App 内存。"""
    raw = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
    if len(raw) <= max_bytes:
        return value
    compact = dict(value)
    contents = compact.get("content") or compact.get("contents")
    if isinstance(contents, list):
        safe_items = []
        remaining = max(8_192, max_bytes - 8_192)
        for item in contents[:100]:
            if not isinstance(item, dict):
                continue
            clean = dict(item)
            if isinstance(clean.get("text"), str):
                text = clean["text"]
                encoded = text.encode("utf-8")
                if len(encoded) > remaining:
                    clean["text"] = encoded[:remaining].decode(
                        "utf-8", errors="ignore") + "\n…[已截断]"
                remaining = max(0, remaining - len(
                    str(clean.get("text") or "").encode("utf-8")))
            if "blob" in clean or "data" in clean:
                clean.pop("blob", None)
                clean.pop("data", None)
                clean["binary_omitted"] = True
            safe_items.append(clean)
            if remaining <= 0:
                break
        key = "content" if "content" in compact else "contents"
        compact[key] = safe_items
    compact["_paper_studio"] = {
        "truncated": True,
        "original_bytes": len(raw),
        "limit_bytes": max_bytes,
    }
    raw = json.dumps(compact, ensure_ascii=False, default=str).encode("utf-8")
    if len(raw) <= max_bytes:
        return compact
    return {
        "_paper_studio": {
            "truncated": True,
            "original_bytes": len(raw),
            "limit_bytes": max_bytes,
            "message": "外部 MCP 结果过大，未载入完整内容",
        }
    }


class MCPConnectionStore:
    """线程安全的 MCP 连接配置库。"""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path or (resolve_data_dir() / "mcp_connections.json"))
        self._lock = threading.RLock()
        self._servers: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        with self._lock:
            try:
                if self.path.stat().st_size > _MAX_CONFIG_BYTES:
                    return
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return
            raw_servers = data.get("servers") if isinstance(data, dict) else None
            if not isinstance(raw_servers, list):
                return
            for raw in raw_servers[:100]:
                if not isinstance(raw, dict):
                    continue
                try:
                    server = self._validate(raw, existing=raw)
                except (TypeError, ValueError):
                    continue
                self._servers[server["id"]] = server

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "version": 1,
            "servers": list(self._servers.values()),
        }, ensure_ascii=False, indent=2)
        if len(payload.encode("utf-8")) > _MAX_CONFIG_BYTES:
            raise ValueError("MCP 连接配置超过 1 MiB")
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _mapping(raw: Any, *, header: bool = False) -> Dict[str, str]:
        if raw in (None, {}):
            return {}
        if not isinstance(raw, dict) or len(raw) > 50:
            raise ValueError("环境映射必须是不超过 50 项的对象")
        clean: Dict[str, str] = {}
        for raw_key, raw_value in raw.items():
            key, value = str(raw_key).strip(), str(raw_value).strip()
            if header:
                if not _HEADER_NAME.fullmatch(key):
                    raise ValueError(f"无效的 HTTP Header 名称: {key}")
                if key.lower() in {"host", "content-length", "connection"}:
                    raise ValueError(f"不允许自定义 Header: {key}")
            elif not _ENV_NAME.fullmatch(key):
                raise ValueError(f"无效的子进程环境变量名: {key}")
            if not _ENV_NAME.fullmatch(value):
                raise ValueError(f"无效的宿主环境变量名: {value}")
            clean[key] = value
        return clean

    @classmethod
    def _validate(cls, raw: Dict[str, Any],
                  existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        name = str(raw.get("name") or "").strip()
        if not 1 <= len(name) <= 100:
            raise ValueError("连接名称必须为 1-100 个字符")
        category = str(raw.get("category") or "custom")
        if category not in _CATEGORIES:
            raise ValueError("不支持的 MCP 连接类型")
        transport = str(raw.get("transport") or "stdio")
        if transport not in _TRANSPORTS:
            raise ValueError("transport 仅支持 stdio 或 streamable_http")
        try:
            timeout = max(2, min(120, int(raw.get("timeout_seconds") or 20)))
        except (TypeError, ValueError) as err:
            raise ValueError("timeout_seconds 必须是整数") from err
        raw_permissions = raw.get("permissions") or {}
        if not isinstance(raw_permissions, dict):
            raise ValueError("permissions 必须是对象")
        permissions = {
            "resources_read": bool(raw_permissions.get("resources_read", True)),
            "tools_call": bool(raw_permissions.get("tools_call", False)),
        }
        args = raw.get("args") or []
        if not isinstance(args, list) or len(args) > 100:
            raise ValueError("args 必须是不超过 100 项的列表")
        args = [str(item) for item in args]
        if any("\x00" in item or len(item) > 4_000 for item in args):
            raise ValueError("args 包含无效参数")

        command, cwd, url = "", None, ""
        env_from: Dict[str, str] = {}
        headers_from: Dict[str, str] = {}
        if transport == "stdio":
            command = str(raw.get("command") or "").strip()
            if (not command or "\x00" in command or any(
                    char.isspace() for char in command)):
                raise ValueError("stdio command 必须是单个可执行文件名或路径")
            raw_cwd = str(raw.get("cwd") or "").strip()
            if raw_cwd:
                cwd_path = Path(raw_cwd).expanduser()
                if not cwd_path.is_absolute():
                    raise ValueError("stdio cwd 必须是绝对路径")
                cwd = str(cwd_path)
            env_from = cls._mapping(raw.get("env_from"))
        else:
            url = str(raw.get("url") or "").strip()
            parsed = urlparse(url)
            if (parsed.scheme not in {"http", "https"} or not parsed.netloc
                    or parsed.username or parsed.password or parsed.fragment
                    or parsed.query):
                raise ValueError(
                    "HTTP MCP URL 必须是不含账号、密钥、查询串或片段的 http(s) 地址")
            if (parsed.scheme == "http" and parsed.hostname not in {
                    "127.0.0.1", "localhost", "::1"}):
                raise ValueError(
                    "非本机 Streamable HTTP 连接必须使用 HTTPS")
            headers_from = cls._mapping(raw.get("headers_from"), header=True)

        raw_id = str(raw.get("id") or "")
        server_id = (raw_id if _SERVER_ID.fullmatch(raw_id)
                     else f"mcp-{_slug(name)}-{secrets.token_hex(4)}")
        previous = existing or {}
        sensitive_fields = (
            "transport", "url", "command", "args", "cwd", "env_from",
            "headers_from", "permissions",
        )
        candidate = {
            "transport": transport, "url": url, "command": command,
            "args": args, "cwd": cwd, "env_from": env_from,
            "headers_from": headers_from, "permissions": permissions,
        }
        changed = any(previous.get(key) != candidate.get(key)
                      for key in sensitive_fields)
        trusted = bool(previous.get("trusted")) and not changed
        return {
            "id": server_id,
            "name": name,
            "category": category,
            **candidate,
            "timeout_seconds": timeout,
            "trusted": trusted,
            "created_at": previous.get("created_at") or _now_text(),
            "updated_at": _now_text(),
            "last_connected_at": previous.get("last_connected_at"),
            "last_status": previous.get("last_status") or "never",
            "last_error": previous.get("last_error"),
            "last_summary": previous.get("last_summary") or {},
        }

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [json.loads(json.dumps(server, ensure_ascii=False))
                    for server in sorted(self._servers.values(),
                                         key=lambda item: item["name"].lower())]

    def get(self, server_id: str) -> Dict[str, Any]:
        with self._lock:
            server = self._servers.get(str(server_id or ""))
            if server is None:
                raise MCPClientError("MCP 连接不存在")
            return json.loads(json.dumps(server, ensure_ascii=False))

    def save(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            requested_id = str(raw.get("id") or "")
            existing = self._servers.get(requested_id)
            if requested_id and existing is None:
                raise ValueError("不能修改不存在的 MCP 连接")
            server = self._validate(raw, existing=existing)
            self._servers[server["id"]] = server
            self._persist()
            return self.get(server["id"])

    def set_trusted(self, server_id: str, trusted: bool = True) -> Dict[str, Any]:
        with self._lock:
            server = self._servers.get(server_id)
            if server is None:
                raise MCPClientError("MCP 连接不存在")
            server["trusted"] = bool(trusted)
            server["updated_at"] = _now_text()
            self._persist()
            return self.get(server_id)

    def record_status(self, server_id: str, *, ok: bool,
                      summary: Optional[Dict[str, Any]] = None,
                      error: Optional[str] = None) -> None:
        with self._lock:
            server = self._servers.get(server_id)
            if server is None:
                return
            server["last_status"] = "connected" if ok else "error"
            server["last_connected_at"] = _now_text() if ok else server.get(
                "last_connected_at")
            server["last_error"] = None if ok else str(error or "")[0:500]
            if summary is not None:
                server["last_summary"] = summary
            self._persist()

    def delete(self, server_id: str) -> bool:
        with self._lock:
            if server_id not in self._servers:
                return False
            del self._servers[server_id]
            self._persist()
            return True


class MCPPermissionBroker:
    """用于 Web/App 交互的短时、单次、目标绑定权限令牌。"""

    def __init__(self, ttl_seconds: int = 120) -> None:
        self.ttl_seconds = max(30, min(300, int(ttl_seconds)))
        self._lock = threading.Lock()
        self._challenges: Dict[str, Dict[str, Any]] = {}
        self._tokens: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _fingerprint(operation: str, server_id: str, target: str,
                     arguments: Optional[Dict[str, Any]] = None) -> str:
        payload = json.dumps({
            "operation": operation, "server_id": server_id,
            "target": target, "arguments": arguments or {},
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
           default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cleanup(self) -> None:
        now = time.time()
        for bucket in (self._challenges, self._tokens):
            for key in list(bucket):
                if float(bucket[key].get("expires_ts") or 0) <= now:
                    del bucket[key]

    def request(self, operation: str, server_id: str, target: str,
                *, server_name: str,
                arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if operation not in _PERMISSION_OPERATIONS:
            raise ValueError("不支持的 MCP Client 权限操作")
        labels = {
            "trust": (
                f"是否信任并连接外部 MCP Server「{server_name}」？"
                "stdio 连接会启动配置的本地程序，HTTP 连接会访问配置的网络地址。"),
            "delete": f"是否删除 MCP 连接配置「{server_name}」？该操作不会删除外部数据。",
            "call_tool": (
                f"是否允许「{server_name}」执行外部 Tool「{target}」？"
                f"参数字段：{', '.join(sorted((arguments or {}).keys())) or '无'}。"
                "外部 Tool 可能读写数据、访问网络或产生费用，具体取决于对方 Server。"),
        }
        challenge_id = secrets.token_urlsafe(24)
        expires_ts = time.time() + self.ttl_seconds
        record = {
            "operation": operation,
            "server_id": server_id,
            "target": target,
            "fingerprint": self._fingerprint(
                operation, server_id, target, arguments),
            "expires_ts": expires_ts,
        }
        with self._lock:
            self._cleanup()
            self._challenges[challenge_id] = record
        return {
            "challenge_id": challenge_id,
            "message": labels[operation],
            "expires_in_seconds": self.ttl_seconds,
        }

    def approve(self, challenge_id: str, approved: bool) -> Dict[str, Any]:
        with self._lock:
            self._cleanup()
            record = self._challenges.pop(str(challenge_id or ""), None)
            if record is None:
                raise MCPClientError("权限确认已过期或不存在")
            if not approved:
                return {"approved": False, "permission_token": None}
            token = secrets.token_urlsafe(32)
            self._tokens[token] = record
            return {
                "approved": True,
                "permission_token": token,
                "expires_in_seconds": max(
                    0, int(record["expires_ts"] - time.time())),
            }

    def consume(self, token: str, operation: str, server_id: str,
                target: str,
                arguments: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            self._cleanup()
            record = self._tokens.pop(str(token or ""), None)
        expected = self._fingerprint(operation, server_id, target, arguments)
        if record is None or not secrets.compare_digest(
                str(record.get("fingerprint") or ""), expected):
            raise MCPClientError("权限令牌无效、过期、已使用或与当前操作不匹配")


class MCPClientManager:
    """连接外部 MCP Server 并提供受限的发现、资源与 Tool 能力。"""

    def __init__(self, store: Optional[MCPConnectionStore] = None) -> None:
        self.store = store or MCPConnectionStore()

    @staticmethod
    def _missing_environment(server: Dict[str, Any]) -> List[str]:
        refs = list((server.get("env_from") or {}).values())
        refs += list((server.get("headers_from") or {}).values())
        return sorted({name for name in refs if not os.environ.get(name)})

    def public_server(self, server: Dict[str, Any]) -> Dict[str, Any]:
        public = json.loads(json.dumps(server, ensure_ascii=False))
        missing = self._missing_environment(server)
        public["environment_ready"] = not missing
        public["missing_environment"] = missing
        public["secret_storage"] = "environment_references_only"
        return public

    def list_servers(self) -> List[Dict[str, Any]]:
        return [self.public_server(server) for server in self.store.list()]

    def save_server(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.public_server(self.store.save(payload))

    def get_server(self, server_id: str) -> Dict[str, Any]:
        return self.store.get(server_id)

    def trust_server(self, server_id: str) -> Dict[str, Any]:
        return self.public_server(self.store.set_trusted(server_id, True))

    def delete_server(self, server_id: str) -> bool:
        return self.store.delete(server_id)

    @asynccontextmanager
    async def _client(self, server: Dict[str, Any]) -> AsyncIterator[Client]:
        if not server.get("trusted"):
            raise MCPClientError("该 MCP 连接尚未经用户信任确认")
        missing = self._missing_environment(server)
        if missing:
            raise MCPClientError(
                "缺少必需的宿主环境变量: " + ", ".join(missing))
        timeout = float(server.get("timeout_seconds") or 20)
        if server["transport"] == "stdio":
            child_env = {
                child: os.environ[parent]
                for child, parent in (server.get("env_from") or {}).items()
            }
            params = StdioServerParameters(
                command=server["command"],
                args=list(server.get("args") or []),
                env=child_env,
                cwd=server.get("cwd"),
            )
            async with Client(
                stdio_client(params), read_timeout_seconds=timeout,
                raise_exceptions=False, mode="auto",
            ) as client:
                yield client
            return

        import httpx2

        headers = {
            header: os.environ[parent]
            for header, parent in (server.get("headers_from") or {}).items()
        }
        async with httpx2.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        ) as http_client:
            transport = streamable_http_client(
                server["url"], http_client=http_client)
            async with Client(
                transport, read_timeout_seconds=timeout,
                raise_exceptions=False, mode="auto",
            ) as client:
                yield client

    @staticmethod
    async def _pages(client: Client, method_name: str,
                     item_name: str) -> List[Any]:
        cursor: Optional[str] = None
        items: List[Any] = []
        for _ in range(10):
            result = await getattr(client, method_name)(cursor=cursor)
            items.extend(list(getattr(result, item_name) or []))
            cursor = getattr(result, "next_cursor", None)
            if not cursor or len(items) >= 500:
                break
        return items[:500]

    async def discover(self, server_id: str) -> Dict[str, Any]:
        server = self.get_server(server_id)
        timeout = float(server.get("timeout_seconds") or 20)
        try:
            with anyio.fail_after(timeout + 5):
                async with self._client(server) as client:
                    tools, resources, templates, prompts = await asyncio.gather(
                        self._pages(client, "list_tools", "tools"),
                        self._pages(client, "list_resources", "resources"),
                        self._pages(client, "list_resource_templates",
                                    "resource_templates"),
                        self._pages(client, "list_prompts", "prompts"),
                    )
                    info = _json_model(client.server_info) if client.server_info else None
                    capabilities = _json_model(client.server_capabilities)
                    result = {
                        "server_id": server_id,
                        "protocol_version": client.protocol_version,
                        "server_info": info,
                        "instructions": client.instructions,
                        "capabilities": capabilities,
                        "tools": [_json_model(item) for item in tools],
                        "resources": [_json_model(item) for item in resources],
                        "resource_templates": [_json_model(item)
                                               for item in templates],
                        "prompts": [_json_model(item) for item in prompts],
                    }
            summary = {
                "tools": len(result["tools"]),
                "resources": len(result["resources"]),
                "resource_templates": len(result["resource_templates"]),
                "prompts": len(result["prompts"]),
                "protocol_version": result["protocol_version"],
            }
            self.store.record_status(server_id, ok=True, summary=summary)
            return _bounded_payload(result)
        except Exception as err:
            message = _one_line(err)
            self.store.record_status(server_id, ok=False, error=message)
            raise MCPClientError(f"无法连接 MCP Server：{message}") from err

    async def read_resource(self, server_id: str, uri: str) -> Dict[str, Any]:
        server = self.get_server(server_id)
        if not (server.get("permissions") or {}).get("resources_read"):
            raise MCPClientError("该连接未授权读取 Resources")
        uri = str(uri or "").strip()
        if not uri or len(uri) > 4_000:
            raise ValueError("无效的 Resource URI")
        try:
            with anyio.fail_after(float(server.get("timeout_seconds") or 20) + 5):
                async with self._client(server) as client:
                    result = await client.read_resource(uri)
            return _bounded_payload(_json_model(result))
        except Exception as err:
            if isinstance(err, (ValueError, MCPClientError)):
                raise
            raise MCPClientError(f"Resource 读取失败：{_one_line(err)}") from err

    async def get_prompt(self, server_id: str, prompt_name: str,
                         arguments: Optional[Dict[str, Any]] = None
                         ) -> Dict[str, Any]:
        """读取外部 Prompt，并验证名称和参数后返回标准协议结果。"""
        server = self.get_server(server_id)
        if not (server.get("permissions") or {}).get("resources_read"):
            raise MCPClientError("该连接未授权读取 Resources 与 Prompts")
        prompt_name = str(prompt_name or "").strip()
        if not prompt_name or len(prompt_name) > 200:
            raise ValueError("无效的 Prompt 名称")
        arguments = arguments or {}
        if not isinstance(arguments, dict):
            raise ValueError("arguments 必须是 JSON 对象")
        normalized = {str(key): str(value) for key, value in arguments.items()}
        if len(normalized) > 100 or len(json.dumps(
                normalized, ensure_ascii=False).encode("utf-8")) > _MAX_CONFIG_BYTES:
            raise ValueError("Prompt 参数超过限制")
        try:
            with anyio.fail_after(float(server.get("timeout_seconds") or 20) + 5):
                async with self._client(server) as client:
                    available = await client.list_prompts()
                    if prompt_name not in {
                            prompt.name for prompt in available.prompts}:
                        raise ValueError(f"外部 Prompt 不存在: {prompt_name}")
                    result = await client.get_prompt(
                        prompt_name, normalized or None)
            return _bounded_payload(_json_model(result))
        except Exception as err:
            if isinstance(err, (ValueError, MCPClientError)):
                raise
            raise MCPClientError(f"Prompt 获取失败：{_one_line(err)}") from err

    async def call_tool(self, server_id: str, tool_name: str,
                        arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        server = self.get_server(server_id)
        if not (server.get("permissions") or {}).get("tools_call"):
            raise MCPClientError("该连接未授权调用 Tools")
        tool_name = str(tool_name or "").strip()
        if not tool_name or len(tool_name) > 200:
            raise ValueError("无效的 Tool 名称")
        arguments = arguments or {}
        if not isinstance(arguments, dict):
            raise ValueError("arguments 必须是 JSON 对象")
        if len(json.dumps(arguments, ensure_ascii=False,
                          default=str).encode("utf-8")) > _MAX_CONFIG_BYTES:
            raise ValueError("Tool 参数超过 1 MiB")
        try:
            with anyio.fail_after(float(server.get("timeout_seconds") or 20) + 5):
                async with self._client(server) as client:
                    available = await client.list_tools()
                    if tool_name not in {tool.name for tool in available.tools}:
                        raise ValueError(f"外部 Tool 不存在: {tool_name}")
                    result = await client.call_tool(
                        tool_name, arguments,
                        read_timeout_seconds=float(
                            server.get("timeout_seconds") or 20))
            return _bounded_payload(_json_model(result))
        except Exception as err:
            if isinstance(err, (ValueError, MCPClientError)):
                raise
            raise MCPClientError(f"Tool 调用失败：{_one_line(err)}") from err


def run_async(awaitable):
    """在当前线程中运行短生命周期 MCP 会话。"""
    return asyncio.run(awaitable)


__all__ = [
    "MCPClientError", "MCPClientManager", "MCPConnectionStore",
    "MCPPermissionBroker", "run_async",
]

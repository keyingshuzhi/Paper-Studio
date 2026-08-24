"""Skill 契约、执行边界与延迟注册表。

现有业务可以继续直接调用 ``execute(**kwargs)`` 获取原始返回值；需要稳定契约的
MCP、插件和新代码应调用 ``invoke(**kwargs)``，它负责：

* 按 JSON Schema 校验输入和输出；
* 检查调用方授予的权限；
* 执行超时控制与协作式检查点；
* 发送结构化进度事件；
* 将成功或失败统一包装为 :class:`SkillResult`。
"""

from __future__ import annotations

import contextvars
import dataclasses
import inspect
import json
import queue
import re
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Type,
    TypeVar,
)


JsonSchema = Dict[str, Any]
ProgressCallback = Callable[["SkillProgress"], None]
_SkillT = TypeVar("_SkillT", bound="BaseSkill")


class SkillPermission(str, Enum):
    """Skill 可能使用的外部能力，用于宿主在执行前做授权。"""

    NETWORK = "network"
    FILESYSTEM_READ = "filesystem.read"
    FILESYSTEM_WRITE = "filesystem.write"
    SENSITIVE_DATA = "sensitive_data"
    PAID_API = "paid_api"
    EXTERNAL_WRITE = "external.write"
    DESTRUCTIVE = "destructive"


class SkillContractError(ValueError):
    """Skill 定义或 JSON Schema 数据违反契约。"""


class SkillTimeoutError(TimeoutError):
    """Skill 超时或在协作式检查点检测到超时。"""


class SkillInvocationError(RuntimeError):
    """``SkillResult.raise_for_error`` 使用的统一异常。"""

    def __init__(self, error: "SkillError") -> None:
        super().__init__(error.message)
        self.error = error


@dataclass(frozen=True)
class SkillProgress:
    """一次可序列化的 Skill 进度更新。"""

    skill: str
    percent: float
    message: str
    stage: Optional[str] = None
    current: Optional[int] = None
    total: Optional[int] = None
    timestamp: str = field(default_factory=lambda: _utc_now())
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _to_jsonable(dataclasses.asdict(self))


@dataclass(frozen=True)
class SkillError:
    """标准化错误，避免上层依赖不同 Skill 的异常类型。"""

    code: str
    message: str
    error_type: str
    retryable: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _to_jsonable(dataclasses.asdict(self))


@dataclass
class SkillResult:
    """标准 Skill 执行结果。``data`` 保留原始 Python 对象。"""

    ok: bool
    skill: str
    data: Any = None
    error: Optional[SkillError] = None
    warnings: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转为可直接写入 JSON 或通过 MCP 返回的字典。"""
        return {
            "ok": self.ok,
            "skill": self.skill,
            "data": _to_jsonable(self.data),
            "error": self.error.to_dict() if self.error else None,
            "warnings": list(self.warnings),
            "meta": _to_jsonable(self.meta),
        }

    def to_json(self, **kwargs: Any) -> str:
        options = {"ensure_ascii": False}
        options.update(kwargs)
        return json.dumps(self.to_dict(), **options)

    def raise_for_error(self) -> "SkillResult":
        if not self.ok and self.error is not None:
            raise SkillInvocationError(self.error)
        return self

    def unwrap(self) -> Any:
        self.raise_for_error()
        return self.data


@dataclass
class _ExecutionContext:
    skill: str
    callback: Optional[ProgressCallback]
    deadline: Optional[float]
    cancelled: threading.Event


_CURRENT_EXECUTION: contextvars.ContextVar[Optional[_ExecutionContext]] = (
    contextvars.ContextVar("skill_execution", default=None)
)


class BaseSkill(ABC):
    """所有原子技能的抽象基类。"""

    name: ClassVar[str] = "base"
    description: ClassVar[str] = ""
    version: ClassVar[str] = "1.0.0"
    input_schema: ClassVar[JsonSchema] = {
        "type": "object",
        "additionalProperties": True,
    }
    output_schema: ClassVar[JsonSchema] = {}
    permissions: ClassVar[FrozenSet[SkillPermission]] = frozenset()
    default_timeout_seconds: ClassVar[Optional[float]] = 60.0

    # 只注册类；实例在 get()/create() 时创建，避免模块导入产生隐式单例。
    _registry: ClassVar[Dict[str, Type["BaseSkill"]]] = {}
    _instances: ClassVar[Dict[str, "BaseSkill"]] = {}
    _registry_lock: ClassVar[threading.RLock] = threading.RLock()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # 未自行声明 name 的测试替身/实现变体继承父类能力，但不覆盖注册项。
        declared_name = cls.__dict__.get("name")
        if declared_name is None or declared_name == "base" or inspect.isabstract(cls):
            return
        if not isinstance(declared_name, str) or not re.fullmatch(
                r"[a-z][a-z0-9_.-]*", declared_name):
            raise SkillContractError(
                f"Skill name 必须是稳定的小写标识符: {declared_name!r}")
        with BaseSkill._registry_lock:
            existing = BaseSkill._registry.get(declared_name)
            if existing is not None and existing is not cls:
                raise SkillContractError(f"Skill name 重复: {declared_name}")
            BaseSkill._registry[declared_name] = cls

    @abstractmethod
    def execute(self, **kwargs: Any) -> Any:
        """原始执行入口，保留给现有内部调用。"""

    def invoke(
        self,
        *,
        timeout_seconds: Optional[float] = None,
        progress_callback: Optional[ProgressCallback] = None,
        allowed_permissions: Optional[Iterable[SkillPermission | str]] = None,
        validate: bool = True,
        **kwargs: Any,
    ) -> SkillResult:
        """在受控边界内执行技能并返回统一结果。

        ``allowed_permissions=None`` 表示宿主沿用旧行为并授予该 Skill 声明的权限；
        MCP 等外部宿主应显式传入本次调用允许的权限集合。

        Python 无法安全终止正在系统调用中的线程，因此超时会立即返回失败并发出
        协作式取消信号。Skill 应在循环中调用 :meth:`checkpoint`，网络请求自身也
        必须继续配置连接/读取超时。
        """
        started_wall = _utc_now()
        started = time.monotonic()
        request_id = uuid.uuid4().hex
        timeout = (self.default_timeout_seconds if timeout_seconds is None
                   else timeout_seconds)
        if timeout is not None:
            try:
                timeout = float(timeout)
            except (TypeError, ValueError):
                return self._failure(
                    "invalid_timeout", "timeout_seconds 必须是数字或 None",
                    SkillContractError, started, started_wall, request_id,
                    timeout_seconds=None)
            if timeout <= 0:
                return self._failure(
                    "invalid_timeout", "timeout_seconds 必须大于 0",
                    SkillContractError, started, started_wall, request_id,
                    timeout_seconds=timeout)

        missing = self._missing_permissions(allowed_permissions)
        if missing:
            return self._failure(
                "permission_denied",
                "缺少 Skill 执行权限: " + ", ".join(missing),
                PermissionError,
                started,
                started_wall,
                request_id,
                timeout_seconds=timeout,
                retryable=False,
                details={"required": sorted(p.value for p in self.permissions),
                         "missing": missing},
            )

        if validate:
            try:
                validate_json_schema(_to_jsonable(kwargs), self.input_schema)
            except SkillContractError as err:
                return self._failure(
                    "input_validation_error", str(err), type(err), started,
                    started_wall, request_id, timeout_seconds=timeout,
                    details={"schema": "input"})

        cancelled = threading.Event()
        context = _ExecutionContext(
            skill=self.name,
            callback=progress_callback,
            deadline=(started + timeout if timeout is not None else None),
            cancelled=cancelled,
        )
        self._emit_progress(context, 0, "开始执行", stage="start")

        result_queue: "queue.Queue[tuple[str, Any]]" = queue.Queue(maxsize=1)

        def worker() -> None:
            token = _CURRENT_EXECUTION.set(context)
            try:
                result_queue.put(("result", self.execute(**kwargs)))
            except BaseException as err:  # noqa: BLE001 - 必须跨线程传回原异常
                result_queue.put(("error", err))
            finally:
                _CURRENT_EXECUTION.reset(token)

        thread = threading.Thread(
            target=worker,
            name=f"skill-{self.name}-{request_id[:8]}",
            daemon=True,
        )
        thread.start()
        remaining = (max(0.0, context.deadline - time.monotonic())
                     if context.deadline is not None else None)
        thread.join(remaining)

        if thread.is_alive():
            cancelled.set()
            return self._failure(
                "timeout",
                f"技能执行超过 {timeout:g} 秒",
                SkillTimeoutError,
                started,
                started_wall,
                request_id,
                timeout_seconds=timeout,
                retryable=True,
            )

        try:
            state, payload = result_queue.get_nowait()
        except queue.Empty:
            return self._failure(
                "execution_error", "技能线程结束但没有返回结果",
                RuntimeError, started, started_wall, request_id,
                timeout_seconds=timeout)

        if state == "error":
            err = payload
            code = "timeout" if isinstance(err, (TimeoutError, SkillTimeoutError)) \
                else "execution_error"
            return self._failure(
                code, str(err) or err.__class__.__name__, err.__class__,
                started, started_wall, request_id, timeout_seconds=timeout,
                retryable=isinstance(err, (TimeoutError, SkillTimeoutError)))

        if validate:
            try:
                validate_json_schema(_to_jsonable(payload), self.output_schema)
            except SkillContractError as err:
                return self._failure(
                    "output_validation_error", str(err), type(err), started,
                    started_wall, request_id, timeout_seconds=timeout,
                    details={"schema": "output"})

        self._emit_progress(context, 100, "执行完成", stage="complete")
        return SkillResult(
            ok=True,
            skill=self.name,
            data=payload,
            meta=self._meta(started, started_wall, request_id, timeout),
        )

    def report_progress(
        self,
        percent: float,
        message: str,
        *,
        stage: Optional[str] = None,
        current: Optional[int] = None,
        total: Optional[int] = None,
        **meta: Any,
    ) -> None:
        """从 ``execute`` 内发送进度；直接调用 execute 时安全地无操作。"""
        context = _CURRENT_EXECUTION.get()
        if context is None:
            return
        self.checkpoint()
        self._emit_progress(context, percent, message, stage=stage,
                            current=current, total=total, meta=meta)

    def checkpoint(self) -> None:
        """循环或阶段边界的协作式超时检查点。"""
        context = _CURRENT_EXECUTION.get()
        if context is None:
            return
        if context.cancelled.is_set():
            raise SkillTimeoutError("技能已因超时取消")
        if context.deadline is not None and time.monotonic() >= context.deadline:
            context.cancelled.set()
            raise SkillTimeoutError("技能执行超时")

    @classmethod
    def manifest(cls) -> Dict[str, Any]:
        """返回可供 UI、MCP 和插件发现的能力清单。"""
        return {
            "name": cls.name,
            "description": cls.description,
            "version": cls.version,
            "input_schema": _to_jsonable(cls.input_schema),
            "output_schema": _to_jsonable(cls.output_schema),
            "permissions": sorted(p.value for p in cls.permissions),
            "timeout_seconds": cls.default_timeout_seconds,
        }

    @classmethod
    def create(cls: Type[_SkillT], name: str, **config: Any) -> _SkillT:
        """创建独立 Skill 实例，适合按任务注入配置。"""
        with BaseSkill._registry_lock:
            skill_type = BaseSkill._registry[name]
        return skill_type(**config)  # type: ignore[return-value]

    @classmethod
    def get(cls: Type[_SkillT], name: str) -> _SkillT:
        """兼容旧注册表接口，按名称获取延迟创建的共享实例。"""
        with BaseSkill._registry_lock:
            if name not in BaseSkill._instances:
                skill_type = BaseSkill._registry[name]
                BaseSkill._instances[name] = skill_type()
            return BaseSkill._instances[name]  # type: ignore[return-value]

    @classmethod
    def all(cls) -> Dict[str, "BaseSkill"]:
        """返回所有已注册技能的延迟单例副本。"""
        with BaseSkill._registry_lock:
            names = list(BaseSkill._registry)
        return {name: cls.get(name) for name in names}

    @classmethod
    def manifests(cls) -> Dict[str, Dict[str, Any]]:
        """无需实例化即可读取所有技能能力清单。"""
        with BaseSkill._registry_lock:
            items = list(BaseSkill._registry.items())
        return {name: skill_type.manifest() for name, skill_type in items}

    @classmethod
    def registered_types(cls) -> Dict[str, Type["BaseSkill"]]:
        with BaseSkill._registry_lock:
            return dict(BaseSkill._registry)

    @classmethod
    def has(cls, name: str) -> bool:
        with BaseSkill._registry_lock:
            return name in BaseSkill._registry

    def _missing_permissions(
        self,
        allowed: Optional[Iterable[SkillPermission | str]],
    ) -> List[str]:
        if allowed is None:
            return []
        values = {p.value if isinstance(p, SkillPermission) else str(p)
                  for p in allowed}
        return sorted(p.value for p in self.permissions if p.value not in values)

    def _failure(
        self,
        code: str,
        message: str,
        error_type: Type[BaseException],
        started: float,
        started_wall: str,
        request_id: str,
        *,
        timeout_seconds: Optional[float],
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ) -> SkillResult:
        return SkillResult(
            ok=False,
            skill=self.name,
            error=SkillError(
                code=code,
                message=message,
                error_type=error_type.__name__,
                retryable=retryable,
                details=details or {},
            ),
            meta=self._meta(started, started_wall, request_id,
                            timeout_seconds),
        )

    def _meta(
        self,
        started: float,
        started_wall: str,
        request_id: str,
        timeout_seconds: Optional[float],
    ) -> Dict[str, Any]:
        return {
            "request_id": request_id,
            "skill_version": self.version,
            "started_at": started_wall,
            "finished_at": _utc_now(),
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "timeout_seconds": timeout_seconds,
            "permissions": sorted(p.value for p in self.permissions),
        }

    @staticmethod
    def _emit_progress(
        context: _ExecutionContext,
        percent: float,
        message: str,
        *,
        stage: Optional[str] = None,
        current: Optional[int] = None,
        total: Optional[int] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        if context.callback is None or context.cancelled.is_set():
            return
        event = SkillProgress(
            skill=context.skill,
            percent=max(0.0, min(100.0, float(percent))),
            message=str(message),
            stage=stage,
            current=current,
            total=total,
            meta=meta or {},
        )
        try:
            context.callback(event)
        except Exception:
            # 展示层回调失败不能中断 Skill 主流程。
            return

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Skill:{self.name}@{self.version}>"


def validate_json_schema(value: Any, schema: Mapping[str, Any],
                         path: str = "$") -> None:
    """校验项目 Skill 使用的 JSON Schema 子集，无需引入额外依赖。

    支持 ``type / required / properties / additionalProperties / items / enum /
    min|max / minLength|maxLength / minItems|maxItems / pattern / anyOf``。
    Schema 本身仍是标准 JSON Schema，可以直接暴露给 MCP 客户端。
    """
    if not schema:
        return
    if "anyOf" in schema:
        for candidate in schema["anyOf"]:
            try:
                validate_json_schema(value, candidate, path)
                break
            except SkillContractError:
                continue
        else:
            raise SkillContractError(f"{path}: 不匹配 anyOf 中的任何结构")
        return

    expected = schema.get("type")
    if expected is not None:
        expected_types = [expected] if isinstance(expected, str) else list(expected)
        if not any(_matches_json_type(value, item) for item in expected_types):
            actual = _json_type_name(value)
            raise SkillContractError(
                f"{path}: 类型应为 {' | '.join(expected_types)}，实际为 {actual}")

    if "enum" in schema and value not in schema["enum"]:
        raise SkillContractError(f"{path}: 值必须是 {schema['enum']!r} 之一")

    if isinstance(value, Mapping):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise SkillContractError(f"{path}.{key}: 缺少必填字段")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                validate_json_schema(item, properties[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise SkillContractError(f"{path}.{key}: 不允许的字段")
            elif isinstance(schema.get("additionalProperties"), Mapping):
                validate_json_schema(item, schema["additionalProperties"],
                                     f"{path}.{key}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SkillContractError(f"{path}: 元素数量不能少于 {schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SkillContractError(f"{path}: 元素数量不能多于 {schema['maxItems']}")
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                validate_json_schema(item, items, f"{path}[{index}]")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SkillContractError(f"{path}: 长度不能小于 {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SkillContractError(f"{path}: 长度不能大于 {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise SkillContractError(f"{path}: 不匹配格式 {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SkillContractError(f"{path}: 不能小于 {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SkillContractError(f"{path}: 不能大于 {schema['maximum']}")


def _matches_json_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _json_type_name(value: Any) -> str:
    for name in ("null", "boolean", "integer", "number", "string",
                 "array", "object"):
        if _matches_json_type(value, name):
            return name
    return type(value).__name__


def _to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _to_jsonable(dataclasses.asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_to_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _to_jsonable(to_dict())
    return str(value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

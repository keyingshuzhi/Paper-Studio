"""插件基类。

插件（Plugin）是技能（Skill）的组合编排：
- 一个插件定义一条完整的业务流水线（如：搜索 → 去重 → 输出）。
- 插件内部调度多个技能，对外暴露统一的 run() 入口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict


class BasePlugin(ABC):
    """所有插件的抽象基类。"""

    name: ClassVar[str] = "base"
    description: ClassVar[str] = ""

    _registry: ClassVar[Dict[str, "BasePlugin"]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.name != "base" and cls.name not in cls._registry:
            cls._registry[cls.name] = cls()

    @abstractmethod
    def run(self, **kwargs: Any) -> Any:
        """执行插件流程。"""

    @classmethod
    def get(cls, name: str) -> "BasePlugin":
        return cls._registry[name]

    @classmethod
    def all(cls) -> Dict[str, "BasePlugin"]:
        return dict(cls._registry)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Plugin:{self.name}>"

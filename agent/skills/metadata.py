"""跨技能共享的结构化数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


PAPER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["title", "url", "source"],
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "url": {"type": "string"},
        "source": {"type": "string", "minLength": 1},
        "authors": {"type": "array", "items": {"type": "string"}},
        "year": {"type": ["integer", "null"]},
        "abstract": {"type": ["string", "null"]},
        "doi": {"type": ["string", "null"]},
        "pdf_url": {"type": ["string", "null"]},
        "venue": {"type": ["string", "null"]},
        "extra": {"type": "object", "additionalProperties": True},
    },
    "additionalProperties": False,
}


@dataclass
class Paper:
    """一条标准化后的学术文献记录。

    所有搜索技能（arXiv / Scholar 等）都必须返回本结构，
    这样上层（Plugins / MCP）无需关心来源差异，可统一处理。
    """

    title: str
    url: str
    source: str  # 来源标识：arxiv / semantic_scholar / crossref / webpage ...
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    abstract: Optional[str] = None
    doi: Optional[str] = None
    pdf_url: Optional[str] = None
    venue: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Paper":
        """从字典重建（研究记忆持久化的反序列化）。"""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Paper {self.year} [{self.source}] {self.title[:60]}>"

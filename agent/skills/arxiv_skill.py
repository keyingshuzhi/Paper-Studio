"""arXiv 搜索技能：通过官方 API 检索预印本论文。

端点：http://export.arxiv.org/api/query
无需 API Key，适合作为学术检索的第一优先级来源。
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import requests

from .base import BaseSkill, SkillPermission
from .metadata import PAPER_SCHEMA, Paper

# arXiv Atom 命名空间
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom"}

_API_BASE = "http://export.arxiv.org/api/query"


def _first_text(elem: Optional[ET.Element], tag: str) -> Optional[str]:
    """从 XML 元素中安全提取首个指定标签的文本。"""
    if elem is None:
        return None
    node = elem.find(tag, _ATOM_NS)
    return node.text.strip() if node is not None and node.text else None


def _clean_abstract(text: str) -> str:
    """去除摘要中的换行与多余空白。"""
    return re.sub(r"\s+", " ", text or "").strip()


class ArxivSkill(BaseSkill):
    """在 arXiv 上按关键词搜索论文。"""

    name = "arxiv_search"
    description = "通过 arXiv 官方 API 检索预印本论文，返回结构化 Paper 列表。"
    version = "1.1.0"
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "max_results": {"type": ["integer", "null"],
                            "minimum": 1, "maximum": 100},
            "categories": {"type": ["array", "null"],
                           "items": {"type": "string", "minLength": 1}},
            "sort_by": {"type": "string", "enum": [
                "relevance", "submittedDate", "updatedDate"]},
            # SearchManager 会把共享筛选条件透传到全部搜索源。
            "year_from": {"type": ["integer", "null"]},
        },
        "additionalProperties": True,
    }
    output_schema = {"type": "array", "items": PAPER_SCHEMA}
    permissions = frozenset({SkillPermission.NETWORK})
    default_timeout_seconds = 75.0

    def __init__(self, max_results: int = 10, timeout: int = 20,
                 retries: int = 2) -> None:
        self.max_results = max_results
        self.timeout = timeout
        self.retries = retries

    # ------------------------------------------------------------------
    def execute(self, query: str, max_results: Optional[int] = None,
                categories: Optional[List[str]] = None,
                sort_by: str = "relevance", **_: Any) -> List[Paper]:
        """执行 arXiv 搜索。

        Args:
            query: 搜索关键词（可含布尔表达式，如 'transformer AND attention'）。
            max_results: 返回结果上限，默认取构造参数。
            categories: arXiv 分类过滤，如 ['cs.AI', 'cs.LG']。
            sort_by: 'relevance' | 'submittedDate' | 'updatedDate'。
        """
        results = max_results or self.max_results
        search_query = self._build_query(query, categories)
        self.report_progress(15, "正在请求 arXiv", stage="request")

        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": results,
            "sortBy": sort_by,
        }

        xml_text = self._request(params)
        self.report_progress(75, "正在解析 arXiv 结果", stage="parse")
        papers = self._parse(xml_text)
        self.report_progress(95, f"已解析 {len(papers)} 篇文献", stage="parse")
        return papers

    # ------------------------------------------------------------------
    def _build_query(self, query: str,
                     categories: Optional[List[str]]) -> str:
        """构造 arXiv 查询表达式。"""
        # 去掉用户输入中可能存在的危险字符，只保留 alnum 与常见运算符
        safe = re.sub(r"[^\w\s:+\-*.\"()]", "", query).strip()
        if not safe:
            raise ValueError("查询关键词为空")
        q = f"all:{safe}"
        if categories:
            parts = [q] + [f"cat:{c}" for c in categories]
            q = " AND ".join(f"({p})" for p in parts)
        return q

    def _request(self, params: Dict[str, Any]) -> str:
        """带重试的 HTTP 请求。"""
        last_err: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                resp = requests.get(_API_BASE, params=params,
                                    timeout=self.timeout)
                resp.raise_for_status()
                return resp.text
            except requests.RequestException as err:  # noqa: PERF203
                last_err = err
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"arXiv API 请求失败: {last_err}")

    def _parse(self, xml_text: str) -> List[Paper]:
        """解析 arXiv Atom XML 为 Paper 列表。"""
        root = ET.fromstring(xml_text)
        papers: List[Paper] = []
        for entry in root.findall("a:entry", _ATOM_NS):
            title = _first_text(entry, "a:title") or ""
            summary = _clean_abstract(_first_text(entry, "a:summary") or "")
            published = _first_text(entry, "a:published") or ""
            year = int(published[:4]) if len(published) >= 4 else None

            authors = [a.text for a in entry.findall("a:author/a:name", _ATOM_NS)
                       if a.text]

            # 链接：alternate/pdf
            pdf_url = None
            for link in entry.findall("a:link", _ATOM_NS):
                if link.get("type") == "application/pdf":
                    pdf_url = link.get("href")
                    break

            doi = None
            doi_node = entry.find("arxiv:doi", _ATOM_NS)
            if doi_node is not None and doi_node.text:
                doi = doi_node.text

            papers.append(Paper(
                title=re.sub(r"\s+", " ", title).strip(),
                url=(entry.findtext("a:id", default="", namespaces=_ATOM_NS)),
                source=self.name,
                authors=authors,
                year=year,
                abstract=summary,
                doi=doi,
                pdf_url=pdf_url,
                venue="arXiv",
            ))
        return papers

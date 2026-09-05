"""学术检索技能：Semantic Scholar API 为主，Crossref 为降级备用。

Google Scholar 无官方开放 API 且反爬严格，因此 V1.0 采用两个
权威且免费可用的替代源：
1. Semantic Scholar Graph API（覆盖 arXiv / ACL / IEEE 等元数据）
2. Crossref REST API（DOI 注册机构，覆盖大量期刊）
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

import requests

from .base import BaseSkill, SkillPermission
from .metadata import PAPER_SCHEMA, Paper

_S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
_S2_FIELDS = "title,authors,year,abstract,externalIds,url,openAccessPdf,venue"
_CROSSREF_API = "https://api.crossref.org/works"


class ScholarSkill(BaseSkill):
    """多后端学术检索技能，统一输出 Paper 列表。"""

    name = "scholar_search"
    description = ("Semantic Scholar / Crossref 学术检索，"
                   "作为 arXiv 的补充来源（期刊、会议论文）。")
    version = "1.1.0"
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "max_results": {"type": ["integer", "null"],
                            "minimum": 1, "maximum": 100},
            "year_from": {"type": ["integer", "null"]},
        },
        "additionalProperties": True,
    }
    output_schema = {"type": "array", "items": PAPER_SCHEMA}
    permissions = frozenset({SkillPermission.NETWORK})
    default_timeout_seconds = 360.0

    def __init__(self, timeout: int = 30, retries: int = 4,
                 backends: Optional[List[str]] = None) -> None:
        self.timeout = timeout
        self.retries = retries
        #: 后端优先级：依次尝试，前面的失败则降级到后面的
        self.backends = backends or ["semantic_scholar", "crossref"]

    # ------------------------------------------------------------------
    def execute(self, query: str, max_results: Optional[int] = 10,
                year_from: Optional[int] = None, **_: Any) -> List[Paper]:
        """执行学术检索。

        Args:
            query: 搜索关键词。
            max_results: 返回结果上限。
            year_from: 只返回该年份及之后的论文。
        """
        papers: List[Paper] = []
        last_err: Optional[Exception] = None

        total_backends = len(self.backends)
        for index, backend in enumerate(self.backends, 1):
            self.report_progress(
                min(80, 10 + (index - 1) * 60 / max(total_backends, 1)),
                f"正在检索 {backend}", stage="request",
                current=index, total=total_backends)
            try:
                if backend == "semantic_scholar":
                    papers = self._search_s2(query, max_results or 10)
                elif backend == "crossref":
                    papers = self._search_crossref(query, max_results or 10)
                else:
                    continue
                if papers:  # 后端有结果即采用
                    break
            except (requests.RequestException, RuntimeError,
                    ValueError) as err:  # noqa: PERF203
                last_err = err
                continue

        if not papers and last_err is not None:
            raise RuntimeError(f"Scholar 检索全部失败: {last_err}")

        if year_from:
            papers = [p for p in papers if (p.year or 0) >= year_from]
        self.report_progress(95, f"已获得 {len(papers)} 篇文献", stage="filter")
        return papers

    # ------------------------------------------------------------------
    def _request(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """带重试的 JSON 请求，对 429 限流采用指数退避。"""
        last_err: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                resp = requests.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 429:
                    # 限流：读取 Retry-After 头部，否则使用较长等待
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        wait_time = float(retry_after)
                    else:
                        wait_time = min(30.0, 5.0 * (2 ** attempt))
                    if attempt < self.retries:
                        time.sleep(wait_time)
                        continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as err:  # noqa: PERF203
                last_err = err
                if attempt < self.retries:
                    time.sleep(2.0 * (attempt + 1))
        raise RuntimeError(f"请求失败 {url}: {last_err}")

    def _search_s2(self, query: str, limit: int) -> List[Paper]:
        """Semantic Scholar Graph API。"""
        data = self._request(_S2_API, {
            "query": query,
            "limit": min(limit, 20),
            "fields": _S2_FIELDS,
        })
        papers: List[Paper] = []
        for item in data.get("data", []):
            ext = item.get("externalIds") or {}
            pdf_url = None
            oap = item.get("openAccessPdf")
            if oap and isinstance(oap, dict):
                pdf_url = oap.get("url")
            papers.append(Paper(
                title=(item.get("title") or "").strip(),
                url=item.get("url") or "",
                source=self.name,
                authors=[a.get("name", "") for a in item.get("authors", [])
                         if a.get("name")],
                year=item.get("year"),
                abstract=item.get("abstract"),
                doi=ext.get("DOI"),
                pdf_url=pdf_url,
                venue=item.get("venue"),
                extra={"s2_paper_id": item.get("paperId")},
            ))
        return papers

    def _search_crossref(self, query: str, limit: int) -> List[Paper]:
        """Crossref REST API（Semantic Scholar 不可用时的降级后端）。"""
        data = self._request(_CROSSREF_API, {
            "query": query,
            "rows": min(limit, 20),
            "select": "title,author,issued,DOI,URL,abstract,container-title",
        })
        papers: List[Paper] = []
        for item in data.get("message", {}).get("items", []):
            title = (item.get("title") or [""])[0]
            if not title:
                continue
            year = None
            issued = item.get("issued", {}).get("date-parts", [[None]])
            if issued and issued[0] and issued[0][0]:
                year = int(issued[0][0])
            authors = []
            for a in item.get("author", []):
                name = " ".join(
                    filter(None, [a.get("given"), a.get("family")]))
                if name:
                    authors.append(name)
            abstract = item.get("abstract")
            if abstract:
                # Crossref 摘要可能含 JATS XML 标签
                abstract = re.sub(r"<[^>]+>", " ", abstract).strip()
            papers.append(Paper(
                title=re.sub(r"\s+", " ", title).strip(),
                url=item.get("URL") or "",
                source=self.name,
                authors=authors,
                year=year,
                abstract=abstract,
                doi=item.get("DOI"),
                venue=(item.get("container-title") or [""])[0] or None,
            ))
        return papers

"""引用信息抓取技能：从任意 URL 提取文献元数据。

策略（按优先级）：
1. DOI / doi.org 链接  -> Crossref API 反查（最可靠）
2. arXiv 链接          -> arXiv API 反查
3. 普通学术页面        -> 解析 HTML 中的 citation_* meta 标签
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from .base import BaseSkill, SkillPermission
from .metadata import PAPER_SCHEMA, Paper

_META_PATTERNS = [
    (r'name="citation_title"\s+content="([^"]*)"', "title"),
    (r'name="citation_author"\s+content="([^"]*)"', "author"),
    (r'name="citation_publication_date"\s+content="([^"]*)"', "date"),
    (r'name="citation_online_date"\s+content="([^"]*)"', "date"),
    (r'name="citation_doi"\s+content="([^"]*)"', "doi"),
    (r'name="citation_pdf_url"\s+content="([^"]*)"', "pdf"),
    (r'name="citation_journal_title"\s+content="([^"]*)"', "venue"),
    (r'name="citation_abstract"\s+content="([^"]*)"', "abstract"),
]


class CitationScraperSkill(BaseSkill):
    """从论文落地页 / DOI 链接抓取结构化引用信息。"""

    name = "citation_scraper"
    description = "从任意文献 URL（DOI / arXiv / 期刊页）提取结构化元数据。"
    version = "1.1.0"
    input_schema = {
        "type": "object",
        "required": ["url"],
        "properties": {"url": {"type": "string", "minLength": 8,
                               "pattern": r"^https?://"}},
        "additionalProperties": True,
    }
    output_schema = PAPER_SCHEMA
    permissions = frozenset({SkillPermission.NETWORK})
    default_timeout_seconds = 60.0

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout
        self.headers = {
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/122.0 Safari/537.36"),
            "Accept-Language": "en-US,en;q=0.9",
        }

    # ------------------------------------------------------------------
    def execute(self, url: str, **_: Any) -> Paper:
        """抓取指定 URL 的文献信息。"""
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"非法 URL: {url}")

        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        self.report_progress(10, "正在识别文献地址", stage="resolve")

        # 1) DOI 优先
        doi = self._extract_doi(url)
        if doi:
            try:
                self.report_progress(35, "正在通过 DOI 获取元数据", stage="request")
                return self._fetch_by_doi(doi, url)
            except requests.RequestException:
                pass  # 降级到 HTML 解析

        # 2) arXiv
        if "arxiv.org" in host:
            try:
                self.report_progress(35, "正在通过 arXiv 获取元数据", stage="request")
                return self._fetch_arxiv(url)
            except requests.RequestException:
                pass

        # 3) 通用 HTML meta 解析
        self.report_progress(55, "正在解析论文页面", stage="parse")
        return self._fetch_html(url)

    # ------------------------------------------------------------------
    def _extract_doi(self, url: str) -> Optional[str]:
        """从 URL 中提取 DOI。"""
        if "doi.org" in url:
            match = re.search(r"doi\.org/([^?#]+)", url)
            if match:
                return match.group(1).strip()
        # 形如 /10.xxxx/yyyy 的路径
        match = re.search(r"/10\.\d{4,9}/[^\s?#]+", url)
        return match.group(0).lstrip("/") if match else None

    def _fetch_by_doi(self, doi: str, original_url: str) -> Paper:
        """通过 Crossref 反查 DOI 元数据。"""
        resp = requests.get(f"https://api.crossref.org/works/{doi}",
                            timeout=self.timeout)
        resp.raise_for_status()
        msg = resp.json()["message"]
        title = (msg.get("title") or [""])[0]
        year = None
        issued = msg.get("issued", {}).get("date-parts", [[None]])
        if issued and issued[0] and issued[0][0]:
            year = int(issued[0][0])
        authors = []
        for a in msg.get("author", []):
            name = " ".join(filter(None, [a.get("given"), a.get("family")]))
            if name:
                authors.append(name)
        return Paper(
            title=title.strip(),
            url=original_url,
            source=self.name,
            authors=authors,
            year=year,
            doi=doi,
            abstract=msg.get("abstract"),
            venue=(msg.get("container-title") or [""])[0] or None,
            extra={"resolved_via": "crossref_doi"},
        )

    def _fetch_arxiv(self, url: str) -> Paper:
        """通过 arXiv API 反查（兼容 abs 页与 pdf 链接）。"""
        arxiv_id = None
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.\d{4,5})(?:v\d+)?", url)
        if m:
            arxiv_id = m.group(1)
        if not arxiv_id:
            raise ValueError(f"无法从 URL 识别 arXiv ID: {url}")
        resp = requests.get(
            f"http://export.arxiv.org/api/query?id_list={arxiv_id}",
            timeout=self.timeout)
        resp.raise_for_status()
        from .arxiv_skill import ArxivSkill  # 复用解析逻辑
        papers = ArxivSkill()._parse(resp.text)
        if not papers:
            raise ValueError(f"arXiv 未找到该文献: {arxiv_id}")
        paper = papers[0]
        paper.url = url
        paper.extra["resolved_via"] = "arxiv_api"
        return paper

    def _fetch_html(self, url: str) -> Paper:
        """解析 HTML meta 标签。"""
        resp = requests.get(url, headers=self.headers, timeout=self.timeout)
        resp.raise_for_status()
        html = resp.text
        fields: Dict[str, Any] = {"title": None, "authors": [], "year": None,
                                  "doi": None, "pdf_url": None, "venue": None,
                                  "abstract": None}
        for pattern, key in _META_PATTERNS:
            match = re.search(pattern, html)
            if match:
                val = match.group(1).strip()
                if key == "author":
                    fields["authors"].append(val)
                elif key == "date":
                    m = re.search(r"(\d{4})", val)
                    if m and fields["year"] is None:
                        fields["year"] = int(m.group(1))
                else:
                    fields[key] = val

        title = fields["title"] or self._guess_title(html)
        if not title:
            raise ValueError(f"无法从页面解析出文献信息: {url}")
        return Paper(
            title=title,
            url=url,
            source=self.name,
            authors=fields["authors"],
            year=fields["year"],
            abstract=fields["abstract"],
            doi=fields["doi"],
            pdf_url=fields["pdf_url"],
            venue=fields["venue"],
            extra={"resolved_via": "html_meta"},
        )

    @staticmethod
    def _guess_title(html: str) -> Optional[str]:
        """无 citation_title 时回退到 <title> 标签。"""
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
            return title[:300] or None
        return None

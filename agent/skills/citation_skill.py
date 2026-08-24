"""引用网络技能：获取论文的参考文献与被引文献。

数据源：Semantic Scholar Graph API（免费，无 Key 限速较重，带退避重试）。
ID 解析优先级：S2 paper id → DOI → arXiv id。
"""

from __future__ import annotations

import os
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote

import requests

from .base import BaseSkill, SkillPermission
from .metadata import PAPER_SCHEMA, Paper

_S2_BASE = "https://api.semanticscholar.org/graph/v1/paper"
_S2_MATCH = f"{_S2_BASE}/search/match"
_FIELDS = "paperId,title,year,authors,externalIds,venue,url"


class CitationError(RuntimeError):
    """引用数据源调用失败。"""


class CitationIdError(CitationError):
    """无法为论文解析 Semantic Scholar 标识符。"""


class CitationNotFoundError(CitationError):
    """标识符在 Semantic Scholar 中不存在。"""


class CitationRateLimitError(CitationError):
    """Semantic Scholar 返回限流，并保留服务端建议等待时间。"""

    def __init__(self, message: str = "429 Too Many Requests",
                 retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class CitationSkill(BaseSkill):
    """引用/被引获取技能。"""

    name = "citation"
    description = "获取论文的参考文献与被引文献（Semantic Scholar）。"
    version = "1.1.0"
    input_schema = {
        "type": "object",
        "required": ["paper"],
        "properties": {
            "paper": PAPER_SCHEMA,
            "mode": {"type": "string", "enum": ["references", "citations"]},
        },
        "additionalProperties": True,
    }
    output_schema = {"type": "array", "items": PAPER_SCHEMA}
    permissions = frozenset({SkillPermission.NETWORK})
    default_timeout_seconds = 180.0

    def __init__(self, timeout: int = 20, retries: int = 2,
                 max_per_paper: int = 50,
                 backoff_base: float = 3.0,
                 min_interval: float = 1.1,
                 api_key: Optional[str] = None) -> None:
        self.timeout = timeout
        self.retries = retries
        self.max_per_paper = max_per_paper
        self.backoff_base = backoff_base
        self.min_interval = max(0.0, float(min_interval))
        self.api_key = (api_key or os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
                        or os.environ.get("S2_API_KEY") or "")
        self._last_request_at = 0.0
        self._rate_lock = threading.Lock()

    # ------------------------------------------------------------------
    def execute(self, paper: Paper, mode: str = "references",
                **_: Any) -> List[Paper]:
        """统一入口。

        Args:
            paper: 目标论文。
            mode: 'references'（该论文引用的文献）| 'citations'（引用它的文献）。
        """
        label = "被引文献" if mode == "citations" else "参考文献"
        self.report_progress(10, f"正在解析论文标识并获取{label}", stage="resolve")
        result = (self.get_citations(paper) if mode == "citations"
                  else self.get_references(paper))
        self.report_progress(95, f"已获取 {len(result)} 篇{label}", stage="parse")
        return result

    def get_references(self, paper: Paper) -> List[Paper]:
        """获取参考文献（paper 引用的文献）。"""
        data = self._request(paper, "references")
        out: List[Paper] = []
        for item in data.get("data", []):
            ref = item.get("citedPaper")
            if ref:
                out.append(self._to_paper(ref))
        return out

    def get_citations(self, paper: Paper) -> List[Paper]:
        """获取被引文献（引用 paper 的文献）。"""
        data = self._request(paper, "citations")
        out: List[Paper] = []
        for item in data.get("data", []):
            cit = item.get("citingPaper")
            if cit:
                out.append(self._to_paper(cit))
        return out

    # ------------------------------------------------------------------
    def _request(self, paper: Paper, mode: str) -> Dict[str, Any]:
        """带退避重试的 API 请求（429 时指数退避）。"""
        paper_id = self._resolve_id(paper)
        if not paper_id:
            paper_id = self._resolve_id_by_title(paper)
        url = f"{_S2_BASE}/{quote(paper_id, safe='')}/{mode}"
        params = {"fields": _FIELDS, "limit": self.max_per_paper}

        try:
            return self._fetch_json(url, params)
        except CitationNotFoundError:
            # DOI/arXiv 元数据偶尔错误或尚未建立映射，再用标题检索一次。
            fallback_id = self._resolve_id_by_title(paper)
            if fallback_id == paper_id:
                raise
            fallback_url = (
                f"{_S2_BASE}/{quote(fallback_id, safe='')}/{mode}")
            return self._fetch_json(fallback_url, params)

    def _fetch_json(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """统一请求、节流与重试，完整尊重 Retry-After。"""

        last_err: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                return self._get_json(url, params)
            except Exception as err:  # noqa: BLE001
                last_err = err
                if isinstance(err, CitationNotFoundError):
                    raise
                if attempt >= self.retries:
                    break
                if isinstance(err, CitationRateLimitError):
                    wait = (err.retry_after if err.retry_after is not None
                            else self.backoff_base * (2 ** attempt))
                    time.sleep(min(max(wait, self.min_interval), 60.0))
                else:
                    time.sleep(min(1.0 * (attempt + 1), 5.0))
        if isinstance(last_err, CitationError):
            raise last_err
        raise CitationError(f"引用请求失败: {last_err}")

    def _get_json(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """可被子类覆写的 HTTP 层（测试注入点）。"""
        with self._rate_lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_request_at = time.monotonic()
        headers = {"x-api-key": self.api_key} if self.api_key else None
        resp = requests.get(url, params=params, headers=headers,
                            timeout=self.timeout)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else None
            except ValueError:
                wait = None
            raise CitationRateLimitError(retry_after=wait)
        if resp.status_code == 404:
            raise CitationNotFoundError("Semantic Scholar 中未找到该论文")
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _resolve_id(paper: Paper) -> Optional[str]:
        """从结构化字段及常见论文 URL 解析 S2 接受的论文 ID。"""
        extra = paper.extra or {}
        s2_id = (extra.get("s2_paper_id") or extra.get("paperId")
                 or extra.get("semantic_scholar_id"))
        if s2_id:
            return str(s2_id)
        corpus_id = extra.get("corpusId") or extra.get("corpus_id")
        if corpus_id:
            return f"CorpusId:{corpus_id}"
        external = extra.get("externalIds") or {}
        for field, prefix in (("MAG", "MAG"), ("ACL", "ACL"),
                              ("PMID", "PMID"), ("PMCID", "PMCID")):
            value = external.get(field) or extra.get(field) or extra.get(field.lower())
            if value:
                return f"{prefix}:{value}"
        doi = paper.doi or external.get("DOI") or extra.get("doi")
        if not doi:
            doi_match = re.search(r"(?:doi\.org/|doi:\s*)(10\.\d{4,9}/[^?#\s]+)",
                                  unquote(paper.url or ""), re.I)
            doi = doi_match.group(1) if doi_match else None
        if doi:
            clean_doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "",
                               str(doi).strip(), flags=re.I)
            return f"DOI:{clean_doi}"
        arxiv_id = external.get("ArXiv") or extra.get("arxiv_id")
        if not arxiv_id:
            m = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#]+)", paper.url or "", re.I)
            arxiv_id = m.group(1) if m else None
        if arxiv_id:
            clean_arxiv = re.sub(r"\.pdf$", "", str(arxiv_id), flags=re.I)
            clean_arxiv = re.sub(r"v\d+$", "", clean_arxiv)
            return f"ARXIV:{clean_arxiv}"
        s2_url = re.search(r"semanticscholar\.org/paper/(?:[^/]+/)?([0-9a-f]{40})",
                           paper.url or "", re.I)
        if s2_url:
            return s2_url.group(1)
        if re.search(r"(?:semanticscholar|arxiv|aclweb|acm|biorxiv)\.org/",
                     paper.url or "", re.I):
            return f"URL:{paper.url}"
        return None

    def _resolve_id_by_title(self, paper: Paper) -> str:
        """无外部 ID 时按标题检索并进行标题/年份相似度校验。"""
        title = re.sub(r"\s+", " ", paper.title or "").strip()
        if len(title) < 4:
            raise CitationIdError(f"缺少可用论文 ID，且标题过短: {title or '未知标题'}")
        query = re.sub(r"[-‐‑–—]+", " ", title)
        response = self._fetch_json(_S2_MATCH, {
            "query": query,
            # matchScore 是标题匹配端点的固定返回字段，不能写入 fields，
            # 否则官方 API 会返回 400 Unrecognized field。
            "fields": "paperId,title,year,externalIds",
        })
        matches = response.get("data")
        best = (matches[0] if isinstance(matches, list) and matches
                and isinstance(matches[0], dict) else response)
        target = self._normalized_title(title)
        if len(target) < 4:
            raise CitationIdError(f"标题缺少可识别字符: {title[:60]}")
        candidate = self._normalized_title(str(best.get("title") or ""))
        local_score = SequenceMatcher(None, target, candidate).ratio()
        year_matches = not paper.year or not best.get("year") \
            or paper.year == best.get("year")
        if ((local_score < 0.84 or not year_matches)
                or not best.get("paperId")):
            raise CitationIdError(f"未找到可靠标题匹配: {title[:60]}")
        if not isinstance(paper.extra, dict):
            paper.extra = {}
        paper.extra["s2_paper_id"] = best["paperId"]
        return str(best["paperId"])

    @staticmethod
    def _normalized_title(title: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title.lower())

    @staticmethod
    def _to_paper(data: Dict[str, Any]) -> Paper:
        """S2 论文对象 → Paper。"""
        ext = data.get("externalIds") or {}
        return Paper(
            title=(data.get("title") or "").strip(),
            url=data.get("url") or "",
            source="citation",
            authors=[a.get("name", "") for a in data.get("authors", [])
                     if a.get("name")],
            year=data.get("year"),
            doi=ext.get("DOI"),
            venue=data.get("venue"),
            extra={"s2_paper_id": data.get("paperId")},
        )

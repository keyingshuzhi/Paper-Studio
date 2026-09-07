"""研究模板的差异化证据整理。

这些函数不额外调用模型，而是把检索、摘要和跨文献分析已经得到的证据
重新组织成不同的决策视图。这样模板的价值来自工作流与产物结构，而不是
仅仅换一组 ``max_results`` / ``rounds`` 参数。
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..skills.metadata import Paper


SURVEY_TEMPLATE = "research_template_survey"
OPENING_TEMPLATE = "research_template_opening"
COMPETITOR_TEMPLATE = "research_template_competitor"
DAILY_TEMPLATE = "research_template_daily"

_MISSING_PREFIXES = (
    "（未从可用摘要", "未从可用摘要", "未报告", "暂无", "未知",
)


def paper_identity(paper: Paper | Dict[str, Any] | str) -> str:
    """返回跨来源稳定的论文标识，优先 DOI，其次 URL，最后归一化标题。"""
    if isinstance(paper, str):
        title = paper
        doi = url = ""
    elif isinstance(paper, Paper):
        title, doi, url = paper.title, paper.doi or "", paper.url or ""
    else:
        title = str(paper.get("title") or "")
        doi = str(paper.get("doi") or "")
        url = str(paper.get("url") or "")
    if doi.strip():
        return "doi:" + doi.strip().casefold()
    if url.strip():
        return "url:" + url.strip().rstrip("/").casefold()
    normalized = "".join(ch for ch in title.casefold() if ch.isalnum())
    return "title:" + normalized


def paper_date(paper: Paper) -> Optional[date]:
    """尽力读取搜索源提供的精确发表日期。"""
    extra = paper.extra if isinstance(paper.extra, dict) else {}
    for key in ("published_date", "published", "publication_date", "updated"):
        raw = str(extra.get(key) or "").strip()
        if not raw:
            continue
        clean = raw[:10]
        try:
            return datetime.strptime(clean, "%Y-%m-%d").date()
        except ValueError:
            continue
    return None


def filter_daily_papers(
        papers: Sequence[Paper], days_back: int,
        seen_keys: Optional[Iterable[str]] = None,
) -> Tuple[List[Paper], Dict[str, Any]]:
    """按精确日期与历史标识筛出每日追踪真正需要阅读的新文献。

    部分来源只提供年份。此时保留处于截止年份内的候选项，但在统计中明确
    标为“日期待核验”，避免把缺失日期误报成精确的最近 N 天。
    """
    window = max(1, min(365, int(days_back)))
    cutoff = date.today() - timedelta(days=window)
    seen = {str(item) for item in (seen_keys or []) if str(item)}
    fresh: List[Paper] = []
    exact_dates = 0
    approximate_dates = 0
    already_seen = 0
    outside_window = 0
    for paper in papers:
        # 同一篇论文在 arXiv、Semantic Scholar 与 Crossref 中可能拥有不同
        # URL。除 DOI/URL 主标识外同时核对归一化标题，避免换源后被误报为
        # “今日新增”。
        title_identity = paper_identity(paper.title)
        if paper_identity(paper) in seen or title_identity in seen:
            already_seen += 1
            continue
        published = paper_date(paper)
        if published is not None:
            if published < cutoff:
                outside_window += 1
                continue
            exact_dates += 1
        elif paper.year is not None:
            if int(paper.year) < cutoff.year:
                outside_window += 1
                continue
            approximate_dates += 1
        else:
            approximate_dates += 1
        fresh.append(paper)
    fresh.sort(key=lambda item: (
        paper_date(item) or date(int(item.year or 1), 1, 1), item.title),
        reverse=True)
    return fresh, {
        "days_back": window,
        "cutoff": cutoff.isoformat(),
        "searched_count": len(papers),
        "new_count": len(fresh),
        "already_seen_count": already_seen,
        "outside_window_count": outside_window,
        "exact_date_count": exact_dates,
        "approximate_date_count": approximate_dates,
    }


def build_single_template_insights(
        template: str, query: str, papers: Sequence[Paper],
        summaries: Optional[Sequence[Dict[str, Any]]],
        analysis: Optional[Dict[str, Any]], *,
        compare_dimensions: Optional[Sequence[str]] = None,
        daily_stats: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """为单轮模板生成可直接渲染的结构化决策视图。"""
    profiles = _profiles(papers, summaries)
    base = analysis if isinstance(analysis, dict) else {}
    if template == OPENING_TEMPLATE:
        gaps = _gap_items(base)
        methods = _unique(profile["method"] for profile in profiles)
        limitations = _unique(profile["limitation"] for profile in profiles)
        questions = []
        for gap in gaps[:4]:
            gap_text = _compact(gap.get("gap"), 180)
            if gap_text:
                questions.append({
                    "question": f"如何围绕“{gap_text}”建立可验证的研究问题？",
                    "evidence": _compact(gap.get("why"), 220),
                    "search": _compact(gap.get("suggested_query"), 160),
                })
        if not questions:
            questions.append({
                "question": f"围绕“{query}”，哪些可量化问题尚未得到充分验证？",
                "evidence": "当前证据尚未形成明确知识盲点，建议先扩大检索范围。",
                "search": query,
            })
        evidence_score = min(100, len(papers) * 8 + len(methods) * 6
                             + len(gaps) * 5)
        if len(papers) >= 6 and (methods or gaps):
            verdict = "建议进入方案设计"
        elif len(papers) >= 3:
            verdict = "可继续，但需补充关键证据"
        else:
            verdict = "建议先扩大检索再定题"
        return {
            "kind": "opening",
            "title": "开题决策面板",
            "verdict": verdict,
            "evidence_score": evidence_score,
            "reason": (f"已获得 {len(papers)} 篇相关文献、"
                       f"{len(methods)} 条可辨识方法路线和 {len(gaps)} 个研究空白。"),
            "research_questions": questions,
            "method_routes": methods[:5],
            "novelty_opportunities": [
                _compact(item.get("gap"), 220) for item in gaps[:5]
                if _compact(item.get("gap"), 220)
            ],
            "risks": limitations[:5] or ["现有摘要证据不足，需精读全文后再确认技术风险。"],
            "next_steps": [
                "选择 1–2 个可量化研究问题并明确因变量与对照基线",
                "精读代表文献，核验方法、数据集和评价指标是否可复现",
                "把知识盲点转换为实验假设、最小验证方案与风险预案",
            ],
        }
    if template == COMPETITOR_TEMPLATE:
        dimensions = _unique(compare_dimensions or (
            "研究问题", "方法路线", "核心贡献", "已知局限"))[:6]
        rows = []
        for profile in profiles:
            rows.append({
                "paper": profile["title"],
                "year": profile["year"],
                "cells": [_dimension_value(profile, item)
                          for item in dimensions],
            })
        resolved = sum(bool(paper.abstract or paper.doi) for paper in papers)
        return {
            "kind": "competitor",
            "title": "竞品论文对照矩阵",
            "dimensions": dimensions,
            "rows": rows,
            "evidence_resolved": resolved,
            "evidence_total": len(papers),
            "recommendations": [{
                "paper": profile["title"],
                "strength": profile["contribution"] or "贡献需结合全文进一步核验",
                "tradeoff": profile["limitation"] or "局限需结合全文进一步核验",
            } for profile in profiles],
        }
    if template == DAILY_TEMPLATE:
        stats = dict(daily_stats or {})
        keywords = Counter()
        for profile in profiles:
            keywords.update(profile["keywords"])
        highlights = []
        for paper, profile in zip(papers, profiles):
            highlights.append({
                "title": paper.title,
                "date": (paper_date(paper).isoformat() if paper_date(paper)
                         else str(paper.year or "日期待核验")),
                "signal": (profile["contribution"] or profile["method"]
                           or "已发现新文献，建议打开摘要或全文核验"),
            })
        return {
            "kind": "daily",
            "title": "增量追踪简报",
            **stats,
            "trend_keywords": [item for item, _count in keywords.most_common(8)],
            "highlights": highlights[:10],
            "status": ("发现新的研究证据" if highlights
                       else "本次窗口内没有发现未读新文献"),
            "next_action": ("优先阅读贡献信号最明确的新文献，并决定是否加入文献库。"
                            if highlights else
                            "无需重复阅读；可扩大时间窗口或调整检索主题。"),
        }
    return {}


def build_survey_insights(
        rounds: Sequence[Dict[str, Any]], papers: Sequence[Paper],
) -> Dict[str, Any]:
    """把多轮综述结果组织为覆盖度、方法谱系、共识与空白地图。"""
    sources = Counter(str(paper.source or "未知来源") for paper in papers)
    years = [int(paper.year) for paper in papers if isinstance(paper.year, int)]
    methods: List[str] = []
    contributions: List[str] = []
    limitations: List[str] = []
    consensus: List[str] = []
    conflicts: List[str] = []
    gaps: List[str] = []
    for record in rounds:
        for profile in _profiles(record.get("papers") or [],
                                 record.get("summaries") or []):
            methods.append(profile["method"])
            contributions.append(profile["contribution"])
            limitations.append(profile["limitation"])
        analysis = record.get("analysis") if isinstance(
            record.get("analysis"), dict) else {}
        consensus.extend(_compact(item.get("statement"), 240)
                         for item in analysis.get("consensus") or []
                         if isinstance(item, dict))
        conflicts.extend(
            _compact(f"{item.get('statement_a', '')} / {item.get('statement_b', '')}", 260)
            for item in analysis.get("conflicts") or [] if isinstance(item, dict))
        gaps.extend(_compact(item.get("gap"), 240)
                    for item in analysis.get("gaps") or []
                    if isinstance(item, dict))
    exact_abstracts = sum(bool(paper.abstract) for paper in papers)
    return {
        "kind": "survey",
        "title": "系统综述证据地图",
        "coverage": {
            "papers": len(papers),
            "sources": len(sources),
            "rounds": len(rounds),
            "year_span": (f"{min(years)}–{max(years)}" if years else "年份待核验"),
            "abstract_coverage": round(exact_abstracts / len(papers), 3)
            if papers else 0.0,
        },
        "source_distribution": [{"source": key, "count": value}
                                for key, value in sources.most_common()],
        "method_landscape": _unique(methods)[:8],
        "representative_contributions": _unique(contributions)[:6],
        "consensus": _unique(consensus)[:6],
        "conflicts": _unique(conflicts)[:5],
        "gaps": _unique(gaps or limitations)[:6],
    }


def _profiles(papers: Sequence[Paper],
              summaries: Optional[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    profiles = []
    records = list(summaries or [])
    for index, paper in enumerate(papers):
        raw = records[index] if index < len(records) and isinstance(
            records[index], dict) else {}
        summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
        profiles.append({
            "title": paper.title,
            "year": paper.year,
            "problem": _useful(summary.get("problem")),
            "method": _useful(summary.get("method")),
            "contribution": _useful(summary.get("contribution")),
            "limitation": _useful(summary.get("limitation")),
            "keywords": [str(item).strip() for item in summary.get("keywords") or []
                         if str(item).strip()],
        })
    return profiles


def _dimension_value(profile: Dict[str, Any], dimension: str) -> str:
    key = str(dimension or "").casefold()
    if any(token in key for token in ("问题", "场景", "目标", "problem")):
        field = "problem"
    elif any(token in key for token in ("方法", "技术", "架构", "method")):
        field = "method"
    elif any(token in key for token in ("贡献", "优势", "创新", "效果", "contribution")):
        field = "contribution"
    elif any(token in key for token in ("局限", "风险", "代价", "不足", "limit")):
        field = "limitation"
    else:
        values = [profile.get("method"), profile.get("contribution"),
                  profile.get("limitation")]
        return "；".join(str(item) for item in values if item) or "需结合全文核验"
    return str(profile.get(field) or "需结合全文核验")


def _gap_items(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [item for item in analysis.get("gaps") or []
            if isinstance(item, dict)]


def _useful(value: Any) -> str:
    text = _compact(value, 600)
    if not text or any(text.startswith(prefix) for prefix in _MISSING_PREFIXES):
        return ""
    return text


def _unique(values: Iterable[Any]) -> List[str]:
    output: List[str] = []
    seen = set()
    for value in values:
        text = _useful(value)
        marker = re.sub(r"\s+", "", text.casefold())
        if text and marker not in seen:
            output.append(text)
            seen.add(marker)
    return output


def _compact(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"

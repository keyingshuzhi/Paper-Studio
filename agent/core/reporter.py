"""报告生成器（MCP 层：输出）。

把检索结果、下载结果与 LLM 智能摘要整理成结构化 Markdown 研究报告。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..skills.metadata import Paper


class Reporter:
    """生成研究报告（单轮 / 多轮深度 / 多主题对比）。"""

    # ------------------------------------------------------------------
    # 多主题对比报告（MultiTopicComparator）
    # ------------------------------------------------------------------
    def write_comparison(self, meta: Dict[str, Any],
                         topic_digests: Dict[str, Dict[str, Any]],
                         comparison: Dict[str, Any],
                         base_dir: str = "downloads",
                         filename: Optional[str] = None) -> Path:
        """写入多主题对比报告，返回路径。"""
        base = Path(base_dir)
        base.mkdir(parents=True, exist_ok=True)
        fname = filename or (
            f"compare_{time.strftime('%Y%m%d_%H%M%S')}.md")
        path = base / fname
        path.write_text(
            self.render_comparison(meta, topic_digests, comparison),
            encoding="utf-8")
        return path

    def render_comparison(self, meta: Dict[str, Any],
                          topic_digests: Dict[str, Dict[str, Any]],
                          comparison: Dict[str, Any]) -> str:
        """渲染多主题对比报告。"""
        topics = meta.get("topics", [])
        lines: List[str] = [
            f"# 多主题对比研究报告（{' vs '.join(topics)}）",
            "",
            f"- **开始时间**：{meta.get('started_at', '')}",
            "",
            "## 主题概览",
            "",
            "| 主题 | 文献数 | 共识点 | 盲点 |",
            "|------|--------|--------|------|",
        ]
        for t in topics:
            d = topic_digests.get(t, {})
            lines.append(f"| {t} | {d.get('papers_count', 0)} | "
                         f"{len(d.get('consensus', []))} | "
                         f"{len(d.get('gaps', []))} |")
        lines.append("")

        if comparison:
            lines += ["---", "", "## 横向综合（LLM）", ""]
            if comparison.get("overview"):
                lines += [f"> **整体态势**：{comparison['overview']}", ""]
            shared = comparison.get("shared_themes") or []
            if shared:
                lines += ["### 共享主题", ""]
                for i, s in enumerate(shared, 1):
                    lines.append(f"{i}. **{s.get('theme', '')}**"
                                 f"（{'、'.join(s.get('topics', []))}）")
                lines.append("")
            focus = comparison.get("distinct_focus") or []
            if focus:
                lines += ["### 各自侧重", ""]
                for f in focus:
                    lines.append(f"- **{f.get('topic', '')}**："
                                 f"{f.get('focus', '')}")
                lines.append("")
            overlap = comparison.get("overlap_papers") or []
            if overlap:
                lines += ["### 论文重叠", ""]
                for o in overlap:
                    lines.append(f"- `{o.get('title', '')}`"
                                 f"（{'、'.join(o.get('topics', []))}）")
                lines.append("")
            cross = comparison.get("cross_suggestions") or []
            if cross:
                lines += ["### 交叉研究建议", ""]
                for i, c in enumerate(cross, 1):
                    lines.append(f"{i}. **{c.get('suggestion', '')}**"
                                 f"（{'、'.join(c.get('topics', []))}）\n"
                                 f"   - 理由：{c.get('why', '')}")
                lines.append("")

        # 各主题简报
        lines += ["---", "", "## 各主题研究简报", ""]
        for t in topics:
            d = topic_digests.get(t, {})
            lines += [f"### {t}", "",
                      f"文献 {d.get('papers_count', 0)} 篇："]
            for p in d.get("papers", []):
                lines.append(f"- {p.get('year') or '?'} | "
                             f"{p.get('title')} ({p.get('source')})")
            lines.append("")
            if d.get("consensus"):
                lines += ["**共识点**："]
                for c in d["consensus"]:
                    lines.append(f"- {c}")
                lines.append("")
            if d.get("gaps"):
                lines += ["**盲点**："]
                for g in d["gaps"]:
                    lines.append(f"- {g}")
                lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 深度研究报告（ResearchLoop 多轮合并输出）
    # ------------------------------------------------------------------
    def write_deep(self, meta: Dict[str, Any],
                   rounds: List[Dict[str, Any]],
                   all_papers: List[Paper],
                   citations: Optional[Dict[str, Any]] = None,
                   acquisition: Optional[Dict[str, Any]] = None,
                   base_dir: str = "downloads",
                   filename: Optional[str] = None) -> Path:
        """写入深度研究报告，返回路径。"""
        base = Path(base_dir)
        base.mkdir(parents=True, exist_ok=True)
        fname = filename or (
            f"deep_report_{time.strftime('%Y%m%d_%H%M%S')}.md")
        path = base / fname
        path.write_text(
            self.render_deep(meta, rounds, all_papers, citations, acquisition),
            encoding="utf-8")
        return path

    def render_deep(self, meta: Dict[str, Any],
                    rounds: List[Dict[str, Any]],
                    all_papers: List[Paper],
                    citations: Optional[Dict[str, Any]] = None,
                    acquisition: Optional[Dict[str, Any]] = None) -> str:
        """渲染多轮深度研究报告。"""
        lines: List[str] = [
            f"# 深度研究报告：{meta['root_query']}",
            "",
            f"- **开始时间**：{meta.get('started_at', '')}",
            f"- **研究轮次**：{meta['rounds']} 轮",
            f"- **查询总数**：{meta['queries']} 次",
            f"- **文献总量**：{meta['papers_raw']} 篇 → "
            f"去重后 {meta['papers_dedup']} 篇",
            "",
            "---",
            "",
            "## 研究路径（查询分支树）",
            "",
        ]
        for rec in rounds:
            indent = "  " * (rec["round"] - 1)
            src = ("用户输入" if rec["origin"] == "user" else
                   "历史记忆" if rec["origin"] == "memory" else "知识盲点")
            lines.append(f"{indent}- **Round {rec['round']}** "
                         f"[{src}] `{rec['query']}`")
        reuse_block = self._render_memory_reuse(meta.get("memory_reuse") or [])
        if reuse_block:
            lines += [""] + reuse_block
        lines += ["", "---", "", "## 汇总文献清单（跨轮去重）", "",
                  "| # | 年份 | 来源 | 标题 | 链接 |",
                  "|---|------|------|------|------|"]
        for i, p in enumerate(all_papers, 1):
            link = f"[链接]({p.url})"
            lines.append(f"| {i} | {p.year or '—'} | {p.source} | "
                         f"{p.title} | {link} |")
        lines.append("")

        if acquisition:
            stats = acquisition.get("stats", {})
            lines += [
                "---", "", "## 本地文献资料包", "",
                f"- 目录：`{acquisition.get('base_dir', '')}`",
                f"- 计划处理：{stats.get('total', 0)} 篇",
                f"- PDF 下载成功：{stats.get('downloaded', stats.get('ok', 0))} 篇",
                f"- 无公开 PDF：{stats.get('unavailable', 0)} 篇",
                f"- 下载失败：{stats.get('failed', 0)} 篇",
                "",
                "可在 Paper Studio 的“文献库”中查看、打开或清理这些文件。",
                "",
            ]

        # 各轮详情
        for rec in rounds:
            lines += ["---", "",
                      f"## Round {rec['round']}：{rec['query']} "
                      f"（{rec['origin']}）", "",
                      f"命中 {len(rec['papers'])} 篇文献", ""]
            if rec["summaries"]:
                lines += self._render_summaries(rec["summaries"])
            if self._analysis_has_content(rec.get("analysis")):
                lines += self._render_analysis(rec["analysis"])

        # 引用网络分析
        if citations and not citations.get("_degraded"):
            lines += self._render_citations(citations)

        lines += ["---", "", "## 下一步建议", "",
                  "1. 基于「知识盲点」的建议检索词继续深挖（新一轮闭环）。",
                  "2. 下载重点文献全文，做精读（--max-downloads）。",
                  "3. 围绕「核心被引文献」做溯源精读，梳理领域脉络。",
                  ""]
        return "\n".join(lines)

    @staticmethod
    def _render_citations(data: Dict[str, Any]) -> List[str]:
        """渲染引用网络分析区块。"""
        lines = [
            "",
            "---",
            "",
            "## 引用网络分析",
            "",
            f"- 引用获取覆盖率：{data.get('coverage', 0):.0%} "
            f"（{data.get('analyzed_papers', 0)}/"
            f"{data.get('total_papers', 0)} 篇）",
            "",
        ]
        top = data.get("top_cited") or []
        intra = data.get("intra_citations") or []
        if top:
            lines += ["### 核心被引文献（语料引用枢纽）", "",
                      "| # | 年份 | 被引次数 | 文献 |",
                      "|---|------|---------|------|"]
            for i, e in enumerate(top[:10], 1):
                lines.append(
                    f"| {i} | {e.get('year') or '—'} | "
                    f"{e.get('cited_by')} | {e.get('title')} |")
            lines.append("")
        if intra:
            lines += ["### 语料内部互引", ""]
            for e in intra[:20]:
                lines.append(f"- `{e.get('citing')[:50]}` → "
                             f"`{e.get('cited')[:50]}`")
            lines.append("")
        errors = data.get("errors") or []
        recovered = int(data.get("recovered_papers") or 0)
        if recovered:
            lines += [f"> ✅ {recovered} 篇文献的临时引用失败已通过低速恢复重试自动恢复。",
                      ""]
        if errors:
            stats = data.get("error_stats") or {}
            # 兼容旧结果中的字符串错误记录。
            if not stats and errors:
                stats = {"request_failed": len(errors)}
            missing = int(stats.get("missing_id") or 0)
            not_found = int(stats.get("not_found") or 0)
            rate_limited = int(stats.get("rate_limited") or 0)
            request_failed = int(stats.get("request_failed") or 0)
            if missing or not_found:
                lines += [
                    f"> ℹ️ {missing + not_found} 篇文献未能在 Semantic Scholar "
                    "建立可靠匹配（缺少标准标识符或数据库尚未收录）。",
                    "",
                ]
            if rate_limited:
                lines += [
                    f"> ⚠️ {rate_limited} 篇文献在自动退避和恢复重试后仍受引用数据源限流。",
                    "",
                ]
            if request_failed:
                lines += [
                    f"> ⚠️ {request_failed} 篇文献因网络或引用数据源异常未完成获取。",
                    "",
                ]
        return lines

    # ------------------------------------------------------------------
    # 单轮研究报告
    # ------------------------------------------------------------------
    def write(self, plan: Any, papers: List[Paper],
              acquisition: Optional[Dict[str, Any]] = None,
              summaries: Optional[List[Dict[str, Any]]] = None,
              analysis: Optional[Dict[str, Any]] = None,
              base_dir: str = "downloads",
              filename: Optional[str] = None) -> Path:
        """生成报告并写入磁盘，返回报告路径。"""
        base = Path(base_dir)
        base.mkdir(parents=True, exist_ok=True)
        fname = filename or f"report_{time.strftime('%Y%m%d_%H%M%S')}.md"
        path = base / fname
        path.write_text(
            self.render(plan, papers, acquisition, summaries, analysis),
            encoding="utf-8")
        return path

    def render(self, plan: Any, papers: List[Paper],
               acquisition: Optional[Dict[str, Any]] = None,
               summaries: Optional[List[Dict[str, Any]]] = None,
               analysis: Optional[Dict[str, Any]] = None) -> str:
        """渲染 Markdown 报告内容。"""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        lines: List[str] = [
            f"# 学术检索报告：{plan.query}",
            "",
            f"- **检索时间**：{now}",
            f"- **用户输入**：{plan.original_query}",
            f"- **来源**：{'、'.join(self._source_names(papers)) or '无'}",
            f"- **命中文献**：{len(papers)} 篇（多源去重后）",
            "",
            "---",
            "",
            "## 文献清单",
            "",
            "| # | 年份 | 来源 | 标题 | 作者 | 链接 |",
            "|---|------|------|------|------|------|",
        ]
        for i, p in enumerate(papers, 1):
            authors = ", ".join(p.authors[:3])
            if len(p.authors) > 3:
                authors += " 等"
            link = f"[链接]({p.url})"
            if p.pdf_url:
                link += f" · [PDF]({p.pdf_url})"
            lines.append(
                f"| {i} | {p.year or '—'} | {p.source} | {p.title} | "
                f"{authors or '—'} | {link} |")

        if acquisition:
            lines += [
                "",
                "---",
                "",
                "## 下载结果",
                "",
                f"- 尝试下载：{acquisition['stats']['total']} 篇",
                f"- 成功：{acquisition['stats']['ok']} 篇",
                f"- 失败：{acquisition['stats']['failed']} 篇",
                f"- 资料目录：`{acquisition['base_dir']}`",
                "",
            ]
            failed = [it for it in acquisition["items"]
                      if it["status"] == "failed"]
            if failed:
                lines += ["**失败明细：**", ""]
                for it in failed:
                    lines.append(f"- `{it['title'][:50]}`：{it['error']}")
                lines.append("")

        lines += [
            "",
            "---",
            "",
            "## 下一步建议（V2.0 能力预告）",
            "",
            "1. **智能摘要**：为每篇文献提炼 问题/方法/贡献/局限。",
            "2. **观点对比**：跨文献对比共识点与分歧点。",
            "3. **知识盲点预警**：识别研究空白，推荐新课题方向。",
            "",
        ]

        # 智能摘要段（放在"下一步建议"之前更合理，故插到列表前）
        if summaries:
            block = self._render_summaries(summaries)
            idx = lines.index("## 下一步建议（V2.0 能力预告）")
            lines[idx:idx] = block

        # 跨文献分析段（插在摘要段之前、文献清单之后）
        reuse_block = self._render_memory_reuse(
            analysis.get("historical_reuse", []) if isinstance(analysis, dict) else [])
        if reuse_block:
            idx = lines.index("## 文献智能摘要（问题 / 方法 / 贡献 / 局限）") \
                if "## 文献智能摘要（问题 / 方法 / 贡献 / 局限）" in lines \
                else lines.index("## 下一步建议（V2.0 能力预告）")
            lines[idx:idx] = reuse_block
        if self._analysis_has_content(analysis):
            block = self._render_analysis(analysis)
            idx = lines.index("## 文献智能摘要（问题 / 方法 / 贡献 / 局限）") \
                if "## 文献智能摘要（问题 / 方法 / 贡献 / 局限）" in lines \
                else lines.index("## 下一步建议（V2.0 能力预告）")
            lines[idx:idx] = block
        return "\n".join(lines)

    @staticmethod
    def _render_memory_reuse(items: List[Dict[str, Any]]) -> List[str]:
        """把实际复用的历史主题写进报告，保证研究过程可审计。"""
        clean = [item for item in items if isinstance(item, dict)
                 and str(item.get("query") or "").strip()]
        if not clean:
            return []
        lines = ["---", "", "## 历史研究复用", "",
                 "以下内容来自本地知识库，已作为本次研究的背景证据；"
                 "结论仍需由本次文献交叉验证。", ""]
        for item in clean[:10]:
            score = item.get("score")
            related = (f" · 相关度 {float(score):.0%}"
                       if isinstance(score, (int, float)) else "")
            lines.append(f"- **《{item.get('query')}》**{related}")
            if item.get("conclusion"):
                lines.append(f"  - 已有结论：{item['conclusion']}")
            if item.get("paper_titles"):
                lines.append("  - 关联论文：" + "；".join(
                    str(title) for title in item["paper_titles"][:3] if title))
        lines.append("")
        return lines

    @staticmethod
    def _analysis_has_content(analysis: Optional[Dict[str, Any]]) -> bool:
        """降级分析只要含有效内容也应进入报告，不能被整体隐藏。"""
        if not isinstance(analysis, dict):
            return False
        return bool(
            str(analysis.get("summary") or "").strip()
            or any(analysis.get(key) for key in
                   ("consensus", "conflicts", "evolution", "gaps"))
        )

    @staticmethod
    def _render_analysis(analysis: Dict[str, Any]) -> List[str]:
        """渲染跨文献分析区块。"""
        lines = [
            "",
            "---",
            "",
            "## 跨文献对比与知识盲点",
            "",
        ]

        consensus = analysis.get("consensus") or []
        conflicts = analysis.get("conflicts") or []
        evolution = analysis.get("evolution") or []
        gaps = analysis.get("gaps") or []
        summary = (analysis.get("summary") or "").strip()

        if summary:
            lines += [f"> **领域态势**：{summary}", ""]

        if consensus:
            lines += ["### 共识点", ""]
            for i, c in enumerate(consensus, 1):
                papers = "、".join(f"#{p}" for p in c.get("papers", []))
                lines.append(f"{i}. **{c.get('topic', '')}**"
                             f"{f'（论文 {papers}）' if papers else ''}："
                             f"{c.get('statement', '')}")
            lines.append("")

        if conflicts:
            lines += ["### 分歧点", ""]
            for i, c in enumerate(conflicts, 1):
                pa = "、".join(f"#{p}" for p in c.get("papers_a", []))
                pb = "、".join(f"#{p}" for p in c.get("papers_b", []))
                lines.append(
                    f"{i}. **{c.get('topic', '')}**\n"
                    f"   - {pa or 'A方'}：{c.get('statement_a', '')}\n"
                    f"   - {pb or 'B方'}：{c.get('statement_b', '')}")
            lines.append("")

        if evolution:
            lines += ["### 演进路径", ""]
            for i, e in enumerate(evolution, 1):
                lines.append(f"{i}. {e.get('from', '')} → {e.get('to', '')}"
                             f"：{e.get('description', '')}")
            lines.append("")

        if gaps:
            lines += ["### 知识盲点（研究空白）", ""]
            for i, g in enumerate(gaps, 1):
                q = g.get("suggested_query")
                lines.append(f"{i}. **{g.get('gap', '')}**：{g.get('why', '')}"
                             + (f"（建议检索：`{q}`）" if q else ""))
            lines.append("")

        if not (consensus or conflicts or evolution or gaps or summary):
            lines.append("（未识别出明确的共识、分歧或空白）")
            lines.append("")
        return lines

    @staticmethod
    def _render_summaries(
            summaries: List[Dict[str, Any]]) -> List[str]:
        """渲染智能摘要区块（按论文序号对齐）。"""
        ok_items = [s for s in summaries if s.get("summary")]
        if not ok_items:
            return []

        lines = [
            "",
            "---",
            "",
            "## 文献智能摘要（问题 / 方法 / 贡献 / 局限）",
            "",
        ]
        for i, s in enumerate(ok_items, 1):
            sm = s["summary"]
            title = sm.get("title") or f"文献 {i}"
            problem = Reporter._summary_value(sm.get("problem"), "研究问题")
            method = Reporter._summary_value(sm.get("method"), "研究方法")
            contribution = Reporter._summary_value(
                sm.get("contribution"), "主要贡献")
            limitation = Reporter._summary_value(sm.get("limitation"), "局限")
            lines += [
                f"### {i}. {title}",
                "",
                f"- **问题**：{problem}",
                f"- **方法**：{method}",
                f"- **贡献**：{contribution}",
                f"- **局限**：{limitation}",
                "",
            ]
            kws = sm.get("keywords") or ["主题待全文核验"]
            if not isinstance(kws, list):
                kws = [str(kws)]
            lines.append(f"- **关键词**：{'、'.join(str(k) for k in kws if str(k).strip())}")
            lines.append("")
        failed = [s for s in summaries if not s.get("ok")]
        if failed:
            lines += [f"> ⚠️ {len(failed)} 篇摘要失败（详见控制台日志）", ""]
        return lines

    @staticmethod
    def _summary_value(value: Any, label: str) -> str:
        """报告层最后一道防线：统一类型且不输出空白横线。"""
        if isinstance(value, (list, tuple, set)):
            text = "；".join(str(item).strip() for item in value
                            if str(item).strip())
        elif isinstance(value, dict):
            text = "；".join(f"{key}：{item}" for key, item in value.items()
                            if str(item).strip())
        else:
            text = str(value or "").strip()
        if text.lower() in {"", "-", "—", "n/a", "na", "none", "null", "暂无"}:
            return f"（未从可用摘要或正文中提取到可靠的{label}，需结合全文核验）"
        return text

    @staticmethod
    def _source_names(papers: List[Paper]) -> List[str]:
        """收集报告中出现的来源名（去重、保序）。"""
        seen: Dict[str, None] = {}
        for p in papers:
            seen.setdefault(p.source, None)
        return list(seen)

"""多主题对比研究（V5.0）。

对多个研究主题分别做「检索 → 摘要 → 跨文献分析」，
再用 LLM 横向综合：共享主题 / 各自侧重 / 论文重叠 / 交叉建议，
产出多主题对比研究报告。
"""

from __future__ import annotations

import time
import json
from typing import Any, Dict, List, Optional

from ..skills import ReportWriteSkill
from .agent import ResearchAgent
from .json_utils import parse_json_block
from .llm import LLMClient, LLMError
from .reporter import Reporter

_COMPARE_SYSTEM = """\
你是一位跨领域研究战略分析专家。基于多个主题的研究简报，进行横向对比，\
输出严格 JSON，不要输出 JSON 以外的内容。

JSON 字段：
- "overview": 一句话概括这些主题的整体研究态势
- "shared_themes": 共享主题数组，每项 {"theme": "主题名", "topics": [主题名...]}
- "distinct_focus": 各自侧重数组，每项 {"topic": "主题名", "focus": "该主题的独特关注点"}
- "overlap_papers": 论文重叠数组（同一篇论文被多个主题覆盖），每项 \
{"title": "论文标题", "topics": [主题名...]}
- "cross_suggestions": 交叉研究建议数组（利用主题间联系的新研究点），每项 \
{"suggestion": "建议", "topics": [主题名...], "why": "理由"}

要求：主题名必须与输入中的名称完全一致；中文回答；忠实于输入简报。"""


class MultiTopicComparator:
    """多主题对比研究驱动器。"""

    def __init__(self, agent: Optional[ResearchAgent] = None,
                 llm: Optional[LLMClient] = None,
                 reporter: Optional[Reporter] = None) -> None:
        self.agent = agent or ResearchAgent()
        self.llm = llm or LLMClient()
        self.reporter = reporter or ReportWriteSkill()

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.llm.available

    def compare(self, topics: List[str], *,
                max_results: int = 5,
                **overrides: Any) -> Dict[str, Any]:
        """对比研究多个主题。

        Args:
            topics: 主题列表，如 ["transformer", "mamba"]。
            max_results: 每主题的结果上限。
            **overrides: 透传给每主题 ResearchAgent.run 的参数。

        Returns:
            {"topics": {主题: 简报}, "comparison": {...}, "report_path": ...}
        """
        if len(topics) < 2:
            raise ValueError("多主题对比至少需要 2 个主题")
        topics = [t.strip() for t in topics if t.strip()]
        if len(topics) < 2:
            raise ValueError("有效主题不足 2 个")

        topic_digests: Dict[str, Dict[str, Any]] = {}
        for t in topics:
            print(f"\n[主题] 研究: {t!r}")
            result = self.agent.run(
                t, max_results=max_results,
                summarize=True, analyze=True,
                report=False,  # 统一由对比报告输出
                **overrides)
            topic_digests[t] = self._digest(t, result)

        # 横向综合（LLM）
        comparison: Dict[str, Any] = {}
        if self.llm.available:
            print("\n[对比] LLM 横向综合分析中 ...")
            try:
                comparison = self._synthesize(topic_digests)
                print(f"[对比] 完成：共享 {len(comparison.get('shared_themes', []))} | "
                      f"重叠论文 {len(comparison.get('overlap_papers', []))} | "
                      f"交叉建议 {len(comparison.get('cross_suggestions', []))}")
            except (LLMError, ValueError, json.JSONDecodeError) as err:
                comparison = self._fallback_comparison(
                    topic_digests, str(err))
                print(f"[warn] 横向综合输出不可用，已生成本地结构化分析：{err}")
        else:
            comparison = self._fallback_comparison(
                topic_digests, "模型当前不可用")
            print("[对比] 模型不可用，已生成本地结构化横向分析")

        meta = {
            "topics": topics,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "papers_per_topic": {t: d["papers_count"]
                                 for t, d in topic_digests.items()},
        }
        report_path = self.reporter.write_comparison(
            meta, topic_digests, comparison)

        return {
            "topics": topic_digests,
            "comparison": comparison,
            "report_path": str(report_path),
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _digest(topic: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """把单主题研究结果压成简报。"""
        analysis = result.get("analysis") or {}
        papers = result.get("papers") or []
        return {
            "query": topic,
            "papers_count": len(papers),
            "papers": [{"title": p.title, "year": p.year,
                        "source": p.source} for p in papers[:8]],
            "consensus": [c.get("statement", "") for c in
                          (analysis.get("consensus") or [])][:5],
            "gaps": [g.get("gap", "") for g in
                     (analysis.get("gaps") or [])][:5],
            "summary": (analysis.get("summary") or "").strip(),
        }

    def _synthesize(self, digests: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """LLM 横向综合。"""
        user = self._build_prompt(digests)
        raw = self.llm.chat(user=user, system=_COMPARE_SYSTEM,
                            json_mode=True, temperature=0.3,
                            max_tokens=2048)
        data = parse_json_block(raw)
        result = {
            "overview": str(data.get("overview") or "").strip(),
            "shared_themes": self._as_list(data.get("shared_themes")),
            "distinct_focus": self._as_list(data.get("distinct_focus")),
            "overlap_papers": self._as_list(data.get("overlap_papers")),
            "cross_suggestions": self._as_list(
                data.get("cross_suggestions")),
        }
        fallback = self._fallback_comparison(digests)
        for key in ("overview", "shared_themes", "distinct_focus",
                    "cross_suggestions"):
            if not result.get(key):
                result[key] = fallback[key]
        return result

    @staticmethod
    def _build_prompt(digests: Dict[str, Dict[str, Any]]) -> str:
        lines: List[str] = [f"共 {len(digests)} 个主题的研究简报：", ""]
        for topic, d in digests.items():
            lines += [f"### 主题：{topic}",
                      f"- 文献数：{d['papers_count']}"]
            if d["papers"]:
                lines.append("- 代表文献：" + "；".join(
                    f"{p['title']} ({p['year'] or '?'})" for p in d["papers"][:5]))
            if d["consensus"]:
                lines.append("- 共识点：" + "；".join(d["consensus"][:3]))
            if d["gaps"]:
                lines.append("- 盲点：" + "；".join(d["gaps"][:3]))
            if d["summary"]:
                lines.append(f"- 态势：{d['summary']}")
            lines.append("")
        return "\n".join(lines) + "请输出多主题对比 JSON。"

    @staticmethod
    def _as_list(value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [v for v in value if isinstance(v, dict)]

    @staticmethod
    def _fallback_comparison(digests: Dict[str, Dict[str, Any]],
                             error: str = "") -> Dict[str, Any]:
        """在模型综合不可用时保持与云端相同的完整结果结构。"""
        topics = list(digests)
        joined = "、".join(topics)
        distinct = []
        for topic, digest in digests.items():
            focus = (digest.get("summary")
                     or next(iter(digest.get("consensus") or []), "")
                     or (digest.get("papers") or [{}])[0].get("title")
                     or "当前证据不足，需补充代表文献")
            distinct.append({"topic": topic, "focus": str(focus)})

        seen: Dict[str, List[str]] = {}
        original_titles: Dict[str, str] = {}
        for topic, digest in digests.items():
            for paper in digest.get("papers") or []:
                title = str(paper.get("title") or "").strip()
                key = title.lower()
                if title:
                    original_titles[key] = title
                    seen.setdefault(key, []).append(topic)
        overlaps = [
            {"title": original_titles[key], "topics": topic_names}
            for key, topic_names in seen.items() if len(set(topic_names)) > 1
        ]
        result: Dict[str, Any] = {
            "overview": (
                f"已基于各主题的文献与结构化摘要完成{joined}的本地横向归纳；"
                "模型综合输出未完成，跨主题结论需结合全文复核。"
            ),
            "shared_themes": [{
                "theme": "跨主题证据关联",
                "topics": topics,
            }],
            "distinct_focus": distinct,
            "overlap_papers": overlaps,
            "cross_suggestions": [{
                "suggestion": f"围绕{joined}建立统一评价维度并补充交叉检索",
                "topics": topics,
                "why": "统一问题、方法、贡献与局限字段后，可进一步验证主题间的真实关联。",
            }],
            "_fallback": True,
        }
        if error:
            result["_error"] = error
        return result

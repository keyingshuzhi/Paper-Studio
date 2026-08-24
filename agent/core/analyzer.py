"""跨文献分析与知识盲点预警（V2.0 综述专家能力）。

输入：多篇论文的结构化画像（来自摘要器或元数据）。
输出：共识点 / 分歧点 / 演进路径 / 知识盲点 / 领域态势总结。

典型输出结构：
{
  "consensus":  [{"topic": "...", "papers": [1, 2, 3], "statement": "..."}],
  "conflicts":  [{"topic": "...", "papers_a": [1], "statement_a": "...",
                  "papers_b": [2, 3], "statement_b": "..."}],
  "evolution":  [{"from": "...", "to": "...", "description": "..."}],
  "gaps":       [{"gap": "...", "why": "...", "suggested_query": "..."}],
  "summary":    "一句话总结该领域研究态势"
}
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .json_utils import parse_json_block
from .llm import LLMClient, LLMError

_ANALYZE_SYSTEM = """\
你是一位严谨的学术综述专家。基于给定的多篇论文结构化画像，进行跨文献分析，\
输出严格 JSON，不要输出 JSON 以外的内容。

JSON 字段：
- "consensus": 共识点数组（多篇论文一致认可的结论/做法）。每项：
    {"topic": "共识话题", "papers": [论文编号...], "statement": "共识内容"}
- "conflicts": 分歧点数组（不同论文观点/方法/结论互相矛盾或取舍不同）。每项：
    {"topic": "分歧话题", "papers_a": [编号], "statement_a": "A 方观点",
     "papers_b": [编号], "statement_b": "B 方观点"}
- "evolution": 演进路径数组（技术或思想的先后演进）。每项：
    {"from": "早期形态", "to": "当前形态", "description": "演进说明"}
- "gaps": 知识盲点数组（现有文献未覆盖/未解决的问题）。每项：
    {"gap": "缺口描述", "why": "为什么重要", "suggested_query": "建议的检索关键词"}
- "summary": 用一句话总结该领域的当前研究态势

要求：
- 论文编号必须与输入中的编号一致，只用真实存在的编号
- 没有共识/分歧/演进时输出空数组 []
- 忠实于输入画像，不编造不存在的观点
- 中文回答"""


class CrossPaperAnalyzer:
    """跨文献对比分析与知识盲点预警器。"""

    def __init__(self, llm: Optional[LLMClient] = None,
                 max_papers: int = 10) -> None:
        self.llm = llm or LLMClient()
        self.max_papers = max_papers

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.llm.available

    def analyze(self, profiles: List[Dict[str, Any]],
                temperature: float = 0.3) -> Dict[str, Any]:
        """分析多篇论文画像，返回结构化分析结果。

        Args:
            profiles: [{"index", "title", "year", "source",
                        "problem", "method", "contribution",
                        "limitation", "keywords"}, ...]
        """
        if not profiles:
            raise ValueError("没有可分析的论文")
        targets = profiles[:self.max_papers]
        if not self.llm.available:
            return self._fallback_analysis(targets, "模型当前不可用")

        user = self._build_prompt(targets)
        try:
            raw = self.llm.chat(user=user, system=_ANALYZE_SYSTEM,
                                json_mode=True, temperature=temperature,
                                max_tokens=2048)
            data = parse_json_block(raw)
            normalized = self._normalize(data)
            if not normalized.get("summary"):
                normalized["summary"] = self._fallback_analysis(
                    targets)["summary"]
            return normalized
        except (LLMError, ValueError, json.JSONDecodeError) as err:
            # 分析失败不能让已完成的检索和摘要整体报废；保留论文与报告，
            # 并显式标记本轮没有得到 LLM 综合结论。
            print(f"[warn] 跨文献分析输出不可用，已降级继续：{err}")
            return self._fallback_analysis(targets, str(err))

    # ------------------------------------------------------------------
    def _build_prompt(self, profiles: List[Dict[str, Any]]) -> str:
        lines: List[str] = [f"共 {len(profiles)} 篇论文画像：", ""]
        for p in profiles:
            idx = p.get("index", "?")
            meta = f"[{idx}] ({p.get('year') or '?'}, {p.get('source') or '?'}) " \
                   f"{p.get('title') or '未知标题'}"
            lines.append(meta)
            for key, label in (("problem", "问题"), ("method", "方法"),
                               ("contribution", "贡献"),
                               ("limitation", "局限")):
                val = (p.get(key) or "").strip()
                if val:
                    lines.append(f"  - {label}：{val[:800]}")
            kws = p.get("keywords") or []
            if kws:
                lines.append(f"  - 关键词：{'、'.join(str(k) for k in kws)}")
            lines.append("")
        return "\n".join(lines) + "请输出跨文献分析 JSON。"

    @staticmethod
    def _normalize(data: Dict[str, Any]) -> Dict[str, Any]:
        """清洗字段：缺失键补默认值，编号字段强制 int 列表。"""
        out: Dict[str, Any] = {
            "consensus": data.get("consensus") or [],
            "conflicts": data.get("conflicts") or [],
            "evolution": data.get("evolution") or [],
            "gaps": data.get("gaps") or [],
            "summary": str(data.get("summary") or "").strip(),
        }
        for key in ("consensus", "conflicts", "evolution", "gaps"):
            items = out[key]
            if not isinstance(items, list):
                out[key] = []
                continue
            cleaned = []
            for it in items:
                if isinstance(it, dict):
                    cleaned.append(CrossPaperAnalyzer._clean_item(it))
            out[key] = cleaned
        return out

    @staticmethod
    def _clean_item(item: Dict[str, Any]) -> Dict[str, Any]:
        """清洗单条分析项：论文编号字段强制为 int 列表。"""
        result = dict(item)
        for key in ("papers", "papers_a", "papers_b"):
            if key in result:
                val = result[key]
                if isinstance(val, list):
                    result[key] = [int(v) for v in val if str(v).isdigit()]
                else:
                    result.pop(key, None)
        return result

    @staticmethod
    def _fallback_analysis(profiles: List[Dict[str, Any]],
                           error: str = "") -> Dict[str, Any]:
        """模型综合失败时，基于完整论文画像生成可追溯的本地分析。"""
        titles = [str(p.get("title") or "未知标题") for p in profiles]
        keywords: List[str] = []
        for profile in profiles:
            for keyword in profile.get("keywords") or []:
                word = str(keyword).strip()
                if word and word not in keywords:
                    keywords.append(word)
        topic = "、".join(keywords[:4]) or "、".join(titles[:2])
        summary = (
            f"本轮基于 {len(profiles)} 篇论文的结构化摘要完成本地归纳，"
            f"研究内容主要围绕{topic}展开；模型综合输出未完成，结论需结合全文复核。"
        )

        consensus: List[Dict[str, Any]] = []
        if len(profiles) >= 2:
            consensus.append({
                "topic": "共同研究方向",
                "papers": [int(p.get("index")) for p in profiles
                           if str(p.get("index") or "").isdigit()],
                "statement": f"这些文献均围绕{topic}提供了问题、方法或实证线索。",
            })

        dated = [p for p in profiles if isinstance(p.get("year"), int)]
        evolution: List[Dict[str, Any]] = []
        if len(dated) >= 2:
            earliest = min(dated, key=lambda p: p["year"])
            latest = max(dated, key=lambda p: p["year"])
            if earliest["year"] != latest["year"]:
                evolution.append({
                    "from": f"{earliest['year']}：{earliest.get('title') or '早期研究'}",
                    "to": f"{latest['year']}：{latest.get('title') or '近期研究'}",
                    "description": "按论文发表年份整理的研究时间线；具体技术演进关系需阅读全文确认。",
                })

        limitations = [
            str(p.get("limitation") or "").strip() for p in profiles
            if str(p.get("limitation") or "").strip()
            and "未报告局限" not in str(p.get("limitation") or "")
        ]
        gap_reason = "；".join(limitations[:3]) or "现有摘要未充分报告局限，需要全文证据补充。"
        query_terms = " ".join(keywords[:3]) or " ".join(titles[:1])
        gaps = [{
            "gap": "现有证据与局限仍需全文验证",
            "why": gap_reason,
            "suggested_query": f"{query_terms} limitations evaluation".strip(),
        }]
        result: Dict[str, Any] = {
            "consensus": consensus,
            "conflicts": [],
            "evolution": evolution,
            "gaps": gaps,
            "summary": summary,
            "_fallback": True,
        }
        if error:
            result["_error"] = error
        return result

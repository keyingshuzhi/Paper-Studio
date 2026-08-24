"""论文智能摘要器（V2.0：问题/方法/贡献/局限）。

能力：
1. summarize: 单篇论文 → 结构化摘要 {problem, method, contribution, limitation, keywords}
2. summarize_many: 批量摘要（可并行），单篇失败不影响整体
3. 无 LLM 配置时降级：返回基于摘要(abstract)的简化摘要
4. 长文本自动截断，控制 token 成本
"""

from __future__ import annotations

import ast
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from .json_utils import parse_json_block
from .llm import LLMClient, LLMError

_SUMMARY_SYSTEM = """\
你是一位严谨的学术文献阅读专家。阅读给定论文文本，提炼出结构化摘要，输出严格 JSON，\
不要输出 JSON 以外的内容。

JSON 字段：
- "title": 论文标题
- "problem": 该论文要解决的研究问题（1-2 句）
- "method": 提出的方法/模型/核心思路（2-4 句）
- "contribution": 主要贡献与创新点（2-3 条，用分号分隔）
- "limitation": 局限性（1-3 条；论文未明说时基于上下文合理推断，并注明"（推断）"）
- "keywords": 3-6 个关键词数组

要求：忠实原文，不编造；用词专业简洁；中文回答。"""

#: 正文参与摘要的最大字符数（成本控制）
DEFAULT_MAX_CHARS = 16000


class PaperSummarizer:
    """论文结构化摘要器。"""

    def __init__(self, llm: Optional[LLMClient] = None,
                 max_chars: int = DEFAULT_MAX_CHARS) -> None:
        self.llm = llm or LLMClient()
        self.max_chars = max_chars

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.llm.available

    def summarize(self, text: str, title: Optional[str] = None,
                  abstract: Optional[str] = None,
                  temperature: float = 0.2) -> Dict[str, Any]:
        """摘要单篇论文。

        Args:
            text: 论文正文（清洗后的纯文本）。
            title: 论文标题（有则提供，提升准确性）。
            abstract: 论文摘要（有则提供，优先于正文）。

        Returns:
            {"title", "problem", "method", "contribution",
             "limitation", "keywords"}
        """
        if not self.llm.available:
            return self._fallback_summary(text, title, abstract)

        user = self._build_prompt(title, abstract, text)
        raw = self.llm.chat(user=user, system=_SUMMARY_SYSTEM,
                            json_mode=True, temperature=temperature,
                            max_tokens=1024)
        data = parse_json_block(raw)
        # 本地模型尤其容易在 JSON 合法时漏掉部分字段。不要把空字段直接
        # 渲染成报告中的“—”，而是以摘要/正文做保守的补全。
        model_summary = {
            "title": self._normalize_text(data.get("title") or title),
            "problem": self._normalize_text(data.get("problem")),
            "method": self._normalize_text(data.get("method")),
            "contribution": self._normalize_text(data.get("contribution")),
            "limitation": self._normalize_text(data.get("limitation")),
            "keywords": self._clean_keywords(data.get("keywords")),
        }
        return self._complete_summary(model_summary, text, title, abstract)

    # ------------------------------------------------------------------
    def summarize_many(self, items: List[Dict[str, Any]],
                       max_workers: int = 2) -> List[Dict[str, Any]]:
        """批量摘要。

        Args:
            items: [{"title": ..., "abstract": ..., "text": ...}, ...]
            max_workers: 并发数（注意 LLM 限速，默认 2）。

        Returns:
            与 items 等长的结果列表：
            {"ok": bool, "summary": {...} | None, "error": str | None}
        """
        results: List[Optional[Dict[str, Any]]] = [None] * len(items)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._summarize_one, i, item): i
                for i, item in enumerate(items)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as err:  # noqa: BLE001 - 单篇失败不影响整体
                    item = items[idx]
                    results[idx] = {
                        "ok": True,
                        "summary": self._fallback_summary(
                            item.get("text") or "", item.get("title"),
                            item.get("abstract")),
                        "error": str(err),
                        "fallback": True,
                    }
        return results

    def _summarize_one(self, idx: int,
                       item: Dict[str, Any]) -> Dict[str, Any]:
        try:
            summary = self.summarize(
                text=item.get("text") or "",
                title=item.get("title"),
                abstract=item.get("abstract"),
            )
            return {"ok": True, "summary": summary, "error": None}
        except LLMError as err:
            # 云端空响应、超时或连续 JSON 失败时，仍保留基于摘要/正文的
            # 本地降级结果，避免 20 篇文献全部因为一次服务波动失效。
            return {"ok": True,
                    "summary": self._fallback_summary(
                        item.get("text") or "", item.get("title"),
                        item.get("abstract")),
                    "error": str(err), "fallback": True}
        except ValueError as err:
            return {"ok": True,
                    "summary": self._fallback_summary(
                        item.get("text") or "", item.get("title"),
                        item.get("abstract")),
                    "error": str(err), "fallback": True}

    # ------------------------------------------------------------------
    def _build_prompt(self, title: Optional[str], abstract: Optional[str],
                      text: str) -> str:
        parts: List[str] = []
        if title:
            parts.append(f"标题：{title}")
        if abstract:
            parts.append(f"摘要：{abstract[:2000]}")
        body = (text or "").strip()
        if body:
            parts.append(f"正文（截断至前 {self.max_chars} 字符）：\n"
                         f"{body[:self.max_chars]}")
        if not parts:
            raise ValueError("论文文本为空，无法摘要")
        return "\n\n".join(parts) + "\n\n请输出该论文的结构化摘要 JSON。"

    @staticmethod
    def _clean_keywords(value: Any) -> List[str]:
        if isinstance(value, str):
            values = re.split(r"[,，、;；|]", value)
        elif isinstance(value, (list, tuple, set)):
            values = list(value)
        else:
            return []
        cleaned: List[str] = []
        for item in values:
            keyword = PaperSummarizer._normalize_text(item)
            if keyword and keyword not in cleaned:
                cleaned.append(keyword)
        return cleaned[:6]

    @staticmethod
    def _normalize_text(value: Any) -> str:
        """把本地模型常见的数组/数字等字段统一为可读文本。"""
        if value is None:
            return ""
        if isinstance(value, (list, tuple, set)):
            parts = [PaperSummarizer._normalize_text(item) for item in value]
            return "；".join(part for part in parts if part)
        if isinstance(value, dict):
            parts = []
            for key, item in value.items():
                text = PaperSummarizer._normalize_text(item)
                if text:
                    parts.append(f"{key}：{text}")
            return "；".join(parts)
        text = re.sub(r"\s+", " ", str(value)).strip()
        # 兼容旧记忆中由 str(list) 产生的 "['贡献1', '贡献2']"。
        if len(text) <= 4000 and text.startswith("[") and text.endswith("]"):
            try:
                parsed = ast.literal_eval(text)
                if isinstance(parsed, (list, tuple, set)):
                    return PaperSummarizer._normalize_text(parsed)
            except (SyntaxError, ValueError):
                pass
        return text

    @classmethod
    def complete_existing(cls, summary: Optional[Dict[str, Any]], *,
                          text: str = "", title: Optional[str] = None,
                          abstract: Optional[str] = None) -> Dict[str, Any]:
        """修复记忆或旧任务中字段不全的摘要，统一到当前完整结构。"""
        source = summary if isinstance(summary, dict) else {}
        normalized = {
            "title": cls._normalize_text(source.get("title") or title),
            "problem": cls._normalize_text(source.get("problem")),
            "method": cls._normalize_text(source.get("method")),
            "contribution": cls._normalize_text(source.get("contribution")),
            "limitation": cls._normalize_text(source.get("limitation")),
            "keywords": cls._clean_keywords(source.get("keywords")),
        }
        completed = cls._complete_summary(
            normalized, text=text, title=title, abstract=abstract)
        if source.get("_fallback"):
            completed["_fallback"] = True
        return completed

    @classmethod
    def _complete_summary(cls, summary: Dict[str, Any], text: str,
                          title: Optional[str], abstract: Optional[str]
                          ) -> Dict[str, Any]:
        """用可验证的原文线索补齐模型遗漏的结构化字段。"""
        fallback = cls._fallback_summary(text, title, abstract)
        completed = dict(summary)
        for field in ("problem", "method", "contribution", "limitation"):
            completed[field] = cls._normalize_text(completed.get(field))
            if cls._is_missing_value(completed.get(field)):
                completed[field] = fallback[field]
        completed["keywords"] = cls._clean_keywords(completed.get("keywords"))
        if not completed["keywords"]:
            completed["keywords"] = fallback["keywords"]
        completed["title"] = cls._normalize_text(completed.get("title"))
        if cls._is_missing_value(completed["title"]):
            completed["title"] = fallback["title"]
        return completed

    @staticmethod
    def _is_missing_value(value: Any) -> bool:
        """判断模型常见的空占位，避免把它们显示为有效摘要。"""
        if value is None:
            return True
        normalized = str(value).strip().lower()
        return normalized in {
            "", "-", "—", "n/a", "na", "null", "none", "暂无", "无",
            "未提供", "未提及", "unknown",
            "（未配置 llm，仅提供原文首段）", "(未配置 llm，仅提供原文首段)",
            "（推断）", "(推断)",
        }

    @staticmethod
    def _fallback_summary(text: str, title: Optional[str],
                          abstract: Optional[str]) -> Dict[str, Any]:
        """无可用 LLM 时，从摘要/正文保守提取四要素。

        这不是让程序臆造论文结论：没有在可用文本中找到相应证据时，明确
        告知用户需结合全文验证，确保报告每个字段都有可读、可追溯的状态。
        """
        src = (abstract or text or "").strip()
        if PaperSummarizer._is_missing_value(src):
            src = ""
        normalized = re.sub(r"\s+", " ", src)
        sentences = [
            item.strip() for item in re.split(r"(?<=[。！？.!?])\s+|\n+", normalized)
            if item.strip()
        ]

        def find_sentence(pattern: str, default: str = "") -> str:
            matched = [s for s in sentences if re.search(pattern, s, re.IGNORECASE)]
            return " ".join(matched[:2]) or default

        problem = " ".join(sentences[:2]) or "（未找到可用于提取研究问题的摘要或正文）"
        first_para = next(
            (re.sub(r"\s+", " ", p).strip() for p in (text or "").split("\n\n")
             if p.strip()), "")[:500]
        method = find_sentence(
            r"提出|方法|框架|模型|算法|通过|采用|基于|利用|构建|设计|"
            r"we\s+(propose|present|introduce|develop|use)|using|based on|"
            r"framework|method|approach|model",
            first_para or (sentences[0] if sentences else ""),
        )
        if not method:
            method = "（未能从可用摘要中可靠提取方法，建议下载全文后再提取）"
        contribution = find_sentence(
            r"贡献|创新|实现|验证|提升|降低|改进|结果|达到|优于|"
            r"demonstrate|show|achieve|improve|reduce|outperform|introduce|provide|enable",
        )
        if not contribution:
            contribution = "（摘要未提供可核验的贡献描述，建议下载全文后再提取）"
        limitation = find_sentence(
            r"局限|限制|挑战|不足|依赖|未来|仍需|未能|"
            r"however|limitation|limited|constraint|challenge|future work|require",
        )
        if not limitation:
            limitation = "（摘要未报告局限，需结合全文验证）"
        keywords = PaperSummarizer._fallback_keywords(title, normalized)
        return {
            "title": title or "未知标题",
            "problem": problem[:500],
            "method": method[:800],
            "contribution": contribution[:800],
            "limitation": limitation[:800],
            "keywords": keywords,
            "_fallback": True,
        }

    @staticmethod
    def _fallback_keywords(title: Optional[str], source: str) -> List[str]:
        """从标题优先提取可追溯关键词，避免降级报告关键词整项缺失。"""
        text = f"{title or ''} {source[:1200]}"
        stopwords = {
            "the", "and", "for", "with", "from", "into", "this", "that",
            "using", "based", "paper", "study", "research", "method", "model",
            "are", "was", "were", "have", "has", "our", "their", "through",
        }
        candidates = re.findall(r"[A-Za-z][A-Za-z0-9+._-]{2,}", text)
        result: List[str] = []
        for token in candidates:
            cleaned = token.strip("._-")
            if (not cleaned or cleaned.lower() in stopwords
                    or cleaned.lower() in {item.lower() for item in result}):
                continue
            result.append(cleaned)
            if len(result) >= 6:
                break
        if result:
            return result
        chinese = re.findall(r"[\u4e00-\u9fff]{2,10}", title or source[:120])
        for token in chinese:
            if token not in result:
                result.append(token)
            if len(result) >= 6:
                break
        return result or ["主题待全文核验"]

    def estimate_cost_chars(self, items: List[Dict[str, Any]]) -> int:
        """估算批量任务的输入字符总量（用于成本控制提示）。"""
        return sum(min(len(i.get("text") or ""), self.max_chars)
                   + min(len(i.get("abstract") or ""), 2000)
                   for i in items)

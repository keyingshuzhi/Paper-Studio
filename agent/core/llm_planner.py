"""LLM 规划器（V2.0 复杂意图解析 + V6.0 技能注入）。

职责：
1. 用 LLM 把用户自然语言解析为结构化 ResearchPlan（JSON）。
2. 解析失败 / 未配置 Key 时，自动降级到规则规划器（Planner）。
3. 用户显式传入的参数（overrides）始终优先于 LLM 的推断。
4. 自动加载同目录下的 SKILL.md 作为技能上下文，扩展 LLM 能力。

可解析的复杂意图示例：
- "下载近三年关于transformer在医学影像应用的论文，每来源10篇"
- "只搜arxiv的mamba论文，2023年以后，不用下载"
- "帮我汇总机器学习的学习资料"  (注入 SKILL.md 技能)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .json_utils import parse_json_block
from .llm import LLMClient
from .planner import Planner, ResearchPlan

# Load agent/skills/SKILL.md for skill injection (if present).
# __file__ 位于 agent/core/，因此 parent.parent 才是 agent/。
_SKILL_MD_PATH = Path(__file__).resolve().parent.parent / "skills" / "SKILL.md"
_SKILL_CONTEXT: Optional[str] = None
if _SKILL_MD_PATH.exists():
    try:
        _SKILL_CONTEXT = _SKILL_MD_PATH.read_text(encoding="utf-8")
    except Exception:
        pass

_SYSTEM_PROMPT = """\
你是一个学术检索任务的规划器。你的唯一职责：把用户的自然语言请求解析成结构化的 \
JSON 检索计划。不要执行检索，不要输出 JSON 以外的任何内容。

输出 JSON 字段说明：
- "query": 纯净检索关键词（剥离命令词、语气词，保留技术术语与英文原名）
- "max_results": 每个来源的结果数量上限，1-50 的整数
- "sources": 来源白名单数组，只允许 "arxiv_search" / "scholar_search"；\
用户没指定来源或要求全部时填 null
- "download": 布尔值，用户要求下载原文/全文/PDF 时为 true
- "max_downloads": 最多下载篇数，未指定时填 null
- "report": 布尔值，默认 true（V1.0 总是生成报告）
- "year_from": 只检索该年份及以后的文献；未指定时间范围时填 null
- "skill": 如果用户请求匹配某个已注册的技能（如"学习资料汇总"），填技能名称；否则填 null
- "reason": 一句话说明你的解析依据（用于日志与调试）

示例：
用户：帮我下载近三年关于mamba状态空间模型的论文，只搜arxiv
输出：{"query": "mamba state space model", "max_results": 10, \
"sources": ["arxiv_search"], "download": true, "max_downloads": null, \
"report": true, "year_from": 2024, "skill": null, \
"reason": "近三年=距今3年"}"""

# Inject SKILL.md context into system prompt if available
if _SKILL_CONTEXT:
    _SYSTEM_PROMPT += f"""

## 已注册技能

以下技能已注入，当用户请求匹配时，在 "skill" 字段填入技能名称：

{_SKILL_CONTEXT}

当用户请求如"汇总学习资料"、"推荐学习资源"等时，设置 "skill": "学习资料汇总"。"""


class LLMPlanner(Planner):
    """LLM 驱动的任务规划器（自动降级到规则规划器）。"""

    def __init__(self, llm: Optional[LLMClient] = None,
                 fallback: Optional[Planner] = None) -> None:
        super().__init__()
        self.llm = llm or LLMClient()
        self.fallback = fallback or Planner()

    # ------------------------------------------------------------------
    @property
    def mode(self) -> str:
        """当前生效的规划模式。"""
        return "llm" if self.llm.available else "rule"

    def make_plan(self, user_input: str, **overrides: Any) -> ResearchPlan:
        """解析用户输入为执行计划（LLM 优先，失败降级规则）。"""
        # 1) LLM 解析
        if self.llm.available:
            try:
                raw = self.llm.chat(
                    user=self._build_prompt(user_input),
                    system=_SYSTEM_PROMPT,
                    json_mode=True,
                    temperature=0.0,
                )
                data = self._parse_json(raw)
                plan = self._from_llm_data(user_input, data)
                plan.extra["planner"] = "llm"
                plan.extra["llm_reason"] = data.get("reason", "")
            except Exception as err:  # noqa: BLE001 - 任何失败都降级
                print(f"[warn] LLM 规划失败，降级为规则规划: {err}")
                plan = self.fallback.make_plan(user_input)
                plan.extra["planner"] = "rule_fallback"
        else:
            print("[info] 未配置 LLM，使用规则规划器 "
                  "（配置 .env 的 LLM_API_KEY 可启用智能解析）")
            plan = self.fallback.make_plan(user_input)
            plan.extra["planner"] = "rule"

        # 2) 用户显式覆盖优先
        self._apply_overrides(plan, overrides)
        return plan

    # ------------------------------------------------------------------
    @staticmethod
    def _build_prompt(user_input: str) -> str:
        return f"用户输入：{user_input}\n\n请输出 JSON 检索计划："

    @staticmethod
    def _parse_json(raw: str) -> Dict[str, Any]:
        """健壮解析（委托给共享工具）。"""
        return parse_json_block(raw)

    def _from_llm_data(self, user_input: str,
                       data: Dict[str, Any]) -> ResearchPlan:
        """把 LLM 的 JSON 转成 ResearchPlan（带字段清洗与校验）。"""
        query = str(data.get("query") or "").strip()
        if not query:
            raise ValueError("LLM 未返回有效 query")

        sources = data.get("sources")
        if isinstance(sources, list):
            sources = [s for s in sources
                       if s in ("arxiv_search", "scholar_search")] or None
        else:
            sources = None

        plan = ResearchPlan(
            query=query,
            original_query=user_input,
            max_results=self._clamp_int(data.get("max_results"), 1, 50, 10),
            sources=sources,
            download=bool(data.get("download", False)),
            max_downloads=self._clamp_int(data.get("max_downloads"),
                                          1, 50, None),
            report=bool(data.get("report", True)),
            year_from=self._clamp_int(data.get("year_from"), 1900, 2100, None),
        )
        # Inject skill name if LLM detected a skill match
        skill = data.get("skill")
        if skill:
            plan.extra["skill"] = str(skill).strip()
        return plan

    @staticmethod
    def _clamp_int(value: Any, lo: int, hi: int,
                   default: Optional[int]) -> Optional[int]:
        """把值夹取到 [lo, hi]；非法或 None 时返回 default。"""
        if value is None:
            return default
        try:
            v = int(value)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    @staticmethod
    def _apply_overrides(plan: ResearchPlan, overrides: Dict[str, Any]) -> None:
        """显式参数覆盖（None 表示不覆盖）。"""
        if overrides.get("max_results") is not None:
            plan.max_results = overrides["max_results"]
        if overrides.get("sources") is not None:
            plan.sources = overrides["sources"]
        if overrides.get("download") is not None:
            plan.download = overrides["download"]
        if overrides.get("max_downloads") is not None:
            plan.max_downloads = overrides["max_downloads"]
        if overrides.get("report") is not None:
            plan.report = overrides["report"]
        if overrides.get("year_from") is not None:
            plan.year_from = overrides["year_from"]

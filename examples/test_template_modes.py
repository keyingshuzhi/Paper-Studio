"""四种研究模式的差异化能力测试（离线、无模型调用）。"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core.agent import ResearchAgent
from agent.core.planner import ResearchPlan
from agent.core.reporter import Reporter
from agent.core.template_insights import (
    COMPETITOR_TEMPLATE, DAILY_TEMPLATE, OPENING_TEMPLATE,
    build_single_template_insights, build_survey_insights,
    filter_daily_papers, paper_identity,
)
from agent.skills import Paper


def expect(name: str, condition: bool) -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        raise SystemExit(f"断言失败: {name}")


def summaries(papers: list[Paper]) -> list[dict]:
    return [{"ok": True, "summary": {
        "title": paper.title,
        "problem": f"{paper.title} 要解决的研究问题",
        "method": f"{paper.title} 的方法路线",
        "contribution": f"{paper.title} 的可验证贡献",
        "limitation": f"{paper.title} 的适用边界",
        "keywords": ["agent", "evaluation"],
    }} for paper in papers]


def main() -> None:
    today = date.today()
    recent = Paper(
        title="Recent Agent Study", url="https://example.test/recent",
        source="arxiv_search", year=today.year, abstract="recent evidence",
        extra={"published_date": (today - timedelta(days=2)).isoformat()})
    seen = Paper(
        title="Seen Agent Study", url="https://example.test/seen",
        source="arxiv_search", year=today.year, abstract="seen evidence",
        extra={"published_date": today.isoformat()})
    old = Paper(
        title="Old Agent Study", url="https://example.test/old",
        source="scholar_search", year=today.year - 2, abstract="old evidence",
        extra={"published_date": (today - timedelta(days=500)).isoformat()})
    approximate = Paper(
        title="Current Year Candidate", url="https://example.test/year-only",
        source="scholar_search", year=today.year, abstract="candidate")

    print("== 用例 1：每日追踪按日期与历史记录做增量筛选 ==")
    fresh, stats = filter_daily_papers(
        [old, seen, recent, approximate], 7, [paper_identity(seen.title)])
    expect("只保留未读且处于窗口内的候选",
           [paper.title for paper in fresh] ==
           ["Recent Agent Study", "Current Year Candidate"])
    expect("区分精确日期与年份候选",
           stats["exact_date_count"] == 1 and
           stats["approximate_date_count"] == 1)
    expect("记录历史去重和窗口外数量",
           stats["already_seen_count"] == 1 and
           stats["outside_window_count"] == 1)

    print("== 用例 1b：每日追踪扩大候选池后限制摘要数量 ==")
    daily_calls: list[dict] = []

    class DailySearch:
        def run(self, **kwargs):
            daily_calls.append(kwargs)
            return [
                Paper(
                    title=f"Daily Candidate {index}",
                    url=f"https://source-{index}.test/paper",
                    source="arxiv_search", year=today.year,
                    abstract=f"evidence {index}",
                    extra={"published_date": (
                        today - timedelta(days=index)).isoformat()})
                for index in range(4)
            ]

    daily_agent = ResearchAgent(search_plugin=DailySearch())
    daily_result = daily_agent.run(
        "agent workflow", max_results=2, days_back=7,
        template=DAILY_TEMPLATE, summarize=False, analyze=False, report=False)
    expect("先拉取三倍候选避免旧结果挤占窗口",
           daily_calls[0]["max_results"] == 6)
    expect("只把设置数量的新文献交给后续摘要",
           len(daily_result["papers"]) == 2
           and daily_result["daily_stats"]["matched_count"] == 4
           and daily_result["daily_stats"]["deferred_count"] == 2)

    analysis = {
        "summary": "领域存在明确机会，但需要统一评测。",
        "consensus": [{"statement": "可复现评测是共同方向"}],
        "conflicts": [{"statement_a": "强调速度", "statement_b": "强调质量"}],
        "gaps": [{"gap": "缺少统一基准", "why": "结果不可直接比较",
                  "suggested_query": "agent benchmark reproducibility"}],
    }

    print("== 用例 2：开题模式输出决策问题、路线与风险 ==")
    opening = build_single_template_insights(
        OPENING_TEMPLATE, "研究 Agent 可复现评测", [recent, approximate],
        summaries([recent, approximate]), analysis)
    expect("开题包含明确选题判断", bool(opening.get("verdict")))
    expect("知识空白转换成可验证研究问题",
           "统一基准" in opening["research_questions"][0]["question"])
    expect("开题包含方法路线和风险",
           bool(opening["method_routes"]) and bool(opening["risks"]))

    print("== 用例 3：竞品模式按自定义维度形成矩阵 ==")
    competitor = build_single_template_insights(
        COMPETITOR_TEMPLATE, "竞品分析", [recent, approximate],
        summaries([recent, approximate]), analysis,
        compare_dimensions=["方法路线", "核心贡献", "已知局限"])
    expect("保留三项自定义维度", len(competitor["dimensions"]) == 3)
    expect("每篇论文都有同维度单元格",
           len(competitor["rows"]) == 2 and
           all(len(row["cells"]) == 3 for row in competitor["rows"]))
    expect("生成逐篇优势与取舍", len(competitor["recommendations"]) == 2)

    print("== 用例 4：仅标题竞品可补齐公开元数据 ==")
    class Search:
        def run(self, **_kwargs):
            return [Paper(
                title="Paper Studio Agent", url="https://example.test/full",
                source="scholar_search", year=today.year,
                abstract="resolved abstract", doi="10.1/resolved")]

    agent = ResearchAgent(search_plugin=Search())
    hydrated = agent._resolve_existing_papers(
        [Paper(title="Paper Studio Agent", url="", source="manual")],
        sources=None, checkpoint=None, event_callback=None)
    expect("标题匹配后补齐摘要与 DOI",
           hydrated[0].abstract == "resolved abstract" and
           hydrated[0].doi == "10.1/resolved")

    print("== 用例 5：每日、开题、竞品报告结构互不相同 ==")
    reporter = Reporter()
    plan = ResearchPlan(query="agent", original_query="agent")
    opening_md = reporter.render(
        plan, [recent, approximate], summaries=summaries([recent, approximate]),
        analysis={**analysis, "template_insights": opening})
    competitor_md = reporter.render(
        plan, [recent, approximate], summaries=summaries([recent, approximate]),
        analysis={**analysis, "template_insights": competitor})
    daily = build_single_template_insights(
        DAILY_TEMPLATE, "agent", fresh, summaries(fresh), None,
        daily_stats=stats)
    daily_md = reporter.render(
        plan, fresh, summaries=summaries(fresh),
        analysis={"template_insights": daily})
    expect("开题报告有决策面板", opening_md.startswith("# 开题调研报告")
           and "## 开题决策面板" in opening_md)
    expect("竞品报告有对照矩阵", competitor_md.startswith("# 竞品论文分析报告")
           and "## 竞品论文对照矩阵" in competitor_md)
    expect("日报只呈现增量追踪", daily_md.startswith("# 每日文献追踪")
           and "历史去重" in daily_md and "新增证据信号" in daily_md)

    print("== 用例 6：综述输出证据覆盖、方法谱系与研究空白 ==")
    survey = build_survey_insights([{
        "round": 1, "query": "agent", "origin": "user",
        "papers": [recent, approximate],
        "summaries": summaries([recent, approximate]), "analysis": analysis,
    }], [recent, approximate])
    deep_meta = {
        "root_query": "agent", "started_at": "now", "rounds": 1,
        "queries": 1, "papers_raw": 2, "papers_dedup": 2,
        "template_insights": survey,
    }
    survey_md = reporter.render_deep(
        deep_meta, [], [recent, approximate])
    expect("综述报告使用专属标题与证据地图",
           survey_md.startswith("# 系统综述报告") and
           "## 系统综述证据地图" in survey_md)
    expect("综述包含来源分布、方法谱系与空白",
           all(text in survey_md for text in
               ("来源分布", "代表方法谱系", "优先研究空白")))

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

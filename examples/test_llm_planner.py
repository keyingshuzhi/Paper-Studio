"""LLM 规划器 Mock 测试（无需真实 API Key）。

覆盖路径：
1. LLM 返回合法 JSON → 正确解析为 ResearchPlan
2. LLM 返回带代码块/多余文字的脏输出 → 健壮解析
3. LLM 返回非法 JSON → 自动降级规则规划器
4. 用户显式参数覆盖 LLM 推断
5. ResearchAgent 集成：LLM 规划 → 年份过滤生效
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core import LLMPlanner, Planner, ResearchAgent
from agent.core.llm import LLMClient
from agent.core.llm_planner import _SKILL_CONTEXT
from agent.skills import Paper


class FakeLLM(LLMClient):
    """模拟 LLM：按脚本返回预设内容。"""

    def __init__(self, replies) -> None:
        self._replies = list(replies)
        self.calls = []

    @property
    def available(self) -> bool:
        """模拟：永远可用。"""
        return True

    def chat(self, user, system=None, temperature=0.0,
             json_mode=False, max_tokens=None) -> str:
        self.calls.append(user)
        if not self._replies:
            raise RuntimeError("FakeLLM 回复耗尽")
        return self._replies.pop(0)


class StaticSearch:
    """隔离真实学术 API，使规划器测试保持完全离线。"""

    def run(self, **_kwargs):
        return [
            Paper(title="Recent", url="https://example.test/recent",
                  source="test", year=2024),
            Paper(title="Old", url="https://example.test/old",
                  source="test", year=2020),
        ]


def expect(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def main() -> None:
    print("== 用例 0：项目级 Skill 注入 ==")
    expect("读取 agent/skills/SKILL.md",
           bool(_SKILL_CONTEXT and "学习资料汇总" in _SKILL_CONTEXT))
    skill_plan = Planner().make_plan("帮我汇总机器学习的学习资料")
    expect("规则模式也能识别 Skill",
           skill_plan.extra.get("skill") == "学习资料汇总")

    print("== 用例 1：合法 JSON ==")
    fake = FakeLLM(['{"query": "mamba state space model", "max_results": 8, '
                    '"sources": ["arxiv_search"], "download": true, '
                    '"max_downloads": 3, "report": true, "year_from": 2023, '
                    '"reason": "近三年"}'])
    plan = LLMPlanner(llm=fake).make_plan("帮我下载近三年mamba论文，只搜arxiv")
    expect("query 提取", plan.query == "mamba state space model")
    expect("max_results", plan.max_results == 8)
    expect("sources 白名单", plan.sources == ["arxiv_search"])
    expect("download", plan.download is True)
    expect("max_downloads", plan.max_downloads == 3)
    expect("year_from", plan.year_from == 2023)
    expect("planner 模式", plan.extra.get("planner") == "llm")

    print("== 用例 2：脏输出（代码块 + 前后废话）==")
    fake = FakeLLM(['好的，以下是计划：\n```json\n{"query": "llm agent", '
                    '"max_results": 5, "sources": null, "download": false}\n```\n'
                    '请查收！'])
    plan = LLMPlanner(llm=fake).make_plan("找llm agent的资料")
    expect("脏 JSON 中提取 query", plan.query == "llm agent")
    expect("sources null → None", plan.sources is None)
    expect("download false", plan.download is False)

    print("== 用例 3：非法 JSON → 降级规则规划 ==")
    fake = FakeLLM(["这不是JSON，是一堆废话"])
    planner = LLMPlanner(llm=fake)
    plan = planner.make_plan("下载attention论文")
    expect("降级为规则规划", plan.extra.get("planner") == "rule_fallback")
    expect("规则提取关键词", "attention" in plan.query)
    expect("规则识别下载意图", plan.download is True)

    print("== 用例 4：显式参数覆盖 LLM ==")
    fake = FakeLLM(['{"query": "transformer", "max_results": 20, '
                    '"download": true, "max_downloads": 5}'])
    plan = LLMPlanner(llm=fake).make_plan(
        "transformer论文", max_results=3, download=False)
    expect("max_results 被覆盖为 3", plan.max_results == 3)
    expect("download 被覆盖为 False", plan.download is False)

    print("== 用例 5：ResearchAgent 集成（年份过滤）==")
    fake = FakeLLM(['{"query": "attention", "max_results": 5, '
                    '"year_from": 2023, "download": false}'])
    agent = ResearchAgent(planner=LLMPlanner(llm=fake),
                          search_plugin=StaticSearch())
    result = agent.run("找2023年以后的attention论文", report=False)
    papers = result["papers"]
    expect("年份过滤后全部 >= 2023",
           all((p.year or 0) >= 2023 for p in papers))
    expect("模式为 llm",
           result["plan"].extra.get("planner") == "llm")

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

"""多主题对比研究测试（FakeAgent + FakeLLM，无网络）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core import MultiTopicComparator
from agent.core.llm import LLMClient
from agent.skills import Paper


class FakeAgent:
    """模拟每主题的单轮研究。"""

    def __init__(self, papers_per_topic: int = 2) -> None:
        self.papers_per_topic = papers_per_topic
        self.calls = []

    def run(self, query, *, max_results=5, summarize=True, analyze=True,
            report=False, **kwargs):
        self.calls.append(query)
        return {
            "papers": [Paper(title=f"{query} Paper {i}",
                             url=f"http://x/{query}/{i}",
                             source="arxiv_search", year=2024)
                       for i in range(self.papers_per_topic)],
            "summaries": [{"ok": True, "summary": {"title": query}}],
            "analysis": {
                "consensus": [{"topic": "t", "papers": [1],
                               "statement": f"{query} 的共识"}],
                "conflicts": [], "evolution": [],
                "gaps": [{"gap": f"{query} 的盲点", "why": "重要",
                          "suggested_query": f"{query} gap"}],
                "summary": f"{query} 领域态势",
            },
            "report_path": None,
        }


class FakeLLM(LLMClient):
    @property
    def available(self) -> bool:
        return True

    def chat(self, user, system=None, temperature=0.0,
             json_mode=False, max_tokens=None) -> str:
        if "对比" not in (system or ""):
            raise RuntimeError("非对比调用")
        return json.dumps({
            "overview": "两个主题共享底层技术栈",
            "shared_themes": [{"theme": "注意力机制",
                               "topics": ["A", "B"]}],
            "distinct_focus": [{"topic": "A", "focus": "效率优化"}],
            "overlap_papers": [{"title": "Attention Is All You Need",
                                "topics": ["A", "B"]}],
            "cross_suggestions": [{"suggestion": "把A的方法用到B",
                                   "topics": ["A", "B"],
                                   "why": "互补"}],
        }, ensure_ascii=False)


class NoKeyLLM(FakeLLM):
    @property
    def available(self) -> bool:
        return False


def expect(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def main() -> None:
    print("== 用例 1：多主题对比全流程 ==")
    comp = MultiTopicComparator(agent=FakeAgent(),
                                llm=FakeLLM())
    result = comp.compare(["A", "B"], max_results=3)
    topics = result["topics"]
    expect("两个主题都被研究", set(topics) == {"A", "B"})
    expect("每个主题有文献", topics["A"]["papers_count"] == 2)
    expect("简报含共识", topics["A"]["consensus"] == ["A 的共识"])
    expect("简报含盲点", topics["B"]["gaps"] == ["B 的盲点"])
    c = result["comparison"]
    expect("横向综合含整体态势", "底层技术栈" in c["overview"])
    expect("共享主题", c["shared_themes"][0]["topics"] == ["A", "B"])
    expect("重叠论文", "Attention" in c["overlap_papers"][0]["title"])
    expect("交叉建议", c["cross_suggestions"][0]["suggestion"].startswith("把"))
    rp = Path(result["report_path"])
    expect("对比报告已生成", rp.exists())
    text = rp.read_text(encoding="utf-8")
    expect("报告含横向综合", "## 横向综合" in text)
    expect("报告含共享主题", "### 共享主题" in text)
    expect("报告含交叉建议", "### 交叉研究建议" in text)
    expect("报告含主题简报", "## 各主题研究简报" in text)

    print("== 用例 2：主题数校验 ==")
    comp = MultiTopicComparator(agent=FakeAgent(), llm=FakeLLM())
    try:
        comp.compare(["only-one"])
        expect("单主题应报错", False)
    except ValueError:
        expect("单主题报错", True)

    print("== 用例 3：无 LLM → 本地完整横向分析 ==")
    comp = MultiTopicComparator(agent=FakeAgent(), llm=NoKeyLLM())
    result = comp.compare(["A", "B"], max_results=3)
    expect("本地综合对比已生成", result["comparison"].get("_fallback") is True)
    expect("本地整体态势不为空", bool(result["comparison"].get("overview")))
    expect("各主题侧重完整", len(result["comparison"].get("distinct_focus", [])) == 2)
    expect("交叉建议不为空", bool(result["comparison"].get("cross_suggestions")))
    expect("主题简报仍生成", len(result["topics"]) == 2)
    expect("报告仍生成", Path(result["report_path"]).exists())

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

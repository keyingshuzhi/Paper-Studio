"""跨文献分析器 Mock 测试（无需真实 API Key）。

覆盖路径：
1. 合法 JSON → 共识/分歧/演进/盲点字段正确解析
2. 空分析（无共识分歧）→ 归一化为空数组
3. 编号字段清洗（字符串编号 → int）
4. 无 LLM → _fallback 标记
5. Reporter 集成：报告含"跨文献对比与知识盲点"区块
6. ResearchAgent 集成：analyze=True 全链路
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core import (CrossPaperAnalyzer, LLMPlanner, PaperSummarizer,
                        Reporter, ResearchAgent)
from agent.core.llm import LLMClient
from agent.core.planner import ResearchPlan
from agent.skills import Paper

_PROFILES = [
    {"index": 1, "title": "GSA", "year": 2024, "source": "arxiv",
     "problem": "长上下文注意力开销大", "method": "稀疏+门控",
     "contribution": "降 FLOPs", "limitation": "需调阈值", "keywords": ["a"]},
    {"index": 2, "title": "SparseFormer", "year": 2023, "source": "arxiv",
     "problem": "长上下文注意力开销大", "method": "纯稀疏",
     "contribution": "更快", "limitation": "不稳定", "keywords": ["b"]},
]


class FakeLLM(LLMClient):
    """模拟 LLM。"""

    def __init__(self, replies) -> None:
        self._replies = list(replies)
        self.calls = []

    @property
    def available(self) -> bool:
        return True

    def chat(self, user, system=None, temperature=0.0,
             json_mode=False, max_tokens=None) -> str:
        self.calls.append(user)
        if not self._replies:
            raise RuntimeError("FakeLLM 回复耗尽")
        return self._replies.pop(0)


class NoKeyLLM(FakeLLM):
    @property
    def available(self) -> bool:
        return False


class StaticSearch:
    def run(self, **_):
        return [Paper(title="GSA", url="https://example.test/gsa.pdf",
                      source="test", year=2024,
                      abstract="Gated sparse attention for long context.")]


def expect(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def main() -> None:
    print("== 用例 1：合法 JSON 解析 ==")
    fake = FakeLLM(["""{
      "consensus": [
        {"topic": "注意力开销", "papers": [1, 2], "statement": "都是主要瓶颈"}
      ],
      "conflicts": [
        {"topic": "稀疏策略", "papers_a": [1], "statement_a": "门控稀疏更稳",
         "papers_b": [2], "statement_b": "纯稀疏更快"}
      ],
      "evolution": [
        {"from": "纯稀疏", "to": "门控稀疏", "description": "引入门控机制"}
      ],
      "gaps": [
        {"gap": "缺少统一基准", "why": "难以横向对比",
         "suggested_query": "sparse attention benchmark"}
      ],
      "summary": "领域处于早期，方法百花齐放"
    }"""])
    out = CrossPaperAnalyzer(llm=fake).analyze(_PROFILES)
    expect("共识 1 条", len(out["consensus"]) == 1)
    expect("共识引用论文", out["consensus"][0]["papers"] == [1, 2])
    expect("分歧双方", out["conflicts"][0]["papers_a"] == [1]
           and out["conflicts"][0]["papers_b"] == [2])
    expect("演进路径", out["evolution"][0]["from"] == "纯稀疏")
    expect("盲点建议查询", "sparse attention benchmark"
           in out["gaps"][0]["suggested_query"])
    expect("领域态势", "早期" in out["summary"])
    expect("prompt 含画像", "GSA" in fake.calls[0] and "SparseFormer" in fake.calls[0])

    print("== 用例 2：空分析归一化 ==")
    fake = FakeLLM(['{"consensus": [], "conflicts": [], "evolution": [], '
                    '"gaps": [], "summary": ""}'])
    out = CrossPaperAnalyzer(llm=fake).analyze(_PROFILES)
    expect("各字段为空数组", all(out[k] == [] for k in
                                 ("consensus", "conflicts",
                                  "evolution", "gaps")))

    print("== 用例 3：编号字段清洗 ==")
    fake = FakeLLM(['{"consensus": [{"topic": "t", "papers": ["1", "2"], '
                    '"statement": "s"}], "conflicts": [], "evolution": [], '
                    '"gaps": [], "summary": ""}'])
    out = CrossPaperAnalyzer(llm=fake).analyze(_PROFILES)
    expect("字符串编号转 int", out["consensus"][0]["papers"] == [1, 2])

    print("== 用例 4：无 LLM 降级 ==")
    out = CrossPaperAnalyzer(llm=NoKeyLLM([])).analyze(_PROFILES)
    expect("_fallback 标记", out.get("_fallback") is True)
    expect("降级领域态势不为空", bool(out.get("summary")))
    expect("降级盲点不为空", bool(out.get("gaps")))

    print("== 用例 4b：空 JSON 输出降级而不中断 ==")
    out = CrossPaperAnalyzer(llm=FakeLLM([""])).analyze(_PROFILES)
    expect("空输出返回降级结果", out.get("_fallback") is True)
    expect("降级结果保留原因", bool(out.get("_error")))
    expect("空输出仍有可读分析", bool(out.get("summary"))
           and bool(out.get("consensus")) and bool(out.get("gaps")))

    print("== 用例 5：Reporter 集成 ==")
    plan = ResearchPlan(query="gsa", original_query="gsa")
    analysis = {
        "consensus": [{"topic": "共识话题", "papers": [1, 2],
                       "statement": "大家一致认为"}],
        "conflicts": [],
        "evolution": [],
        "gaps": [{"gap": "缺口", "why": "重要",
                  "suggested_query": "benchmark"}],
        "summary": "领域态势总结",
    }
    md = Reporter().render(plan, [], None, None, analysis)
    expect("报告含分析区块", "## 跨文献对比与知识盲点" in md)
    expect("报告含共识点", "### 共识点" in md)
    expect("报告含盲点", "### 知识盲点" in md)
    expect("报告含建议查询", "benchmark" in md)

    print("== 用例 6：ResearchAgent 集成 ==")
    fake = FakeLLM([
        # 规划器
        '{"query": "gsa", "max_results": 1, "download": false, '
        '"report": false, "year_from": null}',
        # 分析器（summarize 未开，走元数据画像）
        '{"consensus": [{"topic": "t", "papers": [1], "statement": "s"}], '
        '"conflicts": [], "evolution": [], "gaps": [], "summary": "x"}',
    ])
    agent = ResearchAgent(
        planner=LLMPlanner(llm=fake),
        summarizer=PaperSummarizer(llm=NoKeyLLM([])),
        analyzer=CrossPaperAnalyzer(llm=fake),
        search_plugin=StaticSearch(),
    )
    result = agent.run("找gsa论文", analyze=True,
                       max_results=1, sources=["arxiv_search"])
    expect("分析已执行", result["analysis"] is not None)
    expect("分析含共识", len(result["analysis"].get("consensus", [])) == 1)

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

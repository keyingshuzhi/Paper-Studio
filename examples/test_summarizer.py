"""论文智能摘要器 Mock 测试（无需真实 API Key）。

覆盖路径：
1. 单篇摘要：合法 JSON → 四要素字段正确
2. 脏输出（代码块包裹）→ 健壮解析
3. 批量摘要：单篇失败自动降级且不影响整体
4. 无 LLM 时降级：基于摘要文本的简化摘要
5. Reporter 集成：报告包含"文献智能摘要"区块
6. ResearchAgent 集成：summarize=True 全链路
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core import LLMPlanner, PaperSummarizer, Reporter, ResearchAgent
from agent.core.llm import LLMClient
from agent.core.planner import ResearchPlan
from agent.skills import Paper

_SAMPLE_TEXT = (
    "We propose Gated Sparse Attention (GSA), an architecture that combines "
    "sparse attention with gated mechanisms to reduce computational cost while "
    "maintaining training stability for long-context language models. "
    "Experiments show our method achieves comparable accuracy with lower FLOPs. "
    "One limitation is that our approach requires careful tuning of the sparsity "
    "threshold."
)


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
    """模拟未配置 Key 的 LLM。"""

    @property
    def available(self) -> bool:
        return False


class StaticSearch:
    def run(self, **_):
        return [Paper(title="GSA", url="https://example.test/gsa.pdf",
                      source="test", year=2024, abstract=_SAMPLE_TEXT)]


def expect(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def main() -> None:
    print("== 用例 1：单篇摘要（合法 JSON）==")
    fake = FakeLLM(['{"title": "GSA", "problem": "长上下文注意力计算开销大", '
                    '"method": "稀疏注意力+门控机制", '
                    '"contribution": "降低 FLOPs；保持训练稳定", '
                    '"limitation": "稀疏阈值需精细调参", '
                    '"keywords": ["attention", "sparse", "long-context"]}'])
    sm = PaperSummarizer(llm=fake)
    out = sm.summarize(_SAMPLE_TEXT, title="GSA Paper",
                       abstract="Abstract here")
    expect("问题字段", "计算开销" in out["problem"])
    expect("方法字段", "门控" in out["method"])
    expect("贡献字段", "FLOPs" in out["contribution"])
    expect("局限字段", "调参" in out["limitation"])
    expect("关键词数组", out["keywords"] ==
           ["attention", "sparse", "long-context"])
    expect("标题回填", out["title"] == "GSA")

    print("== 用例 2：脏输出（代码块）==")
    fake = FakeLLM(['```json\n{"problem": "A问题", "method": "B方法", '
                    '"contribution": "C贡献", "limitation": "D局限", '
                    '"keywords": []}\n``` 以上是我的回答'])
    out = PaperSummarizer(llm=fake).summarize("text")
    expect("代码块中解析问题", out["problem"] == "A问题")
    expect("代码块中解析方法", out["method"] == "B方法")

    print("== 用例 3：批量摘要（单篇失败自动降级）==")
    fake = FakeLLM([
        '{"problem": "P1", "method": "M1", "contribution": "C1", '
        '"limitation": "L1", "keywords": []}',
        "这不是JSON",
    ])
    results = PaperSummarizer(llm=fake).summarize_many(
        [{"title": "A", "text": "textA"},
         {"title": "B", "text": "textB"}])
    expect("第一篇成功", results[0]["ok"] is True)
    expect("第二篇降级后仍成功", results[1]["ok"] is True)
    expect("降级带原因", bool(results[1]["error"]))
    expect("降级四要素完整", all(results[1]["summary"].get(key) for key in
                                 ("problem", "method", "contribution",
                                  "limitation", "keywords")))

    print("== 用例 4：无 LLM 降级摘要 ==")
    out = PaperSummarizer(llm=NoKeyLLM([])).summarize(
        _SAMPLE_TEXT, title="GSA", abstract="我们提出门控稀疏注意力方法。")
    expect("降级标记", out.get("_fallback") is True)
    expect("降级仍有问题文本", "门控稀疏注意力" in out["problem"])
    expect("降级方法不为空", bool(out["method"]))
    expect("降级贡献不为空", bool(out["contribution"]))
    expect("降级局限不为空", bool(out["limitation"]))
    expect("降级关键词不为空", bool(out["keywords"]))

    print("== 用例 5：本地模型部分 JSON 自动补全 ==")
    fake = FakeLLM(['{"problem": "长上下文建模成本高", "keywords": ["attention"]}'])
    out = PaperSummarizer(llm=fake).summarize(
        _SAMPLE_TEXT, title="GSA", abstract=_SAMPLE_TEXT)
    expect("保留模型问题", out["problem"] == "长上下文建模成本高")
    expect("缺失方法已补全", bool(out["method"]))
    expect("缺失贡献已补全", bool(out["contribution"]))
    expect("缺失局限已补全", bool(out["limitation"]))

    print("== 用例 5b：数组字段统一为报告文本 ==")
    fake = FakeLLM(['{"problem": "P", "method": ["M1", "M2"], '
                    '"contribution": ["C1", "C2"], "limitation": ["L1"], '
                    '"keywords": "k1, k2"}'])
    out = PaperSummarizer(llm=fake).summarize(_SAMPLE_TEXT, title="GSA")
    expect("方法数组无 Python 方括号", out["method"] == "M1；M2")
    expect("贡献数组使用中文分号", out["contribution"] == "C1；C2")
    expect("关键词字符串转数组", out["keywords"] == ["k1", "k2"])

    print("== 用例 6：Reporter 集成 ==")
    plan = ResearchPlan(query="gsa", original_query="gsa")
    papers = []
    summaries = [{"ok": True, "summary": {
        "title": "GSA", "problem": "P", "method": "M",
        "contribution": "C", "limitation": "L", "keywords": ["k1"]}}]
    md = Reporter().render(plan, papers, None, summaries)
    expect("报告含摘要区块", "## 文献智能摘要" in md)
    expect("报告含四要素", all(x in md for x in
                              ["**问题**", "**方法**", "**贡献**", "**局限**"]))
    legacy = [{"ok": True, "summary": {
        "title": "旧摘要", "problem": "—", "method": "",
        "contribution": [], "limitation": None, "keywords": []}}]
    legacy_md = Reporter().render(plan, papers, None, legacy)
    expect("报告最终兜底不显示空白横线", "：—" not in legacy_md)
    expect("报告最终兜底始终显示关键词", "**关键词**" in legacy_md)

    print("== 用例 7：ResearchAgent 集成 ==")
    fake = FakeLLM([
        # 规划器回复
        '{"query": "gsa", "max_results": 1, "download": false, '
        '"report": true, "year_from": null}',
        # 摘要器回复
        '{"title": "GSA", "problem": "P", "method": "M", '
        '"contribution": "C", "limitation": "L", "keywords": []}',
    ])
    agent = ResearchAgent(planner=LLMPlanner(llm=fake),
                          summarizer=PaperSummarizer(llm=fake),
                          search_plugin=StaticSearch())
    # 确定性场景：仅 arXiv 单来源、单结果，摘要器只需回复一次
    result = agent.run("找gsa论文", summarize=True, report=False,
                       max_results=1, sources=["arxiv_search"])
    expect("摘要已执行", result["summaries"] is not None)
    expect("摘要全部成功",
           all(r.get("ok") for r in result["summaries"]))

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

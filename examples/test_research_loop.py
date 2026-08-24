"""V3.0 深度研究闭环 Mock 测试（无需真实 API Key）。

覆盖路径：
1. 两轮闭环：Round1 盲点 → 自动触发 Round2 检索
2. 预算控制：max_queries 上限生效
3. 无盲点：单轮结束
4. 无 LLM：降级单轮，仍产出报告
5. 深度报告：含研究路径 / 汇总清单 / 各轮详情
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core import (CrossPaperAnalyzer, LLMPlanner, PaperSummarizer,
                        Reporter, ResearchAgent, ResearchLoop)
from agent.core.llm import LLMClient
from agent.skills import Paper


class FakeLLM(LLMClient):
    """按 system prompt 内容分发的假 LLM（对并发摘要安全）。"""

    def __init__(self, analysis_replies) -> None:
        self._analysis_replies = list(analysis_replies)

    @property
    def available(self) -> bool:
        return True

    def chat(self, user, system=None, temperature=0.0,
             json_mode=False, max_tokens=None) -> str:
        sys_text = system or ""
        if "规划器" in sys_text:
            # 回显用户输入作为 query，保证真实检索可跑
            m = re.search(r"用户输入：(.+)", user)
            q = m.group(1).strip() if m else "test"
            return json.dumps({
                "query": q, "max_results": 5, "sources": ["arxiv_search"],
                "download": False, "max_downloads": None, "report": True,
                "year_from": None, "reason": "test"}, ensure_ascii=False)
        if "综述专家" in sys_text:
            if not self._analysis_replies:
                raise RuntimeError("FakeLLM 分析回复耗尽")
            return self._analysis_replies.pop(0)
        if "文献阅读专家" in sys_text:
            return json.dumps({
                "title": "Test Paper", "problem": "问题P", "method": "方法M",
                "contribution": "贡献C", "limitation": "局限L",
                "keywords": ["k1"]}, ensure_ascii=False)
        raise RuntimeError(f"未知 system prompt: {sys_text[:50]}")


class NoKeyLLM(FakeLLM):
    @property
    def available(self) -> bool:
        return False


class DeterministicSearch:
    """深度闭环回归不依赖 arXiv 网络状态。"""

    def run(self, query, max_results=5, **_):
        count = min(2, max(1, int(max_results)))
        slug = query.replace(" ", "-")
        return [Paper(title=f"{query} Paper {i}",
                      url=f"https://example.test/{slug}-{i}.pdf",
                      source="test", year=2025)
                for i in range(1, count + 1)]


class TempReporter(Reporter):
    """报告写到临时目录，避免回归测试污染用户文献库。"""

    _base_dir = tempfile.mkdtemp(prefix="paper-studio-reports-")

    def write_deep(self, *args, **kwargs):
        kwargs["base_dir"] = self._base_dir
        return super().write_deep(*args, **kwargs)


def make_agent(fake, **plugins) -> ResearchAgent:
    plugins.setdefault("search_plugin", DeterministicSearch())
    return ResearchAgent(
        planner=LLMPlanner(llm=fake),
        summarizer=PaperSummarizer(llm=fake),
        analyzer=CrossPaperAnalyzer(llm=fake),
        **plugins,
    )


def expect(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


GAP_JSON = json.dumps({
    "consensus": [], "conflicts": [], "evolution": [],
    "gaps": [{"gap": "缺少基准", "why": "难对比",
              "suggested_query": "mamba benchmark evaluation"}],
    "summary": "s"}, ensure_ascii=False)
EMPTY_JSON = json.dumps({"consensus": [], "conflicts": [], "evolution": [],
                         "gaps": [], "summary": ""}, ensure_ascii=False)


def main() -> None:
    print("== 用例 1：两轮闭环 ==")
    fake = FakeLLM([GAP_JSON, EMPTY_JSON])
    loop = ResearchLoop(agent=make_agent(fake), reporter=TempReporter(), max_rounds=3,
                        branching=2, max_queries=7,
                        analyze_citations=False,
                        use_memory=False)
    result = loop.run("mamba", max_results=2)
    rounds = result["rounds"]
    expect("共 2 轮", len(rounds) == 2)
    expect("Round1 用户输入", rounds[0]["query"] == "mamba"
           and rounds[0]["origin"] == "user")
    expect("Round2 来自盲点", rounds[1]["origin"].startswith("gap"))
    expect("Round2 使用建议查询",
           rounds[1]["query"] == "mamba benchmark evaluation")
    expect("查询统计 = 2", result["stats"]["queries"] == 2)
    expect("去重文献 <= 原始总量",
           result["stats"]["papers_dedup"]
           <= result["stats"]["papers_raw"])
    rp = Path(result["report_path"])
    expect("深度报告已生成", rp.exists())
    text = rp.read_text(encoding="utf-8")
    expect("报告含研究路径", "## 研究路径" in text)
    expect("报告含 Round2 分支", "mamba benchmark evaluation" in text)
    expect("报告含汇总清单", "## 汇总文献清单" in text)

    print("== 用例 2：查询预算上限 ==")
    fake = FakeLLM([GAP_JSON])
    loop = ResearchLoop(agent=make_agent(fake), reporter=TempReporter(), max_queries=1,
                        analyze_citations=False,
                        use_memory=False)
    result = loop.run("mamba", max_results=2)
    expect("只执行 1 个查询", result["stats"]["queries"] == 1)
    expect("仅 1 轮", len(result["rounds"]) == 1)

    print("== 用例 3：无盲点 → 单轮结束 ==")
    fake = FakeLLM([EMPTY_JSON])
    loop = ResearchLoop(agent=make_agent(fake), reporter=TempReporter(),
                        analyze_citations=False,
                        use_memory=False)
    result = loop.run("mamba", max_results=2)
    expect("仅 1 轮", len(result["rounds"]) == 1)
    expect("查询数 = 1", result["stats"]["queries"] == 1)

    print("== 用例 4：无 LLM → 降级单轮 ==")
    fake = NoKeyLLM([])
    loop = ResearchLoop(agent=make_agent(fake), reporter=TempReporter(),
                        analyze_citations=False,
                        use_memory=False)
    result = loop.run("mamba", max_results=2)
    expect("仍执行 1 轮", len(result["rounds"]) == 1)
    summaries = result["rounds"][0]["summaries"]
    expect("模型不可用仍生成全部摘要", len(summaries) == 2)
    expect("降级摘要五字段完整", all(
        rec.get("ok") and all(rec["summary"].get(key) for key in
                              ("problem", "method", "contribution",
                               "limitation", "keywords"))
        for rec in summaries))
    expect("报告仍生成", Path(result["report_path"]).exists())

    print("== 用例 5：研究记忆跨会话复用 ==")
    from agent.core import ResearchMemory
    tmp_mem = Path(tempfile.mkdtemp()) / "mem.json"
    mem = ResearchMemory(path=str(tmp_mem))
    # 预置历史：mamba 已研究过，且无盲点可衍生
    mem.add_round(
        "mamba", [Paper(title="Old Mamba Paper", url="http://x",
                        source="arxiv_search", year=2023)],
        summaries=[], analysis={"consensus": [], "conflicts": [],
                                "evolution": [], "gaps": [], "summary": ""})
    fake = FakeLLM([])  # 不应触发任何真实检索/LLM
    loop = ResearchLoop(agent=make_agent(fake), reporter=TempReporter(), memory=mem,
                        use_memory=True, analyze_citations=False)
    result = loop.run("mamba", max_results=2)
    expect("记忆命中 1 次", result["stats"]["memory_hits"] == 1)
    expect("无新查询", result["stats"]["queries"] == 0)
    expect("Round 来源为 memory",
           result["rounds"][0]["origin"] == "memory")
    expect("复用历史论文",
           result["rounds"][0]["papers"][0].title == "Old Mamba Paper")
    cached = result["rounds"][0]["summaries"]
    expect("旧记忆空摘要已迁移", len(cached) == 1
           and all(cached[0]["summary"].get(key) for key in
                   ("problem", "method", "contribution",
                    "limitation", "keywords")))
    expect("深度报告仍生成", Path(result["report_path"]).exists())

    print("== 用例 6：记忆落盘（研究后自动保存）==")
    fake = FakeLLM([GAP_JSON, EMPTY_JSON])
    mem2 = ResearchMemory(path=str(Path(tempfile.mkdtemp()) / "mem2.json"))
    loop = ResearchLoop(agent=make_agent(fake), reporter=TempReporter(), memory=mem2,
                        use_memory=True, analyze_citations=False,
                        max_rounds=3, max_queries=7)
    loop.run("mamba", max_results=2)
    expect("Round1 已入记忆", mem2.has_query("mamba"))
    expect("Round2 盲点查询已入记忆",
           mem2.has_query("mamba benchmark evaluation"))

    print("== 用例 7：多轮研究全局去重后只下载一次 ==")

    class FakeSearch:
        def run(self, query, **_):
            return [
                Paper(title="Shared Paper", url="https://example.test/shared.pdf",
                      source="test"),
                Paper(title=f"Unique {query}",
                      url=f"https://example.test/{query.replace(' ', '-')}.pdf",
                      source="test"),
            ]

    class FakeAcquisition:
        def __init__(self):
            self.calls = []

        def run(self, papers, **kwargs):
            self.calls.append({"papers": list(papers), "kwargs": kwargs})
            return {"base_dir": "downloads/fake-batch", "items": [],
                    "stats": {"total": len(papers), "downloaded": len(papers),
                              "extracted": 0, "failed": 0, "unavailable": 0}}

    fake = FakeLLM([GAP_JSON, EMPTY_JSON])
    acquisition = FakeAcquisition()
    loop = ResearchLoop(
        agent=make_agent(fake, search_plugin=FakeSearch(),
                         acquisition_plugin=acquisition),
        reporter=TempReporter(),
        max_rounds=3, max_queries=7, analyze_citations=False,
        use_memory=False)
    result = loop.run("mamba", max_results=10, download=True,
                      max_downloads=10, download_interval=0.5)
    expect("下载流水线只调用 1 次", len(acquisition.calls) == 1)
    expect("跨轮重复论文只下载 1 份",
           len(acquisition.calls[0]["papers"]) == 3)
    expect("下载统计进入最终结果",
           result["stats"]["downloads"]["downloaded"] == 3)

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

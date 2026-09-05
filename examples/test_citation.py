"""引用网络分析测试（Fake 引用技能，无需真实网络）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core import CitationAnalyzer, Reporter
from agent.skills import CitationSkill, Paper
from agent.skills.citation_skill import CitationRateLimitError

ATTENTION = {
    "paperId": "s2-attention",
    "title": "Attention Is All You Need",
    "year": 2017,
    "authors": [{"name": "Vaswani"}, {"name": "Shazeer"}],
    "externalIds": {"DOI": "10.5555/3295222.3295349"},
    "venue": "NeurIPS",
}
BERT = {
    "paperId": "s2-bert",
    "title": "BERT: Pre-training of Deep Bidirectional Transformers",
    "year": 2018,
    "authors": [{"name": "Devlin"}],
    "externalIds": {},
    "venue": "NAACL",
}
GPT = {
    "paperId": "s2-gpt",
    "title": "Improving Language Understanding by Generative Pre-Training",
    "year": 2018,
    "authors": [{"name": "Radford"}],
    "externalIds": {},
    "venue": None,
}


class FakeCitationSkill(CitationSkill):
    """覆写 HTTP 层：按请求模式返回固定引用数据。"""

    def __init__(self, references_map) -> None:
        super().__init__(retries=0, backoff_base=0, min_interval=0)
        self._map = references_map  # {s2_id: [ref_dict, ...]}

    def _get_json(self, url, params):
        import re
        m = re.search(r"/paper/([^/]+)/(references|citations)", url)
        if not m:
            raise RuntimeError(f"未知 URL: {url}")
        pid, mode = m.group(1), m.group(2)
        if mode == "references":
            refs = self._map.get(pid, [])
            return {"data": [{"citingPaper": {}, "citedPaper": r}
                             for r in refs]}
        # citations 模式：返回固定被引
        return {"data": [{"citingPaper": GPT, "citedPaper": {}}]}


def expect(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def main() -> None:
    print("== 用例 1：被引频次统计 ==")
    # 语料：A 引用 Attention×2 + BERT×1；B 引用 Attention×1
    refs_map = {
        "s2-a": [ATTENTION, ATTENTION, BERT],
        "s2-b": [ATTENTION, GPT],
    }
    skill = FakeCitationSkill(refs_map)
    analyzer = CitationAnalyzer(skill=skill)
    papers = [
        Paper(title="Paper A", url="http://x/a", source="arxiv",
              extra={"s2_paper_id": "s2-a"}),
        Paper(title="Paper B", url="http://x/b", source="arxiv",
              extra={"s2_paper_id": "s2-b"}),
    ]
    out = analyzer.analyze(papers)
    expect("覆盖率 100%", out["coverage"] == 1.0)
    expect("核心文献为 Attention",
           out["top_cited"][0]["title"] == "Attention Is All You Need")
    expect("被引 3 次", out["top_cited"][0]["cited_by"] == 3)
    expect("第二名为 BERT",
           out["top_cited"][1]["title"].startswith("BERT"))
    expect("含 GPT 引用", any(e["title"].startswith("Improving")
                             for e in out["top_cited"]))
    expect("无退化标记", not out.get("_degraded"))

    print("== 用例 2：语料内互引检测 ==")
    # 语料包含 Attention 本身，且 A 引用了它 → 互引
    corpus = papers + [
        Paper(title="Attention Is All You Need", url="http://x/att",
              source="arxiv", extra={"s2_paper_id": "s2-attention"})]
    out = analyzer.analyze(corpus)
    intra = out["intra_citations"]
    expect("检测到互引", len(intra) >= 1)
    expect("互引方向正确", intra[0]["cited"] == "Attention Is All You Need")

    print("== 用例 3：失败容错 ==")
    class BrokenSkill(CitationSkill):
        def __init__(self):
            super().__init__(retries=0, backoff_base=0, min_interval=0)
            self.calls = 0

        def _get_json(self, url, params):
            self.calls += 1
            raise RuntimeError("429 Too Many Requests")

    broken = BrokenSkill()
    failure_papers = papers + [
        Paper(title="Paper C", url="http://x/c", source="arxiv",
              extra={"s2_paper_id": "s2-c"}),
        Paper(title="Paper D", url="http://x/d", source="arxiv",
              extra={"s2_paper_id": "s2-d"}),
    ]
    out = CitationAnalyzer(skill=broken, max_fail_streak=2,
                           recovery_retries=0).analyze(failure_papers)
    expect("全部失败 → 退化标记", out.get("_degraded") is True)
    expect("错误被分类记录", out["error_stats"]["rate_limited"] == 4)
    expect("连续失败不再中止后续论文", broken.calls == 4)

    print("== 用例 3b：限流失败恢复重试 ==")
    class FlakySkill(FakeCitationSkill):
        def __init__(self, references_map):
            super().__init__(references_map)
            self.attempts = {}

        def _get_json(self, url, params):
            import re
            match = re.search(r"/paper/([^/]+)/(references|citations)", url)
            if match and match.group(2) == "references":
                pid = match.group(1)
                self.attempts[pid] = self.attempts.get(pid, 0) + 1
                if self.attempts[pid] == 1:
                    raise CitationRateLimitError(retry_after=0)
            return super()._get_json(url, params)

    flaky = FlakySkill(refs_map)
    out = CitationAnalyzer(skill=flaky, recovery_delay=0).analyze(papers)
    expect("临时限流全部恢复", out["coverage"] == 1.0 and not out["errors"])
    expect("恢复数量正确", out["recovered_papers"] == 2)

    print("== 用例 3c：缺少 ID 时按标题解析 ==")
    class TitleLookupSkill(FakeCitationSkill):
        def _get_json(self, url, params):
            if url.endswith("/paper/search/match"):
                return {"data": [{"paperId": "s2-a", "title": "Paper A",
                                   "year": 2024, "matchScore": 132.0}]}
            return super()._get_json(url, params)

    title_paper = Paper(title="Paper A", url="http://example.test/paper",
                        source="scholar", year=2024)
    out = CitationAnalyzer(skill=TitleLookupSkill(refs_map),
                           recovery_delay=0).analyze([title_paper])
    expect("标题解析后引用成功", out["coverage"] == 1.0)
    expect("解析结果写回论文", title_paper.extra.get("s2_paper_id") == "s2-a")

    print("== 用例 3c-2：多种标准 ID 解析 ==")
    expect("DOI URL 可解析", CitationSkill._resolve_id(Paper(
        title="D", url="https://doi.org/10.1000/test.1", source="x"))
        == "DOI:10.1000/test.1")
    expect("arXiv 版本号会归一化", CitationSkill._resolve_id(Paper(
        title="A", url="https://arxiv.org/pdf/2401.01234v2.pdf", source="x"))
        == "ARXIV:2401.01234")
    expect("ACL 外部 ID 可解析", CitationSkill._resolve_id(Paper(
        title="ACL", url="", source="x",
        extra={"externalIds": {"ACL": "2024.acl-long.1"}}))
        == "ACL:2024.acl-long.1")

    print("== 用例 3d：报告按原因展示，不再混写 ==")
    rendered = "\n".join(Reporter._render_citations({
        "coverage": 0.5, "analyzed_papers": 1, "total_papers": 2,
        "errors": [{"title": "X", "reason": "missing_id", "message": "x"}],
        "error_stats": {"missing_id": 1, "not_found": 0,
                        "rate_limited": 0, "request_failed": 0},
        "recovered_papers": 1,
    }))
    expect("显示自动恢复", "自动退避" not in rendered and "自动恢复" in rendered)
    expect("失败原因准确", "缺少标准标识符" in rendered)
    expect("移除旧混合警告", "限流或缺少 ID" not in rendered)

    print("== 用例 4：无语料 ==")
    out = CitationAnalyzer(skill=FakeCitationSkill({})).analyze([])
    expect("空语料退化", out.get("_degraded") is True)

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

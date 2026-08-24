"""核心研究能力 Skill 化集成测试（完全离线）。"""

from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core import CrossPaperAnalyzer, PaperSummarizer, Reporter, ResearchMemory
from agent.skills import (
    BaseSkill,
    CitationAnalysisSkill,
    MemoryClearSkill,
    MemoryDeleteSkill,
    MemoryReadSkill,
    MemorySearchSkill,
    MemoryStatsSkill,
    MemoryWriteSkill,
    Paper,
    PaperCompareSkill,
    PaperSummarizeBatchSkill,
    PaperSummarizeSkill,
    ReportRenderSkill,
    ReportWriteSkill,
    SkillPermission,
)


class OfflineLLM:
    @property
    def available(self) -> bool:
        return False


class FakeCitationAnalyzer:
    def analyze(self, papers):
        return {
            "top_cited": [],
            "intra_citations": [],
            "coverage": 1.0 if papers else 0.0,
            "errors": [],
            "analyzed_papers": len(papers),
            "total_papers": len(papers),
            "recovered_papers": 0,
            "error_stats": {},
            "_degraded": not bool(papers),
        }


MODEL_PERMISSIONS = {
    SkillPermission.NETWORK,
    SkillPermission.PAID_API,
}
READ_PERMISSIONS = {SkillPermission.FILESYSTEM_READ}
WRITE_PERMISSIONS = {
    SkillPermission.FILESYSTEM_READ,
    SkillPermission.FILESYSTEM_WRITE,
}
DELETE_PERMISSIONS = {
    *WRITE_PERMISSIONS,
    SkillPermission.DESTRUCTIVE,
}


def make_paper(title: str = "Agent Harness", year: int = 2025) -> Paper:
    return Paper(
        title=title,
        url="https://example.test/paper",
        source="test",
        year=year,
        abstract=("We propose an agent harness method. Experiments show improved "
                  "reliability. One limitation is evaluation scale."),
    )


class CoreSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paper = make_paper()
        self.root = Path(tempfile.mkdtemp())

    def test_summary_single_and_batch(self) -> None:
        engine = PaperSummarizer(llm=OfflineLLM())
        progress = []
        single = PaperSummarizeSkill(summarizer=engine).invoke(
            title=self.paper.title,
            abstract=self.paper.abstract,
            allowed_permissions=MODEL_PERMISSIONS,
            progress_callback=progress.append,
        )
        self.assertTrue(single.ok, single.error)
        self.assertTrue(all(single.data.get(key) for key in (
            "problem", "method", "contribution", "limitation", "keywords")))
        self.assertEqual(progress[-1].percent, 100)

        batch = PaperSummarizeBatchSkill(summarizer=engine).invoke(
            items=[{"title": self.paper.title,
                    "abstract": self.paper.abstract, "text": ""}],
            allowed_permissions=MODEL_PERMISSIONS,
        )
        self.assertTrue(batch.ok, batch.error)
        self.assertEqual(len(batch.data), 1)
        self.assertTrue(batch.data[0]["summary"]["method"])

    def test_cross_paper_compare(self) -> None:
        engine = CrossPaperAnalyzer(llm=OfflineLLM())
        profiles = [
            {"index": 1, "title": "A", "year": 2023, "source": "test",
             "problem": "P", "method": "M1", "contribution": "C1",
             "limitation": "L1", "keywords": ["agent"]},
            {"index": 2, "title": "B", "year": 2025, "source": "test",
             "problem": "P", "method": "M2", "contribution": "C2",
             "limitation": "L2", "keywords": ["agent"]},
        ]
        result = PaperCompareSkill(analyzer=engine).invoke(
            profiles=profiles, allowed_permissions=MODEL_PERMISSIONS)
        self.assertTrue(result.ok, result.error)
        self.assertTrue(result.data["summary"])
        self.assertTrue(result.data["consensus"])
        self.assertTrue(result.data["gaps"])

    def test_citation_analysis(self) -> None:
        result = CitationAnalysisSkill(
            analyzer=FakeCitationAnalyzer()).invoke(
                papers=[self.paper],
                allowed_permissions={SkillPermission.NETWORK})
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.data["coverage"], 1.0)
        self.assertEqual(result.data["total_papers"], 1)

    def test_memory_read_write_search_delete_clear_and_stats(self) -> None:
        memory = ResearchMemory(path=str(self.root / "memory.json"))
        written = MemoryWriteSkill(memory=memory).invoke(
            query="agent harness", papers=[self.paper],
            summaries=[], analysis={"gaps": []},
            allowed_permissions=WRITE_PERMISSIONS)
        self.assertTrue(written.ok, written.error)
        self.assertTrue((self.root / "memory.json").exists())

        searched = MemorySearchSkill(memory=memory).invoke(
            keyword="harness", allowed_permissions=READ_PERMISSIONS)
        self.assertTrue(searched.ok, searched.error)
        self.assertEqual(searched.data[0]["paper_count"], 1)

        read = MemoryReadSkill(memory=memory).invoke(
            query="agent harness", allowed_permissions=READ_PERMISSIONS)
        self.assertTrue(read.ok, read.error)
        self.assertEqual(read.data["papers"][0]["title"], self.paper.title)

        stats = MemoryStatsSkill(memory=memory).invoke(
            allowed_permissions=READ_PERMISSIONS)
        self.assertTrue(stats.ok, stats.error)
        self.assertEqual(stats.data["entries"], 1)

        deleted = MemoryDeleteSkill(memory=memory).invoke(
            query="agent harness", allowed_permissions=DELETE_PERMISSIONS)
        self.assertTrue(deleted.ok, deleted.error)
        self.assertTrue(deleted.data["deleted"])

        MemoryWriteSkill(memory=memory).invoke(
            query="again", papers=[self.paper],
            allowed_permissions=WRITE_PERMISSIONS).raise_for_error()
        denied = MemoryClearSkill(memory=memory).invoke(
            allowed_permissions=WRITE_PERMISSIONS)
        self.assertFalse(denied.ok)
        self.assertEqual(denied.error.code, "permission_denied")
        cleared = MemoryClearSkill(memory=memory).invoke(
            allowed_permissions=DELETE_PERMISSIONS)
        self.assertTrue(cleared.ok, cleared.error)
        self.assertEqual(cleared.data["deleted"], 1)

    def test_report_render_and_write(self) -> None:
        reporter = Reporter()
        plan = {"query": "agent harness", "original_query": "agent harness"}
        rendered = ReportRenderSkill(reporter=reporter).invoke(
            kind="single", plan=plan, papers=[self.paper])
        self.assertTrue(rendered.ok, rendered.error)
        self.assertIn("学术检索报告", rendered.data)
        self.assertIn(self.paper.title, rendered.data)

        written = ReportWriteSkill(reporter=reporter).invoke(
            kind="single", plan=plan, papers=[self.paper],
            base_dir=str(self.root), filename="skill_report.md",
            allowed_permissions={SkillPermission.FILESYSTEM_WRITE})
        self.assertTrue(written.ok, written.error)
        path = Path(written.data["path"])
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "skill_report.md")

        unsafe = ReportWriteSkill(reporter=reporter).invoke(
            kind="single", plan=plan, papers=[self.paper],
            base_dir=str(self.root), filename="../outside.md",
            allowed_permissions={SkillPermission.FILESYSTEM_WRITE})
        self.assertFalse(unsafe.ok)
        self.assertFalse((self.root.parent / "outside.md").exists())

    def test_all_core_skills_are_discoverable(self) -> None:
        expected = {
            "paper_summarize", "paper_summarize_batch", "paper_compare",
            "citation_analyze", "memory_search", "memory_read", "memory_write",
            "memory_delete", "memory_clear", "memory_stats",
            "report_render", "report_write",
        }
        self.assertTrue(expected.issubset(BaseSkill.manifests()))


if __name__ == "__main__":
    unittest.main()

"""本地文献库 RAG Skill 单元测试（无网络，使用 FakeLLM + FakeEmbedder）。"""

from __future__ import annotations

import json
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reportlab.pdfgen import canvas  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402

from agent.core.llm import LLMClient, LLMError  # noqa: E402
from agent.skills.library_rag_skill import (  # noqa: E402
    LibraryRagSkill,
    _chunk_pages,
    _IndexStore,
    _split_paragraphs,
)


def expect(name, cond, got=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + ("" if cond else f" -> {got}"))
    if not cond:
        raise SystemExit(f"断言失败: {name}")


# ----------------------------- Fake 双端 -----------------------------------


class FakeLLM(LLMClient):
    """完全 override embed/chat 路径，不发任何网络请求。"""

    def __init__(self, chat_replies=None) -> None:
        self._chat_replies = list(chat_replies or [])
        self._dim = 8
        self.model = "fake-embed-v1"
        # 跳过父类 __init__（避免读 .env / 探测 ollama）
        self.api_key = ""
        self.base_url = "http://fake.local/v1"
        self.provider = "fake"
        self.provider_type = "openai"
        self.provider_name = "Fake"
        self.requires_api_key = False
        self.timeout = 10
        self.cost_tracker = None

    def embedding(self, text, *, model=None):
        return self._vec(text)

    def embeddings(self, texts, *, model=None, batch_size=32):
        return [self._vec(t) for t in texts]

    def chat(self, user, system=None, temperature=0.0, json_mode=False,
             max_tokens=None, purpose=""):
        if not self._chat_replies:
            raise LLMError("FakeLLM chat 回复耗尽")
        return self._chat_replies.pop(0)

    def _vec(self, text):
        # 简单词袋向量：每个常见词占一维；同时把页码/编号信号编码进去
        text = (text or "").lower()
        vocab = ["transformer", "attention", "rag", "embedding",
                 "mamba", "state", "space", "model", "retrieval",
                 "generation", "augmented", "vector", "index"]
        vec = [0.0] * self._dim
        for index, word in enumerate(vocab):
            if word in text:
                vec[index % self._dim] += 1.0
        # 全部为零时给一个零向量（避免被误判）
        if not any(vec):
            vec[0] = 0.0
        return vec


# ----------------------------- 测试 PDF 构造 --------------------------------


def _make_pdf(path: Path, pages: list[str]) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    for content in pages:
        text_obj = c.beginText(72, height - 72)
        text_obj.setFont("Helvetica", 11)
        for line in content.splitlines() or [""]:
            text_obj.textLine(line)
        c.drawText(text_obj)
        c.showPage()
    c.save()


# ============================== 用例 =======================================


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="library_rag_"))
    try:
        print("== 用例 1：分块与段落切分 ==")
        # 构造足够多的内容触发多 chunk
        para_a = ("This paper introduces the Transformer architecture. "
                  * 30)  # 重复 30 次,撑满
        para_b = ("We propose RAG: retrieval-augmented generation. "
                  "It combines a retriever with a generator. " * 20)
        para_c = "Mamba is a state space model alternative. " * 20
        pages = [
            (1, para_a),
            (2, para_b),
            (3, para_c),
        ]
        chunks = _chunk_pages(pages, chunk_chars=400, overlap_chars=80)
        expect("切出 >= 2 个 chunk", len(chunks) >= 2, len(chunks))
        expect("至少一块包含 transformer", any("Transformer" in c[2]
                                                 for c in chunks))
        expect("页码连续（首块 page_start=1）", chunks[0][0] == 1)
        paras = _split_paragraphs("a\n\nb\n\nc")
        expect("段落切分 3 段", len(paras) == 3, paras)

        print("== 用例 2：构造测试 PDF 并建立索引 ==")
        pdf_dir = tmpdir / "papers"
        pdf_dir.mkdir()
        pdf_a = pdf_dir / "transformer.pdf"
        pdf_b = pdf_dir / "mamba.pdf"
        _make_pdf(pdf_a, [
            "Abstract: We introduce the Transformer, a new architecture "
            "based solely on attention mechanisms.",
            "Introduction: Attention is all you need for sequence "
            "modeling. We replace recurrence with self-attention.",
            "Conclusion: The Transformer achieves state-of-the-art "
            "results on translation tasks.",
        ])
        _make_pdf(pdf_b, [
            "Mamba is a state space model for sequence modeling.",
            "It offers linear-time inference and competitive quality.",
        ])
        index_path = tmpdir / "library_index.json"
        rag = LibraryRagSkill(
            llm=FakeLLM(),
            index_path=str(index_path),
        )
        result = rag.build_index([str(pdf_a), str(pdf_b)])
        expect("索引成功", result["indexed"] == 2, result)
        expect("chunks 数量 > 0", result["chunks"] > 0, result)
        expect("dim = 8", result["dim"] == 8, result)
        expect("索引文件已生成", index_path.exists())

        # 二次 build_index → 增量跳过
        again = rag.build_index([str(pdf_a), str(pdf_b)])
        expect("二次索引：indexed=0", again["indexed"] == 0, again)
        expect("二次索引：skipped=2", again["skipped"] == 2, again)

        print("== 用例 3：query 检索 ==")
        hits = rag.query("What is the Transformer architecture?", top_k=3)
        expect("返回 1-3 个 hit", 1 <= len(hits) <= 3, len(hits))
        expect("最高分 hit 来自 transformer 论文",
               hits and "transformer" in hits[0].chunk.paper_id.lower(),
               hits[0].chunk.paper_id if hits else "")
        expect("hit 包含页码",
               hits and hits[0].chunk.page_start >= 1)

        print("== 用例 4：ask 问答（Fake LLM 回复）==")
        rag2 = LibraryRagSkill(
            llm=FakeLLM(chat_replies=[
                "Transformer 是一个仅基于 attention 的序列模型 [1](p. 1)。"
                "引用: [1]",
            ]),
            index_path=str(index_path),
        )
        ans = rag2.ask("什么是 Transformer?", top_k=2)
        expect("answer 非空", bool(ans.get("answer")))
        expect("citations 数量 = hits 数量",
               len(ans["citations"]) == len(ans["hits"]))
        expect("citation 含 paper_id/page",
               ans["citations"]
               and "page_start" in ans["citations"][0])
        expect("citation 包含页码引用",
               "(p." in ans["answer"] or "页" in ans["answer"])

        print("== 用例 5：ask 在无文献时给出明确提示 ==")
        rag3 = LibraryRagSkill(
            llm=FakeLLM(chat_replies=["备用回答"]),
            index_path=str(tmpdir / "empty_index.json"),
        )
        ans_empty = rag3.ask("没有文献时怎么回答?")
        expect("answer 为提示", "未覆盖" in ans_empty["answer"] or
               "没有" in ans_empty["answer"], ans_empty["answer"])
        expect("citations 为空", ans_empty["citations"] == [])

        print("== 用例 6：JSON 索引可被读回 ==")
        store = _IndexStore.load(index_path)
        expect("读回非空", store is not None and store.chunks)
        expect("embed_model 持久化",
               store and store.embed_model == "fake-embed-v1",
               store.embed_model if store else None)
        expect("papers 表含 2 篇",
               store and len(store.papers) == 2, store.papers.keys() if store else [])

        print("== 用例 7：index 文件可手工 round-trip ==")
        data = json.loads(index_path.read_text(encoding="utf-8"))
        expect("JSON 含 version", "version" in data)
        expect("JSON 含 chunks", isinstance(data.get("chunks"), list))
        expect("chunk 至少一个", len(data["chunks"]) >= 1)

        print("\n全部用例通过 ✅")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()

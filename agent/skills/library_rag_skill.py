"""本地文献库 RAG 技能。

仅基于用户已下载的 PDF 文本回答问题,结论带页码和原文引用片段。
默认零新依赖:使用 ``LLMClient.embeddings`` 复用现有服务商配置
(Ollama 优先,云端 OpenAI 兼容兜底),索引持久化为 JSON。

核心方法:
- :meth:`LibraryRagSkill.build_index` 扫描 PDF 目录并建立向量索引
- :meth:`LibraryRagSkill.query` 检索最相关 chunk
- :meth:`LibraryRagSkill.ask` 检索 + 拼 prompt + 调用 LLM 回答
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .base import BaseSkill, SkillPermission
from .downloader_skill import DownloaderSkill
from .metadata import Paper


# 粗略估算:1 token ≈ 4 字符(英文);中文略偏高但够用
_CHARS_PER_TOKEN = 4
# 段落分隔(双换行)。单换行视作段内换行,保留作为上下文。
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")
# 多余空白折叠
_WHITESPACE = re.compile(r"[ \t]+")


@dataclass
class Chunk:
    """一条文本切片,带定位信息。"""

    chunk_id: str
    paper_id: str
    paper_title: str
    paper_path: str
    page_start: int
    page_end: int
    text: str
    vector: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "paper_id": self.paper_id,
            "paper_title": self.paper_title,
            "paper_path": self.paper_path,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "text": self.text,
            "vector": self.vector,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Chunk":
        return cls(
            chunk_id=str(data["chunk_id"]),
            paper_id=str(data["paper_id"]),
            paper_title=str(data.get("paper_title") or ""),
            paper_path=str(data.get("paper_path") or ""),
            page_start=int(data.get("page_start") or 1),
            page_end=int(data.get("page_end") or 1),
            text=str(data.get("text") or ""),
            vector=[float(x) for x in (data.get("vector") or [])],
        )


@dataclass
class Hit:
    """一次查询命中的 chunk,含相似度。"""

    chunk: Chunk
    score: float

    def to_dict(self) -> Dict[str, Any]:
        return {"score": round(self.score, 6),
                "chunk": self.chunk.to_dict()}


# ============================== 索引 IO ====================================


class _IndexStore:
    """纯 JSON 持久化 + 内存余弦检索。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.version: int = 1
        self.embed_model: str = ""
        self.dim: int = 0
        self.papers: Dict[str, Dict[str, Any]] = {}
        self.chunks: List[Chunk] = []

    # ------------------ 序列化 ------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "embed_model": self.embed_model,
            "dim": self.dim,
            "papers": self.papers,
            "chunks": [c.to_dict() for c in self.chunks],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "_IndexStore":
        store = cls(path=Path(""))
        store.version = int(data.get("version") or 1)
        store.embed_model = str(data.get("embed_model") or "")
        store.dim = int(data.get("dim") or 0)
        store.papers = {str(k): dict(v) for k, v in (data.get("papers") or {}).items()}
        store.chunks = [Chunk.from_dict(item) for item in (data.get("chunks") or [])]
        return store

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 临时文件 + 原子替换,防止中途崩溃损坏索引
        tmp = self.path.with_suffix(self.path.suffix + ".part")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self.path)

    @classmethod
    def load(cls, path: Path) -> Optional["_IndexStore"]:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        store = cls.from_dict(data)
        store.path = path
        return store


# ============================== 文本分块 ====================================


def _split_paragraphs(text: str) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = _PARAGRAPH_SPLIT.split(text)
    return [p.strip() for p in parts if p.strip()]


def _normalize_text(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _chunk_pages(
    pages: Sequence[Tuple[int, str]],
    *,
    chunk_chars: int,
    overlap_chars: int,
) -> List[Tuple[int, int, str]]:
    """把多页文本切成 ``(page_start, page_end, text)`` 块。

    优先在段落边界切;若单个段落超过 ``chunk_chars``,在句号/换行处硬切。
    """
    chunk_chars = max(200, int(chunk_chars))
    overlap_chars = max(0, min(int(overlap_chars), chunk_chars // 2))

    # 把每页的文本先按段落拆开,带上页码;再拼接成不超过 chunk_chars 的段块
    units: List[Tuple[int, str]] = []  # (page_no, paragraph)
    for page_no, raw in pages:
        for para in _split_paragraphs(raw):
            units.append((page_no, _normalize_text(para)))

    blocks: List[Tuple[int, int, str]] = []
    current: List[Tuple[int, str]] = []
    current_len = 0
    page_start = 0

    def flush() -> None:
        nonlocal current, current_len, page_start
        if not current:
            return
        page_end = current[-1][0]
        text = "\n\n".join(item[1] for item in current).strip()
        if text:
            blocks.append((page_start or page_end, page_end, text))
        # 构造 overlap:从尾部回退 overlap_chars 字符对应的若干 unit
        if overlap_chars <= 0:
            current = []
            current_len = 0
            page_start = 0
            return
        keep: List[Tuple[int, str]] = []
        keep_len = 0
        for unit in reversed(current):
            if keep_len + len(unit[1]) + 2 > overlap_chars:
                break
            keep.append(unit)
            keep_len += len(unit[1]) + 2
        keep.reverse()
        current = keep
        current_len = sum(len(item[1]) + 2 for item in keep)
        page_start = current[0][0] if current else 0

    for page_no, para in units:
        para_len = len(para)
        # 超长段落单独成块,在句号/换行处硬切
        if para_len > chunk_chars:
            flush()
            step = max(1, chunk_chars - overlap_chars)
            for start in range(0, para_len, step):
                sub = para[start:start + chunk_chars]
                if not sub.strip():
                    continue
                blocks.append((page_no, page_no, sub.strip()))
            continue
        if current_len + para_len + 2 > chunk_chars and current:
            flush()
        if not current:
            page_start = page_no
        current.append((page_no, para))
        current_len += para_len + 2
    flush()
    return blocks


def _hash_paper(path: Path) -> str:
    """用文件大小 + 头部字节算稳定指纹,避免大文件全量读。"""
    h = hashlib.sha1()
    try:
        size = path.stat().st_size
        h.update(str(size).encode())
        with path.open("rb") as fh:
            h.update(fh.read(64 * 1024))
    except OSError:
        pass
    return h.hexdigest()[:16]


# ============================== Skill 入口 ==================================


_ASK_SYSTEM_PROMPT = (
    "你是一位严谨的学术研究助手。回答用户的问题时,必须 **只** 基于下方"
    "「参考资料」中提供的原文片段,不得引入资料外的知识。每条结论后用 "
    "``[n]`` 标注其来源(对应资料的编号),如 ``[1]``、``[2]``;引用片段中的"
    "页码用 ``(p. 3)`` 形式标注。若资料不足以回答,直接说明「现有文献未覆盖"
    "该问题」,不要编造。回答尽量简洁,聚焦用户问题。"
)


class LibraryRagSkill(BaseSkill):
    """本地文献库 RAG 技能。

    - ``build_index`` 扫描 PDF 目录,按 token 上限切块,调用 embedding
      服务建索引,持久化为 JSON。
    - ``query`` 仅做向量检索,返回命中的 chunk 与相似度。
    - ``ask`` 在 ``query`` 之上拼装 prompt,让 LLM 基于引用片段作答。
    """

    name = "library_rag"
    description = (
        "基于已下载 PDF 文本的本地 RAG:支持 build_index/query/ask,"
        "回答带页码和原文引用片段。"
    )
    version = "0.1.0"
    permissions = frozenset({
        SkillPermission.NETWORK,
        SkillPermission.FILESYSTEM_READ,
        SkillPermission.FILESYSTEM_WRITE,
    })
    default_timeout_seconds = 600.0

    DEFAULT_INDEX_PATH = "downloads/library_index.json"

    def __init__(
        self,
        llm: Optional[Any] = None,
        downloader: Optional[DownloaderSkill] = None,
        index_path: str = DEFAULT_INDEX_PATH,
    ) -> None:
        self._llm = llm
        self.downloader = downloader or DownloaderSkill()
        self.index_path = Path(index_path)

    # ---------- 内部属性 ----------
    @property
    def llm(self) -> Any:
        if self._llm is None:
            from ..core.llm import LLMClient
            self._llm = LLMClient()
        return self._llm

    def _store(self) -> _IndexStore:
        store = _IndexStore.load(self.index_path)
        if store is None:
            return _IndexStore(path=self.index_path)
        return store

    # ===================================================================
    # 1) 索引
    # ===================================================================
    def build_index(
        self,
        pdf_paths: Optional[Sequence[str]] = None,
        *,
        batch_size: int = 16,
        chunk_tokens: int = 500,
        overlap_tokens: int = 80,
        embed_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """扫描 PDF 并建立/更新索引。

        - ``pdf_paths`` 缺省时扫描 :attr:`index_path` 同级目录的 ``papers/`` 子目录
          以及 ``downloads/<run_id>/papers/``。
        - 已索引且文件指纹未变的 PDF 会被跳过(增量)。
        - ``embed_model`` 缺省时使用 :class:`LLMClient` 当前配置;切换模型会
          触发全量重建。
        """
        self.report_progress(2, "正在准备索引", stage="start")
        store = self._store()
        target_model = embed_model or self.llm.model
        if not target_model:
            raise RuntimeError("尚未选择 embedding 模型,请先配置 LLM 服务商")

        # 切换 embedding 模型 → 清空旧索引
        if store.chunks and store.embed_model and store.embed_model != target_model:
            self.report_progress(5, f"检测到模型变化 {store.embed_model}→{target_model},重建索引",
                                 stage="rebuild")
            store = _IndexStore(path=self.index_path)
        store.embed_model = target_model

        candidates = self._discover_pdfs(pdf_paths)
        if not candidates:
            self.report_progress(100, "未发现需要索引的 PDF", stage="empty")
            return {
                "indexed": 0, "skipped": 0, "failed": 0,
                "chunks": 0, "embed_model": target_model,
                "index_path": str(self.index_path),
            }

        indexed = 0
        skipped = 0
        failed = 0
        chunk_chars = max(200, chunk_tokens * _CHARS_PER_TOKEN)
        overlap_chars = max(0, overlap_tokens * _CHARS_PER_TOKEN)

        for paper_path in candidates:
            self.checkpoint()
            paper_id = paper_path.stem
            fingerprint = _hash_paper(paper_path)
            paper_record = store.papers.get(paper_id, {})
            if (paper_record.get("fingerprint") == fingerprint
                    and paper_record.get("chunk_ids")):
                skipped += 1
                self.report_progress(
                    min(95, int(20 + indexed / max(1, len(candidates)) * 70)),
                    f"跳过 {paper_path.name}（未变化）", stage="skip",
                    current=indexed + skipped + failed, total=len(candidates))
                continue
            try:
                pages = self.downloader.extract_pages_with_offsets(str(paper_path))
                cleaned = self.downloader.clean_text("\n".join(t for _, t in pages))
                blocks = _chunk_pages(
                    [(p, t) for p, t in pages],
                    chunk_chars=chunk_chars, overlap_chars=overlap_chars)
                if not blocks:
                    failed += 1
                    continue
                paper_title = self._infer_title(cleaned, paper_path)
                new_chunks: List[Chunk] = []
                texts: List[str] = []
                meta: List[Tuple[int, int]] = []
                for index, (p_start, p_end, text) in enumerate(blocks):
                    cid = f"{paper_id}-{index:04d}"
                    new_chunks.append(Chunk(
                        chunk_id=cid, paper_id=paper_id,
                        paper_title=paper_title,
                        paper_path=str(paper_path),
                        page_start=p_start, page_end=p_end,
                        text=text))
                    texts.append(text)
                    meta.append((p_start, p_end))
                vectors = self.embeddings_with_progress(texts, batch_size=batch_size,
                                                      stage=f"embed:{paper_path.name}")
                for chunk, vector in zip(new_chunks, vectors):
                    chunk.vector = vector
                # 替换旧 chunk
                store.chunks = [c for c in store.chunks
                                if c.paper_id != paper_id]
                store.chunks.extend(new_chunks)
                if vectors:
                    store.dim = len(vectors[0])
                store.papers[paper_id] = {
                    "path": str(paper_path),
                    "title": paper_title,
                    "fingerprint": fingerprint,
                    "chunk_ids": [c.chunk_id for c in new_chunks],
                    "indexed_at": int(time.time()),
                }
                indexed += 1
            except Exception as err:  # noqa: BLE001
                failed += 1
                self.report_progress(
                    min(95, int(20 + (indexed + skipped + failed) /
                                max(1, len(candidates)) * 70)),
                    f"{paper_path.name} 索引失败：{err}", stage="failed")
                continue
            self.report_progress(
                min(95, int(20 + (indexed + skipped + failed) /
                            max(1, len(candidates)) * 70)),
                f"已索引 {indexed}/{len(candidates)} 篇",
                stage="index",
                current=indexed + skipped + failed, total=len(candidates))
        store.save()
        return {
            "indexed": indexed, "skipped": skipped, "failed": failed,
            "chunks": len(store.chunks), "embed_model": target_model,
            "dim": store.dim, "index_path": str(self.index_path),
            "papers": list(store.papers.keys()),
        }

    def embeddings_with_progress(self, texts: List[str], *, batch_size: int,
                                 stage: str) -> List[List[float]]:
        """带进度上报的批量 embedding,内部循环。"""
        vectors: List[List[float]] = []
        for start in range(0, len(texts), batch_size):
            self.checkpoint()
            chunk = texts[start:start + batch_size]
            try:
                vectors.extend(self.llm.embeddings(chunk))
            except Exception as err:
                # 单批失败时降级为逐条调用,定位坏样本
                for single in chunk:
                    try:
                        vectors.append(self.llm.embedding(single))
                    except Exception:  # noqa: BLE001
                        vectors.append([])
                self.report_progress(
                    50, f"embedding 部分失败({stage}): {err}", stage="embed-fallback")
            self.report_progress(
                min(90, int(20 + (start + len(chunk)) /
                            max(1, len(texts)) * 60)),
                f"已编码 {start + len(chunk)}/{len(texts)} 个 chunk",
                stage=stage,
                current=start + len(chunk), total=len(texts))
        return vectors

    # ===================================================================
    # 2) 检索
    # ===================================================================
    def query(
        self,
        question: str,
        *,
        top_k: int = 5,
        embed_model: Optional[str] = None,
        paper_ids: Optional[Sequence[str]] = None,
    ) -> List[Hit]:
        """向量检索:返回与问题最相关的 chunk(按相似度降序)。"""
        if not question or not question.strip():
            return []
        store = self._store()
        if not store.chunks:
            return []
        if not store.embed_model:
            raise RuntimeError("索引为空,请先调用 build_index")

        target_model = embed_model or self.llm.model
        if target_model and store.embed_model and target_model != store.embed_model:
            raise RuntimeError(
                f"索引使用模型 {store.embed_model},当前模型 {target_model}。"
                "请用相同模型构建索引,或重新 build_index。")

        query_vec = self.llm.embedding(question)
        qn = _norm(query_vec)
        if qn == 0:
            return []
        hits: List[Hit] = []
        for chunk in store.chunks:
            if paper_ids and chunk.paper_id not in paper_ids:
                continue
            if not chunk.vector:
                continue
            cn = _norm(chunk.vector)
            if cn == 0:
                continue
            score = sum(a * b for a, b in zip(query_vec, chunk.vector)) / (qn * cn)
            hits.append(Hit(chunk=chunk, score=score))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:max(1, int(top_k))]

    # ===================================================================
    # 3) 问答
    # ===================================================================
    def ask(
        self,
        question: str,
        *,
        top_k: int = 5,
        max_answer_chars: int = 4000,
        embed_model: Optional[str] = None,
        chat_model: Optional[str] = None,
        paper_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """检索 + LLM 回答,返回 ``{answer, citations, hits}``。"""
        hits = self.query(question, top_k=top_k, embed_model=embed_model,
                          paper_ids=paper_ids)
        if not hits:
            return {"answer": "现有文献未覆盖该问题。",
                    "citations": [], "hits": []}

        context_blocks: List[str] = []
        citations: List[Dict[str, Any]] = []
        for index, hit in enumerate(hits, start=1):
            chunk = hit.chunk
            page_label = (f"p. {chunk.page_start}"
                          if chunk.page_start == chunk.page_end
                          else f"pp. {chunk.page_start}-{chunk.page_end}")
            context_blocks.append(
                f"[{index}] {chunk.paper_title}（{page_label}）\n{chunk.text}")
            citations.append({
                "index": index,
                "paper_id": chunk.paper_id,
                "paper_title": chunk.paper_title,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "quote": chunk.text[:300],
                "score": round(hit.score, 4),
            })
        context = "\n\n---\n\n".join(context_blocks)
        user_prompt = (
            f"参考资料：\n\n{context}\n\n"
            f"问题：{question}\n\n"
            "请基于上述资料回答。回答末尾用「引用: [n], [m]」列出用到的来源编号。"
        )

        try:
            answer = self.llm.chat(
                user=user_prompt, system=_ASK_SYSTEM_PROMPT,
                temperature=0.0, json_mode=False,
                max_tokens=min(1024, max_answer_chars // 2),
                purpose="library_rag",
            )
        except Exception as err:  # noqa: BLE001
            answer = (
                f"生成回答时出错：{err}\n\n"
                "已为你检索到最相关的原文片段,可直接阅读下方引用。"
            )
        return {
            "answer": answer.strip(),
            "citations": citations,
            "hits": [h.to_dict() for h in hits],
        }

    # ===================================================================
    # 辅助
    # ===================================================================
    def _discover_pdfs(self, pdf_paths: Optional[Sequence[str]]) -> List[Path]:
        if pdf_paths:
            return [Path(p) for p in pdf_paths if Path(p).exists()]
        roots: List[Path] = []
        # 1) 索引文件同级的 papers/ 子目录(若有)
        sibling = self.index_path.parent / "papers"
        if sibling.exists():
            roots.append(sibling)
        # 2) downloads/<run_id>/papers 下的所有批次
        downloads = self.index_path.parent
        if downloads.exists():
            for run_dir in sorted(downloads.iterdir()):
                if not run_dir.is_dir():
                    continue
                papers_dir = run_dir / "papers"
                if papers_dir.exists():
                    roots.append(papers_dir)
        result: List[Path] = []
        seen = set()
        for root in roots:
            for pdf in sorted(root.glob("*.pdf")):
                key = str(pdf.resolve())
                if key in seen:
                    continue
                seen.add(key)
                result.append(pdf)
        return result

    @staticmethod
    def _infer_title(text: str, path: Path) -> str:
        head = (text or "").splitlines()[:3]
        for line in head:
            line = line.strip()
            if len(line) >= 8 and not line.lower().startswith(("abstract", "arxiv")):
                return line[:200]
        return path.stem.replace("_", " ").replace("-", " ").strip() or path.name

    # ------------------------------------------------------------------
    # 标准 Skill 入口
    # ------------------------------------------------------------------
    def execute(self, **kwargs: Any) -> Any:  # pragma: no cover - 调度入口
        action = kwargs.pop("action", "ask")
        if action == "build_index":
            return self.build_index(**kwargs)
        if action == "query":
            return self.query(**kwargs)
        if action == "ask":
            return self.ask(**kwargs)
        raise ValueError(f"不支持的 library_rag 动作: {action!r}")


# ============================== 纯函数辅助 ==================================


def _norm(vector: Sequence[float]) -> float:
    if not vector:
        return 0.0
    # 避免 math.sqrt 提前计算,显式求和即可
    return math.sqrt(sum(float(x) * float(x) for x in vector))

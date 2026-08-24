"""文件下载与文本抽取技能。

能力：
1. download: 安全下载文件到本地目录（支持流式、超时、文件名推断）。
2. extract_text_from_pdf: 从 PDF 提取纯文本（优先 pypdf，可选 pdfplumber）。
3. clean_text: 清洗文本（去页码、折叠空白、去页眉页脚噪声）。
"""

from __future__ import annotations

import re
import random
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlparse

import requests

from .base import BaseSkill, SkillPermission

# 可选的 PDF 解析库：有则用，无则报出清晰错误
try:  # pragma: no cover
    from pypdf import PdfReader

    _PDF_BACKEND = "pypdf"
except ImportError:  # pragma: no cover
    _PDF_BACKEND = None


class DownloaderSkill(BaseSkill):
    """负责文件下载、PDF 文本抽取与文本清洗。"""

    name = "downloader"
    description = "下载文件、从 PDF 抽取文本、清洗文本噪声。"
    version = "1.1.0"
    input_schema = {
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "minLength": 8,
                    "pattern": r"^https?://"},
            "dest_dir": {"type": "string", "minLength": 1},
            "filename": {"type": ["string", "null"]},
        },
        "additionalProperties": True,
    }
    output_schema = {"type": "string", "minLength": 1}
    permissions = frozenset({
        SkillPermission.NETWORK,
        SkillPermission.FILESYSTEM_READ,
        SkillPermission.FILESYSTEM_WRITE,
    })
    default_timeout_seconds = 600.0

    # 所有下载器实例共享按域名节流状态，避免多个后台任务同时打满同一站点。
    _host_lock = threading.Lock()
    _host_last_request: dict = {}

    def __init__(self, timeout: int = 90, chunk_size: int = 64 * 1024,
                 retries: int = 4, min_interval: float = 1.5,
                 max_file_mb: int = 200,
                 session: Optional[requests.Session] = None) -> None:
        self.timeout = timeout
        self.chunk_size = chunk_size
        self.retries = retries
        self.min_interval = max(0.0, float(min_interval))
        self.max_file_bytes = max(1, int(max_file_mb)) * 1024 * 1024
        self.session = session or requests.Session()
        self.headers = {
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/122.0 Safari/537.36"),
        }

    # ------------------------------------------------------------------
    def execute(self, url: str, dest_dir: str = "downloads",
                filename: Optional[str] = None, **_: object) -> Path:
        """统一入口：下载 url 到 dest_dir，返回本地路径。"""
        self.report_progress(5, "正在准备下载", stage="prepare")
        return self.download(url, dest_dir, filename)

    # ------------------------------------------------------------------
    def download(self, url: str, dest_dir: str = "downloads",
                 filename: Optional[str] = None,
                 expected_pdf: bool = True) -> Path:
        """下载文件（按站点节流、流式临时写入、指数退避与完整性校验）。"""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"不支持的下载地址: {url}")
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)

        last_err: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            self.checkpoint()
            resp: Optional[requests.Response] = None
            part_path: Optional[Path] = None
            try:
                self._wait_for_host(url)
                resp = self.session.get(
                    url, headers=self.headers,
                    timeout=(min(15, self.timeout), self.timeout),
                    stream=True, allow_redirects=True)
                if resp.status_code in (429, 500, 502, 503, 504):
                    retry_after = resp.headers.get("Retry-After")
                    raise RuntimeError(
                        f"HTTP {resp.status_code}"
                        + (f"（Retry-After={retry_after}）" if retry_after else ""))
                resp.raise_for_status()
                self.report_progress(20, "连接成功，正在接收文件", stage="download",
                                     attempt=attempt + 1)
                fname = filename or self._infer_filename(resp, url)
                target = dest / fname
                if target.exists() and target.stat().st_size > 0:
                    if not expected_pdf or self._is_pdf(target):
                        return target
                part_path = target.with_suffix(target.suffix + ".part")
                written = 0
                content_length = int(resp.headers.get("Content-Length") or 0)
                last_reported_percent = -1
                with open(part_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=self.chunk_size):
                        self.checkpoint()
                        if chunk:
                            fh.write(chunk)
                            written += len(chunk)
                            if content_length > 0:
                                percent = 20 + min(70, written / content_length * 70)
                                # 最多每增加 1% 上报一次，避免大文件淹没 UI 事件队列。
                                if int(percent) > last_reported_percent:
                                    last_reported_percent = int(percent)
                                    self.report_progress(
                                        percent, "正在下载文件", stage="download",
                                        current=written, total=content_length)
                            if written > self.max_file_bytes:
                                raise RuntimeError(
                                    f"文件超过 {self.max_file_bytes // 1024 // 1024} MB 限制")
                if written == 0:
                    raise RuntimeError("服务器返回了空文件")
                if expected_pdf and not self._is_pdf(part_path):
                    content_type = resp.headers.get("Content-Type", "未知")
                    raise RuntimeError(
                        f"返回内容不是有效 PDF（Content-Type: {content_type}）")
                part_path.replace(target)
                self.report_progress(95, "文件下载完成", stage="verify",
                                     current=written, total=content_length or None)
                return target
            except (requests.RequestException, OSError, RuntimeError) as err:  # noqa: PERF203
                last_err = err
                if part_path is not None and part_path.exists():
                    try:
                        part_path.unlink()
                    except OSError:
                        pass
                if attempt < self.retries:
                    time.sleep(self._retry_delay(err, attempt))
            finally:
                if resp is not None:
                    resp.close()
        raise RuntimeError(f"下载失败 {url}: {last_err}")

    def _wait_for_host(self, url: str) -> None:
        """同一域名的请求至少间隔 min_interval 秒。"""
        if self.min_interval <= 0:
            return
        host = (urlparse(url).hostname or "").lower()
        with self._host_lock:
            now = time.monotonic()
            wait = self.min_interval - (
                now - float(self._host_last_request.get(host, 0.0)))
            if wait > 0:
                time.sleep(wait)
            self._host_last_request[host] = time.monotonic()

    @staticmethod
    def _retry_delay(err: Exception, attempt: int) -> float:
        """429/5xx 使用较长指数退避，其余错误采用温和退避并加入抖动。"""
        message = str(err)
        retry_match = re.search(r"Retry-After=([0-9.]+)", message)
        if retry_match:
            return min(120.0, max(1.0, float(retry_match.group(1))))
        base = 5.0 if any(code in message for code in ("429", "503")) else 1.5
        return min(60.0, base * (2 ** attempt) + random.uniform(0.1, 0.8))

    @staticmethod
    def _is_pdf(path: Path) -> bool:
        try:
            with path.open("rb") as fh:
                return fh.read(5) == b"%PDF-"
        except OSError:
            return False

    @staticmethod
    def _infer_filename(resp: requests.Response, url: str) -> str:
        """从 Content-Disposition 或 URL 推断文件名。"""
        cd = resp.headers.get("Content-Disposition", "")
        m = re.search(r"filename\*?=(?:UTF-8''|\")([^\";]+)", cd, re.I)
        if m:
            return unquote(m.group(1))
        path = urlparse(url).path
        name = path.rsplit("/", 1)[-1]
        if name and "." in name:
            return unquote(name)
        return f"paper_{int(time.time())}.pdf"

    # ------------------------------------------------------------------
    def extract_text_from_pdf(self, file_path: str,
                              max_pages: Optional[int] = None) -> str:
        """从 PDF 提取纯文本。"""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        if _PDF_BACKEND is None:
            raise RuntimeError(
                "未安装 PDF 解析库，请执行: pip install pypdf")

        reader = PdfReader(str(path))
        pages = reader.pages
        if max_pages:
            pages = pages[:max_pages]
        parts: List[str] = []
        for page in pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - 单页失败不阻塞整体
                continue
        return "\n".join(parts)

    # ------------------------------------------------------------------
    def clean_text(self, raw_text: str) -> str:
        """清洗文本：折叠空白、去掉孤立页码行、压缩重复空行。"""
        if not raw_text:
            return ""
        lines = []
        for line in raw_text.splitlines():
            line = line.strip()
            # 跳过孤立页码行（如 "12" / "Page 12"）
            if re.fullmatch(r"(page\s*)?\d{1,4}", line, re.I):
                continue
            lines.append(line)
        text = "\n".join(lines)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def extract_and_clean(self, file_path: str,
                          max_pages: Optional[int] = None) -> Tuple[str, str]:
        """便捷方法：抽取 + 清洗，返回 (原始文本, 清洗后文本)。"""
        raw = self.extract_text_from_pdf(file_path, max_pages)
        return raw, self.clean_text(raw)

"""通过本机鉴权通道控制 Paper Studio Web/App 研究任务。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import socket
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .read_service import resolve_data_dir


_JOB_ID = re.compile(r"^research-\d{8}T\d{6}-[0-9a-f]{8}$")
_TERMINAL = frozenset({"done", "error", "cancelled"})


class ResearchControlError(RuntimeError):
    """Web/App 研究控制通道不可用或拒绝请求。"""


class ResearchControlClient:
    """MCP 到现有任务中心的轻量本机客户端。"""

    def __init__(self, data_dir: Optional[str | Path] = None,
                 timeout: float = 5.0) -> None:
        self.data_dir = resolve_data_dir(data_dir)
        self.runtime_path = self.data_dir / "mcp_runtime.json"
        self.timeout = max(1.0, min(30.0, float(timeout)))

    def _runtime(self) -> tuple[str, str]:
        try:
            data = json.loads(self.runtime_path.read_text(encoding="utf-8"))
            port = int(data.get("port"))
            token = str(data.get("token") or "")
        except (OSError, ValueError, TypeError, AttributeError) as err:
            raise ResearchControlError(
                "Paper Studio Web/App 后端未运行；请先启动应用或 Web 服务") from err
        if not 1 <= port <= 65535 or len(token) < 32:
            raise ResearchControlError(
                "Paper Studio 控制信息无效；请重启应用或 Web 服务")
        # 地址固定为回环接口，运行时文件无法把 MCP 请求重定向到外网。
        return f"http://127.0.0.1:{port}", token

    def _request(self, method: str, path: str,
                 payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        base_url, token = self._runtime()
        body = (json.dumps(payload, ensure_ascii=False).encode("utf-8")
                if payload is not None else None)
        request = Request(
            base_url + path,
            data=body,
            method=method,
            headers={
                "Content-Type": "application/json",
                "X-Paper-Studio-Control": token,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ResearchControlError("Paper Studio 控制响应过大")
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as err:
                    raise ResearchControlError(
                        "Paper Studio 返回了无效的控制响应") from err
                if not isinstance(data, dict):
                    raise ResearchControlError("Paper Studio 返回了无效的控制响应")
                return data
        except HTTPError as err:
            try:
                detail = json.loads(err.read().decode("utf-8")).get("error")
            except (ValueError, AttributeError):
                detail = None
            if err.code == 403:
                detail = "Paper Studio 控制凭据已失效，请重启应用或 Web 服务"
            raise ResearchControlError(
                str(detail or f"研究控制请求失败（HTTP {err.code}）")) from err
        except (URLError, socket.timeout, TimeoutError, OSError) as err:
            raise ResearchControlError(
                "无法连接 Paper Studio 任务中心；请确认 Web/App 正在运行") from err

    @staticmethod
    def _validate_job_id(job_id: str) -> str:
        job_id = str(job_id or "").strip()
        if not _JOB_ID.fullmatch(job_id):
            raise ValueError("无效的研究任务 ID")
        return job_id

    def _redact_local_paths(self, value: str) -> str:
        text = value
        roots = {self.data_dir, self.data_dir.parent, Path.cwd().resolve()}
        for root in sorted((str(path) for path in roots), key=len, reverse=True):
            if root and root != "/":
                text = text.replace(root, "[local]")
        return text

    def _sanitize_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        status = str(job.get("status") or "unknown")
        raw_report = str(job.get("report_path") or "")
        report_id = Path(raw_report).name if raw_report.endswith(".md") else None
        raw_error = str(job.get("error") or "").strip()
        error = (self._redact_local_paths(raw_error.splitlines()[0])[:500]
                 if raw_error else None)
        logs = [self._redact_local_paths(str(line))[:1000]
                for line in list(job.get("log") or [])[-30:]]
        try:
            progress = max(0, min(100, int(job.get("progress") or 0)))
        except (TypeError, ValueError):
            progress = 0
        try:
            elapsed = max(0, int(job.get("elapsed_seconds") or 0))
        except (TypeError, ValueError):
            elapsed = 0
        return {
            "id": str(job.get("id") or ""),
            "query": str(job.get("query") or ""),
            "mode": str(job.get("mode") or "single"),
            "status": status,
            "stage": str(job.get("stage") or "状态未知"),
            "progress": progress,
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "elapsed_seconds": elapsed,
            "report_id": report_id,
            "error": error,
            "latest_log": logs,
            "can_pause": status in {"queued", "running"},
            "can_resume": status == "paused",
            "is_terminal": status in _TERMINAL,
        }

    def start(self, query: str, mode: str = "deep",
              max_results: int = 10, rounds: int = 2,
              branching: int = 1, max_queries: int = 3) -> Dict[str, Any]:
        job = self._request("POST", "/api/mcp/run", {
            "query": query,
            "mode": mode,
            "max_results": max_results,
            "rounds": rounds,
            "branching": branching,
            "max_queries": max_queries,
        })
        return self._sanitize_job(job)

    def start_download(self, query: str, mode: str = "deep",
                       max_results: int = 10, rounds: int = 2,
                       branching: int = 1, max_queries: int = 3,
                       max_downloads: int = 5) -> Dict[str, Any]:
        job = self._request("POST", "/api/mcp/run-download", {
            "query": query,
            "mode": mode,
            "max_results": max_results,
            "rounds": rounds,
            "branching": branching,
            "max_queries": max_queries,
            "max_downloads": max_downloads,
        })
        return self._sanitize_job(job)

    def write_memory(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/api/mcp/memory-write", payload)

    @staticmethod
    def _sanitize_schedule(task: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(task.get("id") or ""),
            "query": str(task.get("query") or ""),
            "enabled": bool(task.get("enabled", True)),
            "interval_minutes": int(task.get("interval_minutes") or 60),
            "mode": str(task.get("mode") or "deep"),
            "max_results": int(task.get("max_results") or 10),
            "rounds": int(task.get("rounds") or 2),
            "branching": int(task.get("branching") or 1),
            "max_queries": int(task.get("max_queries") or 3),
            "last_run": task.get("last_run"),
            "last_job": task.get("last_job"),
        }

    def list_schedules(self) -> list[Dict[str, Any]]:
        result = self._request("GET", "/api/mcp/schedules")
        raw = result.get("schedules")
        if not isinstance(raw, list):
            raise ResearchControlError("Paper Studio 返回了无效的定时任务列表")
        return [self._sanitize_schedule(item) for item in raw
                if isinstance(item, dict)]

    def save_schedule(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        task = self._request("POST", "/api/mcp/schedule-save", payload)
        return self._sanitize_schedule(task)

    def run_schedule_now(self, schedule_id: str) -> Dict[str, Any]:
        job = self._request("POST", "/api/mcp/schedule-run", {
            "id": str(schedule_id or "").strip(),
        })
        return self._sanitize_job(job)

    def delete_content(self, target_type: str, target_id: str,
                       item_index: Optional[int] = None) -> Dict[str, Any]:
        result = self._request("POST", "/api/mcp/delete", {
            "target_type": target_type,
            "target_id": target_id,
            "item_index": item_index,
        })
        return {
            "deleted": bool(result.get("deleted")),
            "target_type": str(result.get("target_type") or ""),
            "target_id": str(result.get("target_id") or ""),
            "item_index": result.get("item_index"),
        }

    def status(self, job_id: str) -> Dict[str, Any]:
        job_id = self._validate_job_id(job_id)
        job = self._request("GET", "/api/mcp/job?" + urlencode({"id": job_id}))
        return self._sanitize_job(job)

    def pause(self, job_id: str) -> Dict[str, Any]:
        job_id = self._validate_job_id(job_id)
        job = self._request("POST", "/api/mcp/job-control", {
            "id": job_id, "action": "pause",
        })
        return self._sanitize_job(job)

    def resume(self, job_id: str) -> Dict[str, Any]:
        job_id = self._validate_job_id(job_id)
        job = self._request("POST", "/api/mcp/job-control", {
            "id": job_id, "action": "resume",
        })
        return self._sanitize_job(job)

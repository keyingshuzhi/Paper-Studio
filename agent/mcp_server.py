"""Paper Studio MCP Server（stdio）。

启动：``uv run python -B -m agent.mcp_server``

只读能力可直接调用；启动、下载、删除、记忆写入和定时任务通过
MCP Elicitation 请求用户明确确认，拒绝、取消或客户端不支持时默认失败。
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

from mcp.server import MCPServer
from mcp.server.mcpserver import Elicit, Resolve
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from .control_client import ResearchControlClient
from .read_service import PaperStudioReadService


mcp = MCPServer(
    "Paper Studio",
    instructions=(
        "Paper Studio 学术研究服务。可检索公开文献，读取本地文献库、"
        "Markdown 研究报告与人民币成本账本，也可在现有 Web/App 任务中心"
        "启动、查询、暂停和恢复研究。下载、删除、记忆写入、新建/修改/"
        "立即运行定时任务以及可能计费的启动研究都必须经过用户确认。"
        "不开放批量清空、任务取消或模型配置修改。"
    ),
)

_READ_OPEN = ToolAnnotations(read_only_hint=True, open_world_hint=True)
_READ_LOCAL = ToolAnnotations(read_only_hint=True, open_world_hint=False)
_START_RESEARCH = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
_CONTROL_RESEARCH = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
_WRITE_LOCAL = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)
_SCHEDULE_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=True,
)
_DELETE_LOCAL = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)


class PermissionApproval(BaseModel):
    """高风险 MCP 操作的用户确认回执。"""

    model_config = ConfigDict(extra="forbid")
    approved: bool = Field(
        description="仅当用户理解提示的目标和影响并同意执行时选择 true")


class MemoryPaperInput(BaseModel):
    """手动写入研究记忆时的文献条目。"""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(default="", max_length=2_000)
    source: str = Field(default="manual", min_length=1, max_length=100)
    authors: List[str] = Field(default_factory=list, max_length=100)
    year: Optional[int] = Field(default=None, ge=1900, le=2200)
    abstract: Optional[str] = Field(default=None, max_length=50_000)
    doi: Optional[str] = Field(default=None, max_length=500)
    pdf_url: Optional[str] = Field(default=None, max_length=2_000)
    venue: Optional[str] = Field(default=None, max_length=500)
    extra: Dict[str, Any] = Field(default_factory=dict)


class PaperSearchOutput(TypedDict):
    query: str
    sources: List[str]
    max_results_per_source: int
    year_from: Optional[int]
    count: int
    partial: bool
    warnings: List[Dict[str, Any]]
    papers: List[Dict[str, Any]]


class LibrarySearchOutput(TypedDict):
    keyword: str
    status: str
    total: int
    offset: int
    limit: int
    has_more: bool
    items: List[Dict[str, Any]]
    batches: List[Dict[str, Any]]


class ReportListOutput(TypedDict):
    keyword: str
    total: int
    offset: int
    limit: int
    has_more: bool
    reports: List[Dict[str, Any]]


class ReportReadOutput(TypedDict):
    id: str
    name: str
    title: str
    modified: str
    total_chars: int
    offset: int
    returned_chars: int
    has_more: bool
    next_offset: Optional[int]
    content: str


class CostOverviewOutput(TypedDict):
    currency: str
    current_period: str
    current_beijing_time: str
    peak_hours_beijing: List[str]
    ledger_available: bool
    ledger: Dict[str, Any]
    pricing_per_1m_tokens: Dict[str, Any]


class CostEstimateOutput(TypedDict):
    model: str
    currency: str
    price_period: str
    unit: str
    unit_prices: Dict[str, float]
    input_tokens: int
    cached_input_tokens: int
    uncached_input_tokens: int
    output_tokens: int
    calls: int
    estimated_cost_per_call_cny: float
    estimated_total_cny: float


class ResearchStatusOutput(TypedDict):
    id: str
    query: str
    mode: str
    status: str
    stage: str
    progress: int
    created_at: Optional[str]
    started_at: Optional[str]
    finished_at: Optional[str]
    elapsed_seconds: int
    report_id: Optional[str]
    error: Optional[str]
    latest_log: List[str]
    can_pause: bool
    can_resume: bool
    is_terminal: bool


class MemorySearchOutput(TypedDict):
    keyword: str
    entries: int
    total_papers: int
    items: List[Dict[str, Any]]


class MemoryReadOutput(TypedDict):
    query: str
    timestamp: str
    papers: List[Dict[str, Any]]
    summaries: List[Dict[str, Any]]
    analysis: Optional[Dict[str, Any]]


class MemoryWriteOutput(TypedDict):
    query: str
    timestamp: Optional[str]
    paper_count: int
    summary_count: int
    has_analysis: bool


class ScheduleOutput(TypedDict):
    id: str
    query: str
    enabled: bool
    interval_minutes: int
    mode: str
    max_results: int
    rounds: int
    branching: int
    max_queries: int
    last_run: Optional[str]
    last_job: Optional[str]


class ScheduleListOutput(TypedDict):
    schedules: List[ScheduleOutput]


class DeleteOutput(TypedDict):
    deleted: bool
    target_type: str
    target_id: str
    item_index: Optional[int]


def _preview(value: str, limit: int = 100) -> str:
    clean = " ".join(str(value or "").split())
    return clean if len(clean) <= limit else clean[:limit - 1] + "…"


def _require_approved(approval: PermissionApproval) -> None:
    if not approval.approved:
        raise PermissionError("用户未授权执行该操作")


def _confirm_start_research(query: str, mode: str) -> Elicit[PermissionApproval]:
    return Elicit(
        f"是否启动{('深度' if mode == 'deep' else '单轮')}研究「{_preview(query)}」？"
        "将在 Paper Studio 中新建任务、写入记忆与报告；"
        "如当前使用 DeepSeek，还会按 token 产生费用。",
        PermissionApproval,
    )


def _confirm_download(query: str, max_downloads: int,
                      mode: str) -> Elicit[PermissionApproval]:
    return Elicit(
        f"是否启动{('深度' if mode == 'deep' else '单轮')}研究「{_preview(query)}」，"
        f"并最多下载 {max_downloads} 篇公开文献？"
        "文件会写入 Paper Studio 文献库，下载按应用设置限速与重试；"
        "如当前使用 DeepSeek，研究还会按 token 产生费用。",
        PermissionApproval,
    )


def _confirm_memory_write(query: str, papers: Optional[List[MemoryPaperInput]],
                          summaries: Optional[List[Dict[str, Any]]],
                          notes: Optional[str]) -> Elicit[PermissionApproval]:
    return Elicit(
        f"是否写入研究记忆「{_preview(query)}」？"
        f"将保存 {len(papers or [])} 篇文献、{len(summaries or [])} 条摘要"
        f"{'及备注' if notes else ''}；同名查询的旧记忆会被替换。",
        PermissionApproval,
    )


def _confirm_schedule_save(query: str, schedule_id: Optional[str],
                           enabled: bool,
                           interval_minutes: int) -> Elicit[PermissionApproval]:
    action = "修改" if schedule_id else "创建"
    enabled_text = (
        f"启用后可能立即运行，此后每 {interval_minutes} 分钟自动启动研究"
        if enabled else "任务将保存为禁用，不会自动运行")
    return Elicit(
        f"是否{action}定时任务「{_preview(query)}」？{enabled_text}。"
        "每次使用应用当前模型；DeepSeek 模式会反复产生费用。",
        PermissionApproval,
    )


def _confirm_schedule_run(schedule_id: str) -> Elicit[PermissionApproval]:
    return Elicit(
        f"是否立即运行定时任务「{_preview(schedule_id)}」？"
        "这会新建一个研究任务；DeepSeek 模式可能产生费用。",
        PermissionApproval,
    )


def _confirm_delete(target_type: str, target_id: str,
                    item_index: Optional[int]) -> Elicit[PermissionApproval]:
    labels = {
        "report": "报告",
        "library_item": "文献条目",
        "library_batch": "整个文献批次",
        "memory": "研究记忆",
        "schedule": "定时任务",
        "research_record": "已结束的任务记录",
    }
    suffix = f"，条目序号 {item_index}" if item_index is not None else ""
    return Elicit(
        f"是否删除{labels.get(target_type, target_type)}「{_preview(target_id)}」{suffix}？"
        "该操作不可撤销，且仅会处理此处列出的单个目标。",
        PermissionApproval,
    )


def _service() -> PaperStudioReadService:
    # 每次解析环境变量，便于测试和桌面/开发环境切换数据目录。
    return PaperStudioReadService()


def _control() -> ResearchControlClient:
    return ResearchControlClient()


@mcp.tool(title="搜索学术文献", annotations=_READ_OPEN)
def search_papers(query: str, max_results: int = 10,
                  sources: Optional[List[str]] = None,
                  year_from: Optional[int] = None) -> PaperSearchOutput:
    """只读联网检索。来源可选 arxiv_search、scholar_search；上限按每个来源计算。"""
    return _service().search_papers(query, max_results, sources, year_from)


@mcp.tool(title="搜索本地文献库", annotations=_READ_LOCAL)
def search_library(keyword: str = "", status: str = "all",
                   limit: int = 50, offset: int = 0) -> LibrarySearchOutput:
    """只读浏览已下载文献的脱敏元数据，支持关键词、状态和分页。"""
    return _service().search_library(keyword, status, limit, offset)


@mcp.tool(title="列出研究报告", annotations=_READ_LOCAL)
def list_reports(keyword: str = "", limit: int = 50,
                 offset: int = 0) -> ReportListOutput:
    """只读列出 Markdown 研究报告及标题、摘要、时间和资源 URI。"""
    return _service().list_reports(keyword, limit, offset)


@mcp.tool(title="读取研究报告", annotations=_READ_LOCAL)
def read_report(report_id: str, offset: int = 0,
                max_chars: int = 40_000) -> ReportReadOutput:
    """只读分段读取指定报告；用 next_offset 继续读取超长报告。"""
    return _service().read_report(report_id, offset, max_chars)


@mcp.tool(title="查看成本概览", annotations=_READ_LOCAL)
def get_cost_overview() -> CostOverviewOutput:
    """只读查看人民币成本账本、预算状态以及 Flash/Pro 当前价格。"""
    return _service().cost_overview()


@mcp.tool(title="估算 DeepSeek 成本", annotations=_READ_LOCAL)
def estimate_cost(model: str, input_tokens: int = 0,
                  cached_input_tokens: int = 0,
                  output_tokens: int = 0,
                  calls: int = 1) -> CostEstimateOutput:
    """按当前北京时间价格只读估算 Flash 或 Pro 的 token 成本。"""
    return _service().estimate_cost(
        model, input_tokens, cached_input_tokens, output_tokens, calls)


@mcp.tool(title="搜索研究记忆", annotations=_READ_LOCAL)
def search_memory(keyword: str = "", limit: int = 100) -> MemorySearchOutput:
    """只读搜索记忆索引，返回查询、论文数和摘要数等轻量信息。"""
    return _service().search_memory(keyword, limit)


@mcp.tool(title="读取研究记忆", annotations=_READ_LOCAL)
def read_memory(query: str) -> MemoryReadOutput:
    """只读获取一条记忆的文献、摘要和分析明细。"""
    return _service().read_memory(query)


@mcp.tool(title="启动研究", annotations=_START_RESEARCH)
def start_research(
    query: Annotated[str, Field(min_length=1, max_length=500)],
    approval: Annotated[PermissionApproval, Resolve(_confirm_start_research)],
    mode: Literal["single", "deep"] = "deep",
    max_results: Annotated[int, Field(ge=1, le=50)] = 10,
    rounds: Annotated[int, Field(ge=1, le=5)] = 2,
    branching: Annotated[int, Field(ge=1, le=3)] = 1,
    max_queries: Annotated[int, Field(ge=1, le=20)] = 3,
) -> ResearchStatusOutput:
    """经用户确认后启动研究；使用当前模型和预算，云端可能计费。"""
    _require_approved(approval)
    return _control().start(
        query, mode, max_results, rounds, branching, max_queries)


@mcp.tool(title="启动研究并下载文献", annotations=_START_RESEARCH)
def start_research_with_download(
    query: Annotated[str, Field(min_length=1, max_length=500)],
    approval: Annotated[PermissionApproval, Resolve(_confirm_download)],
    mode: Literal["single", "deep"] = "deep",
    max_results: Annotated[int, Field(ge=1, le=50)] = 10,
    rounds: Annotated[int, Field(ge=1, le=5)] = 2,
    branching: Annotated[int, Field(ge=1, le=3)] = 1,
    max_queries: Annotated[int, Field(ge=1, le=20)] = 3,
    max_downloads: Annotated[int, Field(ge=1, le=50)] = 5,
) -> ResearchStatusOutput:
    """经确认启动研究并下载公开文献；继承应用的限速、重试和超时设置。"""
    _require_approved(approval)
    return _control().start_download(
        query, mode, max_results, rounds, branching, max_queries,
        max_downloads)


@mcp.tool(title="写入研究记忆", annotations=_WRITE_LOCAL)
def write_memory(
    query: Annotated[str, Field(min_length=1, max_length=500)],
    approval: Annotated[PermissionApproval, Resolve(_confirm_memory_write)],
    notes: Optional[Annotated[str, Field(max_length=50_000)]] = None,
    papers: Optional[Annotated[List[MemoryPaperInput],
                               Field(max_length=100)]] = None,
    summaries: Optional[Annotated[List[Dict[str, Any]],
                                  Field(max_length=100)]] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> MemoryWriteOutput:
    """经确认把结构化内容写入记忆；查询同名时替换旧记忆。"""
    _require_approved(approval)
    merged_analysis = dict(analysis or {})
    if notes:
        merged_analysis["notes"] = notes
    payload = {
        "query": query,
        "papers": [paper.model_dump(exclude_none=True)
                   for paper in (papers or [])],
        "summaries": list(summaries or []),
        "analysis": merged_analysis or None,
    }
    return _control().write_memory(payload)


@mcp.tool(title="列出定时任务", annotations=_READ_LOCAL)
def list_schedules() -> ScheduleListOutput:
    """只读列出当前定时研究任务与上次运行状态。"""
    return {"schedules": _control().list_schedules()}


@mcp.tool(title="保存定时任务", annotations=_SCHEDULE_WRITE)
def save_schedule(
    query: Annotated[str, Field(min_length=1, max_length=500)],
    approval: Annotated[PermissionApproval, Resolve(_confirm_schedule_save)],
    schedule_id: Optional[Annotated[
        str, Field(pattern=r"^schedule-[A-Za-z0-9._-]+$", max_length=128)
    ]] = None,
    enabled: bool = True,
    interval_minutes: Annotated[int, Field(ge=1, le=525_600)] = 1_440,
    mode: Literal["single", "deep"] = "deep",
    max_results: Annotated[int, Field(ge=1, le=50)] = 10,
    rounds: Annotated[int, Field(ge=1, le=5)] = 2,
    branching: Annotated[int, Field(ge=1, le=3)] = 1,
    max_queries: Annotated[int, Field(ge=1, le=20)] = 3,
) -> ScheduleOutput:
    """经确认创建或修改定时研究；启用的新任务可能立即运行。"""
    _require_approved(approval)
    return _control().save_schedule({
        "id": schedule_id,
        "query": query,
        "enabled": enabled,
        "interval_minutes": interval_minutes,
        "mode": mode,
        "max_results": max_results,
        "rounds": rounds,
        "branching": branching,
        "max_queries": max_queries,
    })


@mcp.tool(title="立即运行定时任务", annotations=_START_RESEARCH)
def run_schedule_now(
    schedule_id: Annotated[str, Field(min_length=1, max_length=128)],
    approval: Annotated[PermissionApproval, Resolve(_confirm_schedule_run)],
) -> ResearchStatusOutput:
    """经确认立即触发一次已保存的定时研究。"""
    _require_approved(approval)
    return _control().run_schedule_now(schedule_id)


@mcp.tool(title="删除 Paper Studio 内容", annotations=_DELETE_LOCAL)
def delete_content(
    target_type: Literal[
        "report", "library_item", "library_batch", "memory", "schedule",
        "research_record",
    ],
    target_id: Annotated[str, Field(min_length=1, max_length=500)],
    approval: Annotated[PermissionApproval, Resolve(_confirm_delete)],
    item_index: Optional[Annotated[int, Field(ge=1)]] = None,
) -> DeleteOutput:
    """经确认删除单个报告、文献/批次、记忆、定时任务或已结束任务记录。"""
    _require_approved(approval)
    return _control().delete_content(target_type, target_id, item_index)


@mcp.tool(title="查询研究状态", annotations=_READ_LOCAL)
def get_research_status(job_id: str) -> ResearchStatusOutput:
    """查询研究阶段、进度、最近日志和报告 ID，不修改任务。"""
    return _control().status(job_id)


@mcp.tool(title="暂停研究", annotations=_CONTROL_RESEARCH)
def pause_research(job_id: str) -> ResearchStatusOutput:
    """在安全检查点暂停排队中或运行中的研究；重复暂停不会产生额外影响。"""
    return _control().pause(job_id)


@mcp.tool(title="恢复研究", annotations=_CONTROL_RESEARCH)
def resume_research(job_id: str) -> ResearchStatusOutput:
    """恢复已暂停的研究；重复恢复不会创建新任务。"""
    return _control().resume(job_id)


@mcp.resource(
    "paper-studio://library",
    title="Paper Studio 文献库索引",
    mime_type="application/json",
)
def library_index() -> Dict[str, Any]:
    """最近的本地文献条目和全部批次索引。"""
    return _service().search_library(limit=100)


@mcp.resource(
    "paper-studio://library/{batch_id}",
    title="Paper Studio 文献批次",
    mime_type="application/json",
)
def library_batch(batch_id: str) -> Dict[str, Any]:
    """指定文献批次的完整脱敏元数据。"""
    return _service().get_library_batch(batch_id)


@mcp.resource(
    "paper-studio://reports",
    title="Paper Studio 报告索引",
    mime_type="application/json",
)
def reports_index() -> Dict[str, Any]:
    """最近报告和报告资源 URI 索引。"""
    return _service().list_reports(limit=100)


@mcp.resource(
    "paper-studio://reports/{report_id}",
    title="Paper Studio 研究报告",
    mime_type="text/markdown",
)
def report_resource(report_id: str) -> str:
    """读取指定 Markdown 报告的完整内容。"""
    return _service().read_report_full(report_id)


@mcp.resource(
    "paper-studio://cost",
    title="Paper Studio 成本概览",
    mime_type="application/json",
)
def cost_resource() -> Dict[str, Any]:
    """人民币成本账本、预算状态与 DeepSeek Flash/Pro 价格。"""
    return _service().cost_overview()


def main() -> None:
    """以标准输入/输出传输启动，stdout 仅用于 MCP 协议。"""
    mcp.run()


if __name__ == "__main__":
    main()

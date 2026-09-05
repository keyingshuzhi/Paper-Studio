# Paper Studio 学术研究 Agent（v0.1.0）

根据用户输入的关键词（或自然语言指令），跨多个权威平台检索、去重、排序学术文献，
并可下载论文原文、抽取文本、生成结构化研究报告。

## v0.1.0 体验升级

- 全新的 Paper Studio 品牌 Logo 与桌面图标，统一 Web、macOS 和 Windows 视觉识别。
- 移除 Web / App 的成本页面、预算表单、费用图表与调用明细，让工作区专注于研究本身。
- 模型设置升级为服务商档案：内置 Ollama、DeepSeek、OpenAI、OpenRouter、硅基流动、智谱、阿里百炼、火山方舟与 OneAPI，并可添加、编辑或删除任意 OpenAI 兼容服务商及其模型。
- 每个服务商可独立配置 Base URL、模型列表、默认模型、环境变量和 API Key；模型设置会持久化为安全的 `model_config.json`，桌面版使用 Electron `safeStorage` 加密保存多组凭据并在启动时自动恢复。
- Python 依赖与项目内虚拟环境统一交由 uv 管理；桌面打包自动使用 uv 的 `build` 依赖组。
- 重构导航、研究工作区、对比研究、报告阅读器、设置中心、浅色/暗色主题、反馈提示和响应式交互；新增 Web/App 共用的品牌启动动画，并兼容系统主题与“减少动态效果”偏好。
- 研究任务新增本机恢复队列：保存输入、选项、日志、结构化执行轨迹和安全检查点；Web/App 意外关闭后可在任务中心继续。
- 云端模型支持草稿级真实推理检测、短暂故障自动重试，以及在已配置的兼容云服务商之间自动故障切换。
- 文献库升级为本地阅读工作台：PDF 预览配合可选择的抽取文本，支持高亮、批注、摘录、标签、页码与引用/关键词定位；所有笔记仅保存在应用数据目录。
- 报告页支持章节目录、已下载文献跳转、不可变版本快照与恢复，以及离线导出 Markdown、Word 和 PDF。
- 可勾选已有本地文献启动“已有文献续研”，复用原文与元数据进行总结、对比和报告，不再重复外部检索。
- 文献列表提供可解释的辅助质量评分，综合来源、年份、引用量、全文可用性、重复度与查询相关性；评分不替代同行评审。

## 架构（DSH 三层）

```
agent/
├── skills/     原子能力层 (Skills)  —— 每个技能只做一件事
│   ├── SKILL.md            项目级学习资料汇总规则（自动注入规划器）
│   ├── base.py              标准 Skill 契约 + 延迟注册表
│   ├── metadata.py          统一数据结构 Paper
│   ├── contracts.py         核心研究能力共享 JSON Schema
│   ├── arxiv_skill.py       arXiv 官方 API 搜索
│   ├── scholar_skill.py     Semantic Scholar → Crossref 降级学术搜索
│   ├── scraper_skill.py     任意文献 URL → 元数据（DOI/arXiv/HTML meta）
│   ├── downloader_skill.py  下载文件 + PDF 文本抽取 + 文本清洗
│   ├── citation_skill.py    引用/被引获取（Semantic Scholar，带退避）
│   ├── summarizer_skill.py  单篇/批量结构化总结 Skill
│   ├── analysis_skill.py    跨论文对比与知识盲点 Skill
│   ├── citation_analysis_skill.py 引用网络聚合分析 Skill
│   ├── memory_skill.py      记忆搜索/读取/写入/删除/清空/统计 Skills
│   ├── report_skill.py      报告渲染/落盘 Skills
│   └── search_manager.py    多源聚合：并行调度、标题去重、来源优先级排序
├── plugins/    流程编排层 (Plugins) —— 组合技能完成业务闭环
│   ├── base.py              插件基类 + 注册表
│   ├── search_plugin.py     ComprehensiveSourceSearch：多源综合搜索
│   └── acquisition_plugin.py DataAcquisitionPipeline：下载+抽取→资料包
├── core/       控制调度层           —— 规划、决策、摘要、报告
│   ├── planner.py          规则规划器（意图解析，兜底）
│   ├── llm.py              LLM 客户端（OpenAI 兼容协议）
│   ├── llm_planner.py      LLM 规划器（复杂意图解析，失败自动降级）
│   ├── summarizer.py       论文智能摘要器（问题/方法/贡献/局限）
│   ├── analyzer.py         跨文献分析器（共识/分歧/演进/知识盲点）
│   ├── citation_analyzer.py 引用网络分析器（核心被引文献/互引）
│   ├── memory.py           研究记忆库（跨会话持久化）
│   ├── research_loop.py    深度研究闭环（V3.0 多轮自主研究）
│   ├── multi_topic.py      多主题对比研究（V5.0）
│   ├── json_utils.py       LLM 结构化 JSON 解析工具
│   ├── reporter.py         报告生成：单轮 / 深度 / 多主题对比
│   ├── agent.py            ResearchAgent 门面：总控调度入口
│   └── config.py           .env 轻量加载
├── scheduler.py            定时自动研究调度器（V5.0）
├── read_service.py         只读数据层（搜索/文献库/报告/成本，路径脱敏）
├── control_client.py       MCP → Web/App 本机鉴权任务控制通道
├── mcp_client.py           外部 MCP 连接、能力发现、权限与调用中心
├── mcp_server.py           MCP v2 Server（数据读取 + 研究控制，stdio）
├── webapp.py               Web 界面（V5.0，纯标准库）
└── cli.py                  命令行入口
```

## Skill 标准契约

所有原子能力继续支持原有的 `execute(**kwargs)` 调用；MCP、外部插件和新代码应使用
`invoke(**kwargs)`。标准入口会完成输入/输出 JSON Schema 校验、权限检查、超时控制、
结构化进度回调，并统一返回 `SkillResult`：

```python
from agent.skills import ArxivSkill, SkillPermission

events = []
result = ArxivSkill().invoke(
    query="agent harness",
    max_results=5,
    timeout_seconds=60,
    allowed_permissions={SkillPermission.NETWORK},
    progress_callback=events.append,
)
if result.ok:
    papers = result.unwrap()
else:
    print(result.error.code, result.error.message)
```

`BaseSkill.manifests()` 无需实例化即可返回全部 Skill 的 Schema、权限、版本和默认
超时，供设置界面及后续 MCP Server 做能力发现。文件、网络和付费 API 权限均采用
显式声明；未传 `allowed_permissions` 时保留旧版内部调用的兼容行为。

### 核心研究 Skills

核心研究能力已经通过 Skill 适配层进入默认研究编排，并保留原核心引擎的 Python
接口以兼容已有调用：

| 能力 | Skill ID | 权限 |
| --- | --- | --- |
| 单篇/批量总结 | `paper_summarize` / `paper_summarize_batch` | 网络、可能产生 API 费用 |
| 跨论文对比 | `paper_compare` | 网络、可能产生 API 费用 |
| 引用网络分析 | `citation_analyze` | 网络 |
| 记忆查询 | `memory_search` / `memory_read` / `memory_stats` | 文件读取 |
| 记忆变更 | `memory_write` / `memory_delete` / `memory_clear` | 文件写入；删除操作另需 destructive 权限 |
| 报告生成 | `report_render` / `report_write` | 纯渲染无权限；落盘需要文件写入 |
| 本地文献库 RAG | `library_rag` | 网络（embedding 调用）、文件读写 |

记忆和报告特意拆分成只读、写入和删除入口，便于 MCP 宿主按最小权限授权。

## 本地文献库 RAG（v0.1.0）

仅基于已下载的 PDF 回答问题，结论带页码和原文引用片段，**不引入新依赖**：

- **Embedding**：复用现有 LLM 服务商配置。优先本地 Ollama（`nomic-embed-text` /
  `mxbai-embed-large`），可降级到任意 OpenAI 兼容云端。
- **索引存储**：纯 JSON（`downloads/library_index.json`），原子写不损坏。
- **检索**：纯标准库余弦相似度，论文级数据集毫秒级返回。
- **增量**：按 PDF 文件指纹跳过未变化文件，切换 embedding 模型自动重建。

```python
from agent.skills import LibraryRagSkill

rag = LibraryRagSkill(index_path="downloads/library_index.json")
rag.build_index()                  # 扫描 downloads/*/papers/*.pdf 建索引
hits = rag.query("Mamba 的核心思想是什么?", top_k=5)
answer = rag.ask("Mamba 与 Transformer 的核心区别是什么?")
# answer["answer"]    文本回答
# answer["citations"] 引用列表,含 paper_id / page / quote / score
```

在 Web/桌面界面暴露的"文献库对话"页可作为后续任务实现。
报告写入 Skill 只接受不含目录的 `.md` 文件名，阻止通过文件名越出指定报告目录。

## MCP Server

MCP Server 采用官方 Python SDK v2 和 stdio 传输。数据能力保持只读，并接入
Web/App 的同一任务中心：

| MCP Tool | 能力 | 数据边界 |
| --- | --- | --- |
| `search_papers` | 搜索 arXiv、Semantic Scholar/Crossref | 只读联网，不下载 |
| `search_library` | 搜索本地文献库元数据 | 不返回绝对路径 |
| `list_reports` / `read_report` | 列出、分页读取报告 | 仅 `downloads/*.md` |
| `get_cost_overview` | 查询账本、预算和 Flash/Pro 价格 | 不包含 API Key |
| `estimate_cost` | 按当前北京时间价格估算成本 | 纯本地计算 |
| `search_memory` / `read_memory` | 搜索、查看研究记忆 | 只读，本地路径脱敏 |
| `start_research` | 使用应用当前模型、预算启动研究 | **需确认**；可能产生云端费用 |
| `start_research_with_download` | 启动研究并限速下载公开文献 | **需确认**；写入文献库并可能计费 |
| `write_memory` | 写入文献、摘要、分析或备注 | **需确认**；同名记忆会替换 |
| `list_schedules` | 列出定时研究任务 | 只读 |
| `save_schedule` / `run_schedule_now` | 创建、修改或立即运行定时研究 | **需确认**；启用后可反复计费 |
| `delete_content` | 删除单个报告、文献/批次、记忆、定时任务或已结束任务记录 | **需确认**；不可撤销 |
| `get_research_status` | 查询阶段、进度、最近日志与报告 ID | 只读 |
| `pause_research` / `resume_research` | 在安全检查点暂停或恢复 | 非破坏性、可重复调用 |

同时提供以下 Resources：

- `paper-studio://library` 与 `paper-studio://library/{batch_id}`
- `paper-studio://reports` 与 `paper-studio://reports/{report_id}`
- `paper-studio://cost`

权限确认使用 MCP `Resolve` + `Elicit` 表单：确认参数由协议层注入，
不出现在 Tool 输入 Schema 中，模型无法自行伪造。用户拒绝、取消，或 MCP
宿主未宣告表单征询能力时，操作在进入 Web/App 控制端点前即失败。
下载继承模型设置页的速率、重试与超时配置；删除只支持单个明确目标，
不开放批量清空。任务取消和模型/密钥配置仍未对 MCP 开放。

研究控制要求 Paper Studio Web 版或桌面 App 正在运行。Web 后端会在应用数据目录
原子发布仅含本机端口和随机令牌的 `mcp_runtime.json`（权限 `0600`），MCP 只连接
`127.0.0.1` 的专用鉴权接口。任务会直接出现在 App 任务队列中；关闭应用后，运行中
任务会被恢复为“待恢复”，保留最后一个安全检查点、输入、日志与执行轨迹。用户确认继续后，
系统从可安全重放的边界重新调度，不会把中途的网络请求误标记为已完成。

直接启动（stdio 不监听端口）：

```bash
uv run python -B -m agent.mcp_server
```

宿主配置示例。stdio 宿主应使用绝对路径；`PAPER_STUDIO_DATA_DIR` 可指向 Web
版的 `downloads`，也可指向桌面版的
`~/Library/Application Support/Paper Studio/downloads`：

```json
{
  "mcpServers": {
    "paper-studio": {
      "command": "uv",
      "args": ["run", "--directory", "/绝对路径/paper-studio", "python", "-B", "-m", "agent.mcp_server"],
      "env": {
        "PYTHONPATH": "/绝对路径/paper-studio",
        "PAPER_STUDIO_DATA_DIR": "/绝对路径/Paper Studio/downloads"
      }
    }
  }
}
```

协议与控制链路验证：

```bash
uv run python -B examples/test_mcp_server.py
uv run python -B examples/test_mcp_control.py
uv run python -B examples/test_mcp_permissions.py
```

### MCP Client（外部连接）

Paper Studio 同时可作为 MCP Client。在“设置 → MCP 连接”中可添加：

- `stdio`：本地文献管理器、文件系统 Server 或本地知识库；
- `Streamable HTTP`：远程知识库、机构数据库或统一 MCP Gateway。

连接中心支持发现 Tools、Resources、Resource Templates 和 Prompts，
读取已授权的静态 Resource，以及在每次确认后调用外部 Tool。
当前采用短连接会话：每次发现、读取或调用完成后会关闭连接，
避免后台进程与网络会话泄漏。

安全边界：

- 新连接默认为“未信任”，不会启动进程或访问网络；
- 首次信任、删除连接和每次 Tool 调用使用短时、单次、目标与参数绑定的权限令牌；
- Tool 调用默认关闭；Resource 读取可按连接独立开关；
- stdio 命令使用 argv 直接启动，不经过 Shell；
- HTTP URL 不允许内嵌账号、密钥或查询串；非本机连接必须使用 HTTPS；
- `downloads/mcp_connections.json` 权限为 `0600`，仅保存环境变量名的映射，
  不保存 token 或密钥值；
- 外部 Resource / Tool 结果最多载入 2 MiB，超限文本截断、二进制内容省略。

凭据映射示例（页面中每行一项）：

```text
# stdio：子进程变量 = Paper Studio 宿主环境变量
ZOTERO_TOKEN=PAPER_STUDIO_ZOTERO_TOKEN

# HTTP：Header = Paper Studio 宿主环境变量
Authorization=PAPER_STUDIO_INSTITUTION_TOKEN
```

MCP Client 协议验证：

```bash
uv run python -B examples/test_mcp_client.py
uv run python -B examples/test_mcp_client_http.py
uv run python -B examples/test_mcp_client_web.py
```

## 环境（由 uv 统一管理）

```bash
# 第一次使用：uv 会创建项目内 .venv/ 并依据 uv.lock 同步依赖
uv sync

# 打包桌面版时再安装 PyInstaller 构建组
uv sync --group build
```

## 快速开始

```bash
# 方式一：命令行
uv run python -B -m agent.cli "transformer"                    # 检索 + 报告
uv run python -B -m agent.cli "下载关于llm的论文" --max-downloads 3  # 检索 + 下载
uv run python -B -m agent.cli "llm agent" --summarize          # 检索 + LLM 智能摘要
uv run python -B -m agent.cli "mamba" --summarize --analyze    # 摘要 + 跨文献分析
uv run python -B -m agent.cli "mamba" --summarize --summarize-limit 3  # 最多摘要 3 篇

# 方式二：Python API
uv run python -B examples/demo_search.py "mamba state space model" 5
```


## Web 界面（v0.1.0）

纯标准库实现的浏览器界面（零新依赖）：

```bash
uv run python -B -m agent.webapp --port 8765
# 浏览器打开 http://127.0.0.1:8765
```

功能：提交单轮、深度闭环与多主题对比研究，管理任务、计划、报告、文献和记忆，
并在 Web 中管理多家模型服务、运行标准 Skill，以及管理 Paper Studio 作为 MCP Server 和
MCP Client 的双重能力。任务在后台线程执行，页面自动同步运行状态。

应用内还提供以下工作台能力：

- **模型服务中心**：内置 Ollama、DeepSeek、OpenAI、OpenRouter、硅基流动、智谱、阿里百炼、火山方舟和 OneAPI，可添加任意 OpenAI 兼容服务；每个档案独立管理地址、模型列表、默认模型、Key 环境变量和凭据状态。
- **现代应用体验**：首次载入提供 Web/App 共用的品牌启动动画；导航可折叠，报告、文献与记忆工作区使用独立滚动区域，菜单、筛选、空状态和浅色/暗色配色均按桌面应用交互统一。
- **研究参数**：手动研究、多主题对比与定时计划均可设置来源、起始年份、摘要上限、引用分析和公开 PDF 下载策略。
- **多主题对比**：在独立的「对比研究」页面中一次输入 2–6 个主题，任务进入统一队列，生成可管理的对比报告。
- **任务控制与恢复**：运行中的研究可暂停、继续或取消。暂停与取消在当前网络请求、下载或单篇摘要结束后的安全检查点生效；应用关闭后的活动任务自动变为“待恢复”，用户可从上次安全检查点继续。
- **完整执行轨迹**：任务中心会保存并展示研究输入、检索计划与结果、模型输入/输出、模型重试、服务商切换、失败原因、报告生成及检查点；日志管道强制 UTF-8，自动合并 `print()` 分片、移除 ANSI 控制码并还原 `\uXXXX` 中文转义。
- **人工介入**：暂停或待恢复的任务可修改研究查询、逐篇排除论文、补充检索方向后继续；修改查询或排除论文会清晰地重置旧检查点，避免混合旧问题或已排除论文的证据。
- **任务检索与筛选**：可按主题或任务编号搜索，按执行中、待恢复、已完成、失败和已取消筛选，并可一键复制完整日志。
- **队列管理**：已完成、已取消和失败的任务可单独删除或一键清空；不会删除关联报告。
- **定时任务**：在「定时」页创建、启用、停用、立即执行或删除自动研究计划；计划会完整保留检索范围、引用和下载参数。
- **报告阅读与管理**：在「报告」页查看历史 Markdown 报告、不必离开应用；提供目录导航、已下载文献跳转、版本快照/恢复和 Markdown、Word、PDF 离线导出，也可在系统文件管理器中显示原文件或删除应用下载目录内的报告。
- **本地文献库**：按下载批次查看、搜索、筛选和质量评分排序 PDF；内置阅读工作台可预览 PDF、在抽取文本中高亮、批注、摘录、打标签和定位引用。可勾选已有文献继续研究而不重新检索；删除单篇时保留元数据和「已删除」状态，方便溯源。
- **语义记忆与知识图谱**：在「记忆」页离线语义搜索历史研究、论文、方法、结论与盲点，并查看主题—论文—作者—方法—结论—盲点关系图；支持固定、归档、合并、设置有效期、安全清理及 Markdown / JSON 导出。新研究会自动复用相关历史结论，并在任务轨迹与报告中明确列出复用来源，同时继续用新文献交叉验证。
- **Skill 能力中心**：查看全部核心 Skill 的 Schema、权限和默认超时，以统一 `SkillResult` 执行并展示进度；网络、写入、付费或删除操作需要用户确认。
- **MCP 双角色**：设置页提供 MCP Server 状态和宿主配置；MCP Client 可发现 Tools、Resources、Resource Templates 和 Prompts，并按连接权限安全调用。

### 文献下载与限流保护

- Web / 桌面端每来源默认检索 10 条，可设置 1–50；下载数量默认 10，可设置 1–50，不再固定为 5 篇。
- 深度研究先合并所有轮次并全局去重，之后只执行一次批量下载；`max_downloads` 是整个任务的上限，不会在每个派生查询中重复计算。
- 下载器按主机名全局限速（默认间隔 2 秒），对 HTTP 429 和临时服务错误指数退避重试。「设置 → 运行与下载」可调整下载间隔、重试次数和超时。
- 每篇处理后原子更新 `metadata.json`，单篇失败不会中断整批；临时文件使用 `.part` 后缀，验证 PDF 签名成功后才替换正式文件。
- 遵循 `agent/skills/SKILL.md` 的可获取性和版权合规原则：只下载明确的公开 PDF 地址。只有 DOI/出版社页面时保留元数据并标记「无公开 PDF」，不会把 HTML 错当成 PDF。

### 多模型服务商

- **智能选择（默认）**：优先使用已启动的 Ollama；不可用时选择首个配置完整的云端服务商。
- **Ollama**：本地推理，不产生 API token 费用。先执行 `ollama serve`，再拉取模型，例如 `ollama pull gemma4:e4b`。
- **云端与兼容服务**：内置 DeepSeek、OpenAI、OpenRouter、硅基流动、智谱、阿里百炼和火山方舟连接模板，并提供 OneAPI 自部署网关模板；也可连接机构网关、私有部署或其他 OpenAI Chat Completions 兼容 API。
- **自定义模型**：模型名称始终允许手动录入，也可通过兼容服务的 `GET /models` 自动发现。
- **真实可用性检测**：可在保存前使用当前 Base URL、模型与新输入的 Key 测试草稿；测试会向所选模型发出一次不流式、最多 1 个输出 token 的请求，验证地址、凭据、权限和模型推理能力。不会把“已填 Key”误显示为“模型可用”，临时 Key 也不会写入设置文件。
- **自动重试与切换**：HTTP 429、超时、连接错误和 5xx 会采用短暂指数退避重试；仍失败时仅在已配置、可用的 OpenAI 兼容云服务商之间切换，并在任务轨迹中保留原因与目标模型。
- **模型配置文件**：设置 → 模型配置可查看、刷新和复制 `model_config.json`。该文件可安全备份服务商、模型、路由和超时配置；其中只保存凭据引用和管理策略，绝不保存 API Key 明文。
- **持久化凭据**：桌面版会为每个服务商使用操作系统加密安全存储，并在每次启动时自动恢复，无需重复填写。Key 不回显、不写入报告，也不会写入 `model_config.json`。独立 Web 服务如需跨重启持久化，应使用服务商对应的环境变量。
- **请求超时**：在设置中调整单次模型请求的超时秒数（10–1800，默认 90）。本地模型可按设备性能延长，网络不稳定的云端服务也可适当增加。
- **成本页面**：v0.1.0 已移除成本页面及其所有界面功能；云端服务产生的费用请在对应服务商控制台查看。

## Electron 桌面应用（v0.1.0）

桌面版在本机启动 Python 后端，不会将论文或 API Key 发送给 Electron 之外的服务；
报告、下载内容和研究记忆保存到操作系统的应用数据目录。

```bash
cd desktop
npm install
npm start
```

构建当前平台安装包：

```bash
cd desktop
npm run dist
```

桌面构建由 `uv run --group build` 自动同步 Python 环境并运行 PyInstaller，不再依赖手动准备的 `.venv`。`npm run dist` 会自动识别宿主系统并先生成不依赖目标电脑 Python 环境的自包含后端：Apple Silicon Mac 输出 arm64 的 DMG/ZIP，Windows x64 输出 NSIS 安装程序/ZIP。每个目标平台必须在对应原生系统构建，详见 [desktop/README.md](desktop/README.md)。

macOS 应用壳使用 `desktop/assets/icon.icns`，Windows 使用 `desktop/assets/icon.png`；Web 页面、启动动画和应用内部关于页继续使用 `agent/static/assets/paper-studio-logo.png`。发布包只包含程序与静态资源，不包含 `.env`、API Key、模型配置、报告、PDF、记忆、任务或定时计划。当前 macOS 构建未配置 Apple Developer ID 签名与公证，首次启动可能需要右键选择“打开”。
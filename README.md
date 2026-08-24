# Paper Studio 学术研究 Agent（v0.0.4）

根据用户输入的关键词（或自然语言指令），跨多个权威平台检索、去重、排序学术文献，
并可下载论文原文、抽取文本、生成结构化研究报告。

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

记忆和报告特意拆分成只读、写入和删除入口，便于 MCP 宿主按最小权限授权。
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
`127.0.0.1` 的专用鉴权接口。任务会直接出现在 App 任务队列中；关闭应用后，当前
进程内任务状态结束，但已经生成的报告、文献和成本账本仍会保留。

直接启动（stdio 不监听端口）：

```bash
.venv/bin/python -B -m agent.mcp_server
```

宿主配置示例。stdio 宿主应使用绝对路径；`PAPER_STUDIO_DATA_DIR` 可指向 Web
版的 `downloads`，也可指向桌面版的
`~/Library/Application Support/Paper Studio/downloads`：

```json
{
  "mcpServers": {
    "paper-studio": {
      "command": "/绝对路径/paper-studio/.venv/bin/python",
      "args": ["-B", "-m", "agent.mcp_server"],
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
.venv/bin/python -B examples/test_mcp_server.py
.venv/bin/python -B examples/test_mcp_control.py
.venv/bin/python -B examples/test_mcp_permissions.py
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
.venv/bin/python -B examples/test_mcp_client.py
.venv/bin/python -B examples/test_mcp_client_http.py
.venv/bin/python -B examples/test_mcp_client_web.py
```

## 环境（全部在项目内）

```bash
# 虚拟环境位于项目内 .venv/，系统 Python 零污染
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 快速开始

```bash
# 方式一：命令行
.venv/bin/python -B -m agent.cli "transformer"                    # 检索 + 报告
.venv/bin/python -B -m agent.cli "下载关于llm的论文" --max-downloads 3  # 检索 + 下载
.venv/bin/python -B -m agent.cli "llm agent" --summarize          # 检索 + LLM 智能摘要
.venv/bin/python -B -m agent.cli "mamba" --summarize --analyze    # 摘要 + 跨文献分析
.venv/bin/python -B -m agent.cli "mamba" --summarize --summarize-limit 3  # 最多摘要 3 篇

# 方式二：Python API
.venv/bin/python -B examples/demo_search.py "mamba state space model" 5
```

## 论文智能摘要（V2.0）

配置 LLM 后，为每篇文献提炼结构化摘要，写入报告「文献智能摘要」区块：

```bash
.venv/bin/python -B -m agent.cli "mamba state space model" --max-results 3 --summarize
```

每篇摘要包含四个要素 + 关键词：

- **问题 (Problem)**：该论文要解决的研究问题
- **方法 (Method)**：提出的方法/模型/核心思路
- **贡献 (Contribution)**：主要创新点
- **局限 (Limitation)**：已知局限（论文未明说时标注"（推断）"）

设计要点：
- 长文本自动截断（默认 16000 字符），控制 token 成本
- 批量摘要并发执行（默认 2 线程），单篇失败不影响整体
- 优先使用已下载论文的全文，其次使用元数据摘要
- 未配置 LLM 时自动降级为基于摘要文本的简化摘要

## 跨文献对比与知识盲点（V2.0）

综述专家能力：对多篇论文做横向分析，识别共识、分歧、技术演进与研究空白：

```bash
.venv/bin/python -B -m agent.cli "mamba" --max-results 3 --summarize --analyze
```

输出四个区块（写入报告）：

- **共识点**：多篇论文一致认可的结论（标注论文编号）
- **分歧点**：不同论文观点/方法互相矛盾之处（双方观点并列）
- **演进路径**：技术/思想的先后演进脉络
- **知识盲点**：现有文献未覆盖的问题 + **建议检索关键词**（可直接用于下一轮研究）

输入画像优先用智能摘要（四要素），无摘要时降级用元数据。未配置 LLM 时自动跳过。

## LLM 规划器（V2.0）

在项目根目录创建 `.env`（参考 `.env.example`）配置任一家 OpenAI 兼容服务，
即可启用 LLM 复杂意图解析；未配置时自动降级为规则规划器。

```bash
# .env（任选一家）
LLM_API_KEY=sk-xxxx
LLM_BASE_URL=https://api.deepseek.com/v1   # DeepSeek
LLM_MODEL=deepseek-chat
```

启用后支持的自然语言指令示例：

| 用户输入 | LLM 解析结果 |
| --- | --- |
| "下载近三年关于mamba的论文，只搜arxiv" | query=mamba state space model, year_from=近三年, sources=[arxiv_search], download=true |
| "找2023年以后attention综述，每来源5篇" | query=attention survey, year_from=2023, max_results=5 |
| "随便搜点llm agent的资料" | query=llm agent, sources=null, download=false |

验证测试（无需真实 Key）：

```bash
.venv/bin/python -B examples/test_llm_planner.py
```

## Python API 示例

```python
from agent.core import ResearchAgent

agent = ResearchAgent()
result = agent.run("下载关于transformer的论文", max_results=5,
                   max_downloads=2, summarize=True, analyze=True)

print(result["papers"])          # 去重排序后的 Paper 列表
print(result["summaries"])       # 智能摘要列表（问题/方法/贡献/局限）
print(result["analysis"])        # 跨文献分析（共识/分歧/演进/盲点）
print(result["report_path"])     # 生成的 Markdown 报告路径
print(result["acquisition"])     # 下载统计（成功/失败/资料目录）
```

## 产出物结构

```
downloads/
├── report_YYYYMMDD_HHMMSS.md    单轮研究报告（文献清单 + 摘要 + 分析）
├── deep_report_YYYYMMDD_HHMMSS.md 深度研究报告（V3.0 多轮闭环）
├── cost_ledger.json             成本、预算与调用记录（不含 API Key）
├── research_memory.json         研究记忆库（V4.0 跨会话持久化）
├── search_result.json           检索结果 JSON
└── <run_id>/
    ├── metadata.json            元数据 + 下载清单
    ├── papers/01_<标题>.pdf     已下载的 PDF
    └── texts/01_<标题>.txt      抽取出的纯文本
```

## 深度研究闭环（V3.0）

自主多轮研究：**检索 → 摘要 → 跨文献分析 → 盲点关键词自动触发下一轮**，
直至预算上限或不再产生新盲点，合并为一份深度研究报告：

```bash
.venv/bin/python -B -m agent.cli "mamba" --deep
.venv/bin/python -B -m agent.cli "mamba" --deep --rounds 3 --branching 2 --max-queries 7
```

预算控制（防止查询数 2^n 爆炸）：

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| `--rounds` | 3 | 最大研究轮数 |
| `--branching` | 2 | 每轮最多衍生的盲点查询数 |
| `--max-queries` | 7 | 总查询数上限（含第 1 轮） |

深度报告包含：**研究路径树**（查询如何从用户输入分支出去）、
**跨轮去重汇总清单**、**各轮文献+摘要+分析详情**、**引用网络分析**。

## 研究记忆（V4.0）

每次深度研究自动存入 `downloads/research_memory.json`：

- **跨会话去重**：同一主题再次研究时，已检索过的查询直接命中记忆，复用历史文献与摘要，不重复花 LLM 费用
- **研究延续**：记忆中的盲点分析仍会派生下一轮查询，增量式深挖
- 历史查询可在 `ResearchMemory.all_queries()` 查看

```bash
.venv/bin/python -B -m agent.cli "mamba" --deep   # 第一次：完整研究并写入记忆
.venv/bin/python -B -m agent.cli "mamba" --deep   # 第二次：命中记忆，跳过重复检索
.venv/bin/python -B -m agent.cli "mamba" --deep --no-memory  # 强制重新研究
```

## 引用网络分析（V4.0）

深度研究结束后，对语料自动做引用分析（Semantic Scholar，尽力而为）：

- **核心被引文献**：语料参考文献中出现最多的文献（领域枢纽，供溯源精读）
- **语料内互引**：语料中论文之间的引用关系
- 限流/失败自动降级，不影响主流程；`--no-citations` 可关闭

## Web 界面（v0.0.4）

纯标准库实现的浏览器界面（零新依赖）：

```bash
.venv/bin/python -B -m agent.webapp --port 8765
# 浏览器打开 http://127.0.0.1:8765
```

功能：提交单轮、深度闭环与多主题对比研究，管理任务、计划、报告、文献、记忆和成本，
并在 Web 中直接配置 Ollama / DeepSeek、运行标准 Skill，以及管理 Paper Studio 作为 MCP Server 和
MCP Client 的双重能力。任务在后台线程执行，页面自动同步运行状态。

应用内还提供以下工作台能力：

- **模型配置**：填写 Ollama / DeepSeek 服务地址；点击「读取 Ollama 模型」发现本机已安装模型，或手动输入任意已部署模型名称。
- **研究参数**：手动研究、多主题对比与定时计划均可设置来源、起始年份、摘要上限、引用分析和公开 PDF 下载策略。
- **多主题对比**：在独立的「对比研究」页面中一次输入 2–6 个主题，任务进入统一队列，生成可管理的对比报告。
- **任务控制**：运行中的研究可暂停、继续或取消。暂停与取消在当前网络请求、下载或单篇摘要结束后的安全检查点生效，避免损坏资料与报告。
- **任务进度与中文日志**：任务中心展示规划、检索、下载、摘要、分析、引用和报告阶段，同时显示进度条与耗时。日志管道强制 UTF-8，自动合并 `print()` 分片、移除 ANSI 控制码并还原 `\uXXXX` 中文转义。
- **任务检索与筛选**：可按主题或任务编号搜索，按执行中、已完成、失败和已取消筛选，并可一键复制完整日志。
- **队列管理**：已完成、已取消和失败的任务可单独删除或一键清空；不会删除关联报告。
- **定时任务**：在「定时」页创建、启用、停用、立即执行或删除自动研究计划；计划会完整保留检索范围、引用和下载参数。
- **报告阅读与管理**：在「报告」页查看历史 Markdown 报告、不必离开应用；可在系统文件管理器中显示原文件，或删除应用下载目录内的报告。
- **本地文献库**：按下载批次查看、搜索和筛选 PDF；可直接打开、在文件夹中定位、删除单篇本地文件或整批资料。删除单篇时保留元数据和「已删除」状态，方便溯源。
- **记忆管理**：在「记忆」页搜索历史查询或论文标题、查看保存的论文与研究盲点，并可删除单条或清空全部记忆；删除后同一主题将重新检索。
- **Skill 能力中心**：查看全部核心 Skill 的 Schema、权限和默认超时，以统一 `SkillResult` 执行并展示进度；网络、写入、付费或删除操作需要用户确认。
- **MCP 双角色**：设置页提供 MCP Server 状态和宿主配置；MCP Client 可发现 Tools、Resources、Resource Templates 和 Prompts，并按连接权限安全调用。

### 文献下载与限流保护

- Web / 桌面端每来源默认检索 10 条，可设置 1–50；下载数量默认 10，可设置 1–50，不再固定为 5 篇。
- 深度研究先合并所有轮次并全局去重，之后只执行一次批量下载；`max_downloads` 是整个任务的上限，不会在每个派生查询中重复计算。
- 下载器按主机名全局限速（默认间隔 2 秒），对 HTTP 429 和临时服务错误指数退避重试。「模型与费用设置」可调整下载间隔、重试次数和超时。
- 每篇处理后原子更新 `metadata.json`，单篇失败不会中断整批；临时文件使用 `.part` 后缀，验证 PDF 签名成功后才替换正式文件。
- 遵循 `agent/skills/SKILL.md` 的可获取性和版权合规原则：只下载明确的公开 PDF 地址。只有 DOI/出版社页面时保留元数据并标记「无公开 PDF」，不会把 HTML 错当成 PDF。

### Ollama / DeepSeek 双模式与预算保护

- **自动模式（默认）**：优先使用已启动的 Ollama；不可用时才使用已配置的 DeepSeek。
- **Ollama**：本地推理，不产生 API token 费用。先执行 `ollama serve`，再拉取模型，例如 `ollama pull gemma4:e4b`。
- **DeepSeek**：桌面版输入 Key 后会使用操作系统的加密安全存储保存，并在下次启动时自动加载；Key 不会回显或写入报告。浏览器模式仍仅在当前进程保留，也可通过 `.env` 配置 `LLM_API_KEY`。
- **预算**：设置会话 CNY 上限后，每次 DeepSeek 请求均按「缓存未命中输入 + 最大输出」的保守价格预检；预计越界即拒绝请求。实际 token 消耗会在「成本」页记录。
- **成本工作台**：成本页按云端 / 本地调用拆分，展示预算进度、剩余额度、80% 预警与拦截记录；调用明细可按服务商、用途或模型筛选。清空仅重置本次应用会话的统计，不影响实际云端账单。
- **请求超时**：在模型设置中调整单次模型请求的超时秒数（10–600，默认 90）。本地模型可按设备性能设为 180–300 秒，网络不稳定的云端服务也可适当增加。

当前 Web 内置的 DeepSeek 官方价格（人民币 / 每 1M tokens）仅包含 Flash 和 Pro：

| 模型 | 时段 | 输入（缓存未命中） | 输入（缓存命中） | 输出 |
| --- | --- | ---: | ---: | ---: |
| `deepseek-v4-flash` | 高峰 | ¥3.00 | ¥0.10 | ¥9.00 |
| `deepseek-v4-flash` | 空闲 | ¥1.50 | ¥0.05 | ¥4.50 |
| `deepseek-v4-pro` | 高峰 | ¥9.00 | ¥0.30 | ¥27.00 |
| `deepseek-v4-pro` | 空闲 | ¥4.50 | ¥0.15 | ¥13.50 |

高峰时段为北京时间 09:00–12:00、14:00–18:00，其余时间按空闲价格。

价格会调整，使用前请以 [DeepSeek 官方定价页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing) 为准。

## Electron 桌面应用（v0.0.4）

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

构建前需要在项目根目录准备 `.venv` 并安装 `requirements-build.txt`。`npm run dist` 会自动识别宿主系统并先生成不依赖目标电脑 Python 环境的自包含后端：Apple Silicon Mac 输出 arm64 的 DMG/ZIP，Windows x64 输出 NSIS 安装程序/ZIP。每个目标平台必须在对应原生系统构建，详见 [desktop/README.md](desktop/README.md)。

## 定时自动研究（V5.0）

用 JSON 配置研究任务，按间隔自动执行：

```bash
# 创建 tasks.json 后
.venv/bin/python -B -m agent.scheduler tasks.json --once   # 执行到期任务一次
.venv/bin/python -B -m agent.scheduler tasks.json          # 后台循环
```

任务示例：

```json
{"tasks": [
  {"id": "mamba-daily", "query": "mamba state space model",
   "interval_minutes": 1440, "deep": true, "max_results": 5,
   "rounds": 2, "branching": 1}
]}
```

运行记录持久化到 `tasks.json.state.json`（跨重启不重复执行）。

## 多主题对比研究（V5.0）

对多个主题分别做「检索 → 摘要 → 跨文献分析」，再用 LLM 横向综合：

```bash
.venv/bin/python -B -m agent.cli --compare "transformer|mamba" --max-results 5
```

对比报告包含：主题概览表、整体态势、**共享主题**、**各自侧重**、
**论文重叠**、**交叉研究建议**（带理由的新研究方向）。

## 数据源（均免费、无需 Key）

| 数据源 | 用途 | 说明 |
| --- | --- | --- |
| arXiv API | 预印本检索 | 官方接口，稳定 |
| Semantic Scholar API | 期刊/会议检索 + 引用/被引 | 无 Key 限速（429 时自动降级） |
| Crossref API | DOI 反查 / 降级检索 | DOI 注册机构，可靠 |

## 路线图

> 当前产品发行版本统一为 **0.0.4**；下方 V1–V5 仅保留为历史能力里程碑，不代表当前发行版本。

- [x] V1.0 Skills 层：搜索 / 抓取 / 下载 / 解析 / 聚合
- [x] V1.0 Plugins 层：综合搜索、数据获取流水线
- [x] V1.0 MCP 层：规则规划器、报告生成器、Agent 门面
- [x] V2.0 LLM 规划器：复杂意图解析、JSON 结构化输出、自动降级、参数覆盖
- [x] V2.0 论文智能摘要：问题/方法/贡献/局限、批量并发、降级摘要
- [x] V2.0 跨文献分析：共识/分歧/演进/知识盲点、建议检索关键词
- [x] V3.0 研究闭环：盲点建议关键词自动触发下一轮检索、多轮深度报告
- [x] V4.0 引用网络分析 + 研究记忆持久化：跨会话去重、增量式深度研究
- [x] V5.0 Web 界面 + 定时自动研究 + 多主题对比研究
- [x] V5.2 Ollama / DeepSeek 双模式、会话预算保护、Electron 桌面封装
- [x] V5.3 文献库管理、全局限速重试、多轮去重下载、项目级 Skill 注入
- [x] V5.4 中文日志管道、阶段进度、任务耗时、状态筛选与日志复制

### 推荐的下一阶段升级

1. **文献库对话 / RAG**：仅基于用户已下载文献回答，结论带页码和原文引用。
2. **任务持久化与恢复**：重启 App 后保留历史任务，对检索、下载和摘要建立可恢复检查点。
3. **标签、收藏与笔记**：按项目/主题建立文献集合，添加阅读状态、批注和个人摘要。
4. **引用图谱**：交互式查看核心文献、互引关系、时间演进和未覆盖的研究分支。
5. **学术工具导出**：支持 BibTeX、RIS、CSV 以及 Zotero 可导入资料包。
6. **桌面原生体验**：任务完成/失败系统通知、托盘运行、自动更新、Apple/Windows 签名与崩溃日志导出。

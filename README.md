# Paper Studio v0.1.1

Paper Studio 是一款本地优先的 AI 学术研究工作台。它将多源文献检索、深度研究、PDF 阅读、知识记忆、报告管理、模型服务商配置与 MCP 集成在一个 Web / 桌面应用中，帮助研究者从问题提出走到可追溯的研究结论。

当前项目发行版本为 **v0.1.1**。

## 核心能力

### 深度研究 Agent

- 支持通用研究、系统综述、开题调研、竞品论文分析、每日追踪，以及独立的多主题对比研究。
- 自动执行检索、去重、结构化摘要、跨文献比较、引用网络分析和研究盲点识别。
- 支持深度多轮研究：仅在发现有效盲点时派生下一轮查询，并受轮次、分支数和总查询数上限控制。
- 任务中心实时呈现阶段、进度、检索结果、模型输入输出、重试、服务商切换、失败原因和报告产出。
- 研究可在安全检查点暂停、恢复或取消；应用关闭后，任务会保留可恢复的输入、日志和执行轨迹。
- 暂停后可修改查询、补充检索方向或排除论文，再从安全边界继续执行。

### 文献库与阅读工作台

- 聚合 arXiv、Semantic Scholar 与 Crossref 的公开学术数据，按标题去重并排序。
- 支持公开 PDF 的限速下载、重试、超时控制和批次管理，降低上游限流导致的失败。
- 内置 PDF 预览与抽取文本阅读，支持高亮、批注、摘录、标签、页码与关键词定位。
- 可按来源、年份、引用量、全文可用性、重复度和相关性查看辅助质量评分。
- 可选择已有本地文献继续研究，复用已下载原文与元数据，无需重复检索。
- 支持从文献库定位原始文件、管理下载批次，以及在报告中跳转到关联资料。

### 报告与知识记忆

- 自动生成结构化 Markdown 研究报告，包含文献摘要、共识、分歧、演进路径、引用分析和研究盲点。
- 报告提供目录导航、搜索、文献跳转，并可离线导出 Markdown、Word 与 PDF。
- 本地语义记忆会保存主题、论文、方法、结论和盲点，研究时自动复用相关历史结论并标明来源。
- 通过主题知识图谱查看主题—论文—作者—方法—结论—盲点之间的关系。
- 记忆支持搜索、固定、归档、合并、设置有效期、长期记忆整理及 Markdown / JSON 导出。

### 模型服务商与可靠性

- 内置 Ollama、DeepSeek、OpenAI、OpenRouter、硅基流动、智谱、阿里百炼、火山方舟和 OneAPI 档案。
- 可添加、编辑和删除任意 OpenAI Chat Completions 兼容服务商；每个档案可独立设置 Base URL、模型、默认模型、环境变量与请求超时。
- 支持自动发现模型，也支持手动输入任意模型名称。
- 模型保存前执行真实的草稿级推理检测，验证地址、密钥、模型权限和推理可用性。
- 云端请求支持短暂故障重试与已配置兼容服务商之间的故障切换；本地模型可根据设备性能调长超时。
- 模型配置可查看和备份；其中不保存 API Key 明文。桌面版凭据使用 Electron 系统安全存储并在重启后自动恢复。

### Skills 与 MCP

- 核心能力已拆分为带 Schema、权限、超时、进度和标准结果的 Skills，包括搜索、下载、总结、比较、引用分析、记忆、报告与本地文献库 RAG。
- Skill 能力中心可查看各能力的输入输出 Schema、所需权限和执行进度；网络、写入、付费和删除操作需要确认。
- Paper Studio 可作为 MCP Server，为外部 Agent 提供受权限控制的检索、文献库、报告、记忆和研究控制能力。
- Paper Studio 也可作为 MCP Client，安全连接本地文件系统、知识库、文献管理工具或机构数据库。

## 使用方式

### Web 版

环境由 [uv](https://docs.astral.sh/uv/) 统一管理：

```bash
uv sync
uv run python -B -m agent.webapp --port 8765
```

浏览器访问 `http://127.0.0.1:8765`，在“设置 → 模型配置”中完成本地或云端模型配置后即可开始研究。

### 命令行

```bash
# 基础检索并生成报告
uv run python -B -m agent.cli "transformer"

# 深度多轮研究
uv run python -B -m agent.cli "mamba state space model" --deep --rounds 3

# 下载公开论文并生成摘要、跨文献分析
uv run python -B -m agent.cli "llm agent" --max-downloads 3 --summarize --analyze

# 多主题对比研究
uv run python -B -m agent.cli --compare "transformer|mamba" --max-results 5
```

### 桌面版开发与打包

```bash
# 准备 Python 构建环境
uv sync --group build

# 启动 Electron 桌面版
cd desktop
npm install
npm start

# 为当前操作系统构建安装包
npm run dist
```

构建会自动识别宿主系统并生成自包含后端：

- macOS Apple Silicon：`Paper Studio-0.1.1-arm64.dmg`、`Paper Studio-0.1.1-arm64.zip`
- Windows x64：`Paper Studio-0.1.1-x64.exe`、`Paper Studio-0.1.1-x64.zip`

必须在对应的原生操作系统构建对应平台的安装包。更多桌面构建说明见 [desktop/README.md](desktop/README.md)。

### Docker 部署

v0.1.1 提供容器镜像与 Compose 部署方案。镜像发布地址为
`ghcr.io/keyingshuzhi/paper-studio:v0.1.1`；GitHub Release 标签或手动运行
「Publish Paper Studio container」工作流会构建 `linux/amd64` 与 `linux/arm64` 镜像。

推荐使用 Compose，将所有可变数据持久化到项目当前目录下的 `docker-data/`：

```bash
mkdir -p docker-data/data docker-data/config
docker compose up -d --build
```

浏览器访问 `http://127.0.0.1:8765`。后续使用已发布镜像时可跳过本地构建：

```bash
docker compose pull
docker compose up -d
```

`docker-data/data/` 保存报告、PDF、文献批次、知识记忆、任务和定时计划；
`docker-data/config/` 保存 `model_config.json`，也可放置仅供本机使用的 `.env`。
该目录已被 Git 忽略，不能提交 API Key 或研究数据。Linux 主机如遇目录权限问题，可执行：

```bash
sudo chown -R 10001:10001 docker-data
```

如使用宿主机的 Ollama，请在“设置 → 模型配置”中将 Ollama Base URL 设置为
`http://host.docker.internal:11434`。Compose 已为 Linux、macOS 与 Windows 的 Docker
环境提供该主机名映射。

常用运维命令：

```bash
docker compose logs -f
docker compose down
```

## 数据与隐私

- 研究报告、下载论文、阅读批注、知识记忆、任务队列和定时任务均保存在本机。
- 桌面版数据位于操作系统的 Paper Studio 应用数据目录；安装包与安装目录不携带用户 API Key、模型配置、报告、论文或记忆。
- Web 版默认将研究产出写入项目的 `downloads/` 目录。
- 仅在执行云端模型请求、联网检索或用户明确授权的 MCP 调用时，才会连接相应的外部服务。

## 项目结构

```text
agent/
├── core/       研究规划、模型调用、深度循环、报告与记忆编排
├── skills/     带 Schema 与权限边界的原子研究能力
├── plugins/    搜索与数据获取等流程组合
├── static/     Web / Electron 共用界面与品牌资源
├── mcp_server.py
├── mcp_client.py
└── webapp.py   Web 界面与桌面版共享后端

desktop/        Electron 壳、构建脚本与平台安装包配置
examples/       回归测试、MCP 验证与命令行示例
```

## 数据来源

| 数据源 | 用途 |
| --- | --- |
| arXiv API | 预印本检索 |
| Semantic Scholar API | 学术论文检索、引用与被引分析 |
| Crossref API | DOI 元数据查询与检索降级 |

公开 PDF 的下载遵循 `agent/skills/SKILL.md` 的可获取性与版权规则：仅下载明确公开的 PDF 地址；无法确认公开全文时保留元数据而不下载。

## 验证

```bash
# 核心记忆能力
uv run python -B examples/test_memory.py

# Web 与前端能力
uv run python -B examples/test_web_latest.py

# MCP Server、控制链路与权限
uv run python -B examples/test_mcp_server.py
uv run python -B examples/test_mcp_control.py
uv run python -B examples/test_mcp_permissions.py

# 桌面后端打包与记忆生命周期验证
npm --prefix desktop run backend
```

## 系统要求

- Python 3.10–3.13（Web / CLI 开发）
- Node.js 与 npm（Electron 开发或打包）
- macOS Apple Silicon 或 Windows x64（桌面版构建与运行）
- 使用本地模型时需自行安装并启动 [Ollama](https://ollama.com/)

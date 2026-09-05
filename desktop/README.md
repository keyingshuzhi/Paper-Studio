# Paper Studio Desktop v0.1.0

Electron 桌面壳会在本机启动 Python Web 后端，并在窗口关闭时自动停止它。

开发启动：

```bash
cd ..
uv sync
cd desktop
npm install
npm start
```

打包前需在项目根目录准备 Python 运行环境：

```bash
uv sync --group build
cd desktop
npm run dist
```

打包命令会自动识别当前系统，并先用 PyInstaller 生成当前平台的自包含后端；成品不依赖用户额外安装 Python。支持的发布目标如下：

- macOS Apple Silicon（`darwin/arm64`）：生成 `DMG` 与 `ZIP`；
- Windows x64（`win32/x64`）：生成 `NSIS .exe` 安装程序与 `ZIP`。

必须在对应目标系统原生构建，不能用 Mac 构建 Windows 后端，也不能用 Windows 构建 macOS 后端。运行 `npm run dist` 会自动选择当前支持的平台与架构；不支持 Linux、Intel Mac 或 Windows ARM64 构建，以避免生成未经验证的安装包。

报告、下载内容、记忆和凭据均保存在操作系统的 Paper Studio 用户数据目录，不会写入安装目录或打进安装包。发布包不会携带开发环境中的 `.env`、模型配置、API Key、报告、PDF、任务或定时计划，新安装会从空白用户数据开始。

桌面端的「文献库」可直接打开 PDF、在 Finder / 资源管理器中定位文件，以及删除单篇或整批资料。下载限速、重试次数和超时设置会持久化，重启后继续生效。

桌面进程强制使用 UTF-8，并通过流式解码器合并跨输出分片的中文字符。「任务」页会显示中文阶段、进度、耗时与可复制的完整日志。

v0.1.0 使用全新的 Paper Studio 应用图标，并将模型凭据扩展为按服务商独立加密保存。设置页内置 Ollama、DeepSeek、OpenAI、OpenRouter、硅基流动、智谱、阿里百炼、火山方舟和 OneAPI，也允许添加任意 OpenAI 兼容服务商和模型；成本页面已从桌面界面移除。

macOS 应用壳使用 `assets/icon.icns`，Windows 使用 `assets/icon.png`；Web 页面、品牌启动动画和应用内部图标仍使用共享前端资源，不受平台安装图标替换影响。启动动画支持浅色、暗色、随系统和“减少动态效果”偏好，并包含超时降级，不会阻塞主界面。

当前发布文件命名：

- macOS：`Paper Studio-0.1.0-arm64.dmg`、`Paper Studio-0.1.0-arm64.zip`
- Windows：`Paper Studio-0.1.0-x64.exe`、`Paper Studio-0.1.0-x64.zip`

当前 macOS 构建未配置 Apple Developer ID 签名与公证，首次启动可能需要右键应用并选择“打开”。Windows 正式分发前同样建议配置代码签名证书。

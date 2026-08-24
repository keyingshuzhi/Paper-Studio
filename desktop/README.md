# Paper Studio Desktop

Electron 桌面壳会在本机启动 Python Web 后端，并在窗口关闭时自动停止它。

开发启动：

```bash
cd desktop
npm install
npm start
```

打包前需在项目根目录准备 Python 运行环境：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-build.txt
cd desktop
npm run dist
```

打包命令会自动识别当前系统，并先用 PyInstaller 生成当前平台的自包含后端；成品不依赖用户额外安装 Python。支持的发布目标如下：

- macOS Apple Silicon（`darwin/arm64`）：生成 `DMG` 与 `ZIP`；
- Windows x64（`win32/x64`）：生成 `NSIS .exe` 安装程序与 `ZIP`。

必须在对应目标系统原生构建，不能用 Mac 构建 Windows 后端，也不能用 Windows 构建 macOS 后端。运行 `npm run dist` 会自动选择当前支持的平台与架构；不支持 Linux、Intel Mac 或 Windows ARM64 构建，以避免生成未经验证的安装包。

报告、下载内容、记忆和凭据均保存在操作系统的 Paper Studio 用户数据目录，不会写入安装目录或打进安装包。

桌面端的「文献库」可直接打开 PDF、在 Finder / 资源管理器中定位文件，以及删除单篇或整批资料。下载限速、重试次数和超时设置会持久化，重启后继续生效。

桌面进程强制使用 UTF-8，并通过流式解码器合并跨输出分片的中文字符。「任务」页会显示中文阶段、进度、耗时与可复制的完整日志。

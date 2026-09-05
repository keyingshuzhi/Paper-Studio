"use strict";

/**
 * 在当前宿主系统构建 PyInstaller 后端。
 * PyInstaller 不能可靠地跨平台交叉编译，因此只允许 macOS arm64 和
 * Windows x64 在各自原生系统上生成对应的后端。
 */
const { spawnSync } = require("child_process");
const path = require("path");

const desktopDir = path.resolve(__dirname, "..");
const projectRoot = path.resolve(desktopDir, "..");
const supported = {
  darwin: "arm64",
  win32: "x64",
};

function requireSupportedHost() {
  const expectedArch = supported[process.platform];
  if (!expectedArch) {
    throw new Error("仅支持在 macOS Apple Silicon 或 Windows x64 上构建桌面版后端。");
  }
  if (process.arch !== expectedArch) {
    throw new Error(`当前平台为 ${process.platform}/${process.arch}；仅支持 ${process.platform}/${expectedArch} 原生构建。`);
  }
}

function main() {
  requireSupportedHost();
  // uv is the only Python environment manager for this repository.  `uv run`
  // creates/synchronizes .venv and installs the build dependency group before
  // invoking PyInstaller, so desktop packaging never relies on a manually
  // prepared or stale virtual environment.
  const result = spawnSync("uv", [
    "run", "--project", projectRoot, "--group", "build", "python",
    "-m", "PyInstaller",
    "--noconfirm", "--clean",
    "--distpath", path.join(projectRoot, "build", "backend-dist"),
    "--workpath", path.join(projectRoot, "build", "backend-work"),
    path.join(desktopDir, "paper-studio-backend.spec"),
  ], {
    cwd: desktopDir,
    env: {
      ...process.env,
      PYINSTALLER_CONFIG_DIR: path.join(projectRoot, "build", "pyinstaller-cache"),
    },
    stdio: "inherit",
  });
  if (result.error?.code === "ENOENT") {
    throw new Error("未找到 uv。请先安装 uv（https://docs.astral.sh/uv/），然后重新执行 npm run dist。");
  }
  if (result.error) throw result.error;
  process.exit(result.status ?? 1);
}

try {
  main();
} catch (error) {
  console.error(`后端构建失败：${error.message}`);
  process.exit(1);
}

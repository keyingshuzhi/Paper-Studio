"use strict";

/**
 * 按当前操作系统选择 Electron Builder 目标。
 * macOS 仅输出 Apple Silicon arm64；Windows 仅输出 x64。
 */
const { spawnSync } = require("child_process");
const path = require("path");

const desktopDir = path.resolve(__dirname, "..");
const supported = {
  darwin: { arch: "arm64", target: "--mac" },
  win32: { arch: "x64", target: "--win" },
};

function main() {
  const platform = supported[process.platform];
  if (!platform) {
    throw new Error("仅支持在 macOS Apple Silicon 或 Windows x64 上打包 Paper Studio。\nLinux 不生成发布安装包。");
  }
  if (process.arch !== platform.arch) {
    throw new Error(`当前平台为 ${process.platform}/${process.arch}；需要 ${process.platform}/${platform.arch} 原生环境。`);
  }

  const builder = path.join(
    desktopDir,
    "node_modules",
    ".bin",
    process.platform === "win32" ? "electron-builder.cmd" : "electron-builder",
  );
  const passthrough = process.argv.slice(2);
  const result = spawnSync(builder, [platform.target, `--${platform.arch}`, ...passthrough], {
    cwd: desktopDir,
    stdio: "inherit",
    shell: process.platform === "win32",
  });
  if (result.error) throw result.error;
  process.exit(result.status ?? 1);
}

try {
  main();
} catch (error) {
  console.error(`桌面打包失败：${error.message}`);
  process.exit(1);
}

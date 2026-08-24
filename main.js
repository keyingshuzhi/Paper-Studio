"use strict";

/**
 * Electron 主进程：只负责桌面窗口和 Python 后端的生命周期。
 * 渲染层永远运行在 127.0.0.1，Node 能力仅通过 preload 的窄桥接暴露。
 */
const { app, BrowserWindow, dialog, ipcMain, safeStorage, shell } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const { StringDecoder } = require("string_decoder");

let backend = null;
let mainWindow = null;

function withinDownloads(targetPath, suffixes) {
  const root = path.resolve(app.getPath("userData"), "downloads");
  const target = path.resolve(app.getPath("userData"), targetPath || "");
  return target.startsWith(root + path.sep) && suffixes.includes(path.extname(target).toLowerCase())
    ? target : null;
}

function credentialPath() {
  return path.join(app.getPath("userData"), "deepseek-api-key.bin");
}

function projectRoot() {
  // 打包后资源在 resources/；开发期 desktop/ 的父目录即项目根目录。
  return app.isPackaged ? process.resourcesPath : path.resolve(__dirname, "..");
}

function pythonCommand(root) {
  if (process.platform === "win32") {
    return { command: path.join(root, ".venv", "Scripts", "python.exe"), args: [] };
  }
  return { command: path.join(root, ".venv", "bin", "python"), args: [] };
}

function launchBackend() {
  const root = projectRoot();
  const python = pythonCommand(root);
  const userData = app.getPath("userData");
  const configDir = app.isPackaged ? userData : root;
  backend = spawn(python.command, ["-B", "-m", "agent.webapp", "--port", "0"], {
    // 研究报告、下载内容与记忆保存到用户可写的应用数据目录，而不是
    // 打包后的 resources（macOS/Windows 都可能是只读）。
    cwd: userData,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: "1",
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8",
      PYTHONPATH: root + path.delimiter + (process.env.PYTHONPATH || ""),
      PAPER_STUDIO_CONFIG_DIR: configDir,
      PAPER_STUDIO_DATA_DIR: path.join(userData, "downloads"),
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  const fail = (message) => {
    dialog.showErrorBox("Paper Studio 无法启动", message);
    app.quit();
  };
  backend.on("error", (error) => fail(
    `无法启动 Python 后端：${error.message}\n\n请先在项目根目录创建 .venv 并安装 requirements.txt。`));
  // StringDecoder 会保留跨 chunk 的 UTF-8 多字节字符，避免中文被
  // data.toString() 在边界上拆成替换符。
  const stdoutDecoder = new StringDecoder("utf8");
  const stderrDecoder = new StringDecoder("utf8");
  backend.stderr.on("data", (data) => {
    const text = stderrDecoder.write(data);
    if (text) console.error(`[backend] ${text}`);
  });
  backend.stdout.on("data", (data) => {
    const text = stdoutDecoder.write(data);
    console.log(`[backend] ${text}`);
    const match = text.match(/http:\/\/127\.0\.0\.1:(\d+)/);
    if (match && mainWindow) mainWindow.loadURL(`http://127.0.0.1:${match[1]}`);
  });
  backend.on("close", () => {
    const out = stdoutDecoder.end();
    const err = stderrDecoder.end();
    if (out) console.log(`[backend] ${out}`);
    if (err) console.error(`[backend] ${err}`);
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1240,
    height: 850,
    minWidth: 900,
    minHeight: 640,
    backgroundColor: "#f5f7fb",
    title: "Paper Studio",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  mainWindow.setMenuBarVisibility(false);
  launchBackend();
}

app.whenReady().then(createWindow);
ipcMain.handle("app:info", () => ({
  version: app.getVersion(),
  platform: process.platform,
  packaged: app.isPackaged,
}));
ipcMain.handle("report:reveal", (_event, reportPath) => {
  const target = withinDownloads(reportPath, [".md"]);
  if (!target || !fs.existsSync(target)) return false;
  shell.showItemInFolder(target);
  return true;
});
ipcMain.handle("library:reveal", (_event, filePath) => {
  const target = withinDownloads(filePath, [".pdf", ".txt"]);
  if (!target || !fs.existsSync(target)) return false;
  shell.showItemInFolder(target);
  return true;
});
ipcMain.handle("library:open", async (_event, filePath) => {
  const target = withinDownloads(filePath, [".pdf", ".txt"]);
  if (!target || !fs.existsSync(target)) return false;
  return (await shell.openPath(target)) === "";
});
ipcMain.handle("secret:load", () => {
  if (!safeStorage.isEncryptionAvailable() || !fs.existsSync(credentialPath())) {
    return null;
  }
  try {
    return safeStorage.decryptString(fs.readFileSync(credentialPath()));
  } catch {
    return null;
  }
});
ipcMain.handle("secret:save", (_event, value) => {
  if (!safeStorage.isEncryptionAvailable()) return false;
  if (!value) {
    fs.rmSync(credentialPath(), { force: true });
    return true;
  }
  fs.writeFileSync(credentialPath(), safeStorage.encryptString(String(value)), { mode: 0o600 });
  return true;
});
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  if (backend && !backend.killed) backend.kill();
});
app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });

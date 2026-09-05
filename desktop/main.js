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

// 桌面版使用产品名对应的独立数据档案。旧开发版使用包名
// paper-studio-desktop，升级后不再自动带入旧的报告、文献或凭据。
// 不删除旧目录，用户仍可自行备份或恢复历史研究数据。
app.setPath("userData", path.join(app.getPath("appData"), "Paper Studio"));

let backend = null;
let mainWindow = null;

function withinDownloads(targetPath, suffixes) {
  const root = path.resolve(app.getPath("userData"), "downloads");
  const target = path.resolve(app.getPath("userData"), targetPath || "");
  return target.startsWith(root + path.sep) && suffixes.includes(path.extname(target).toLowerCase())
    ? target : null;
}

function credentialPath() {
  return path.join(app.getPath("userData"), "provider-secrets.bin");
}

function legacyCredentialPath() {
  return path.join(app.getPath("userData"), "deepseek-api-key.bin");
}

function loadProviderSecrets() {
  if (!safeStorage.isEncryptionAvailable()) return {};
  try {
    if (fs.existsSync(credentialPath())) {
      const parsed = JSON.parse(safeStorage.decryptString(fs.readFileSync(credentialPath())));
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") return {};
      return Object.fromEntries(Object.entries(parsed)
        .filter(([key, value]) => /^[a-z0-9][a-z0-9_-]{0,47}$/.test(key)
          && typeof value === "string" && value.length <= 10000));
    }
    // Read the v0.0.x DeepSeek-only file until the next credentials save.
    // saveProviderSecrets removes that obsolete encrypted file so a cleared
    // legacy key cannot unexpectedly reappear after restart.
    if (fs.existsSync(legacyCredentialPath())) {
      const key = safeStorage.decryptString(fs.readFileSync(legacyCredentialPath()));
      return key ? { deepseek: key } : {};
    }
  } catch {
    return {};
  }
  return {};
}

function saveProviderSecrets(value) {
  if (!safeStorage.isEncryptionAvailable() || !value
      || Array.isArray(value) || typeof value !== "object") return false;
  const clean = Object.fromEntries(Object.entries(value)
    .filter(([key, secret]) => /^[a-z0-9][a-z0-9_-]{0,47}$/.test(key)
      && typeof secret === "string" && secret.length <= 10000 && secret));
  if (Object.keys(clean).length === 0) {
    fs.rmSync(credentialPath(), { force: true });
    fs.rmSync(legacyCredentialPath(), { force: true });
    return true;
  }
  fs.writeFileSync(credentialPath(), safeStorage.encryptString(JSON.stringify(clean)), {
    mode: 0o600,
  });
  fs.rmSync(legacyCredentialPath(), { force: true });
  return true;
}

function projectRoot() {
  // 打包后资源在 resources/；开发期 desktop/ 的父目录即项目根目录。
  return app.isPackaged ? process.resourcesPath : path.resolve(__dirname, "..");
}

function backendCommand(root) {
  if (app.isPackaged) {
    return {
      command: path.join(
        root,
        "backend",
        process.platform === "win32"
          ? "paper-studio-backend.exe"
          : "paper-studio-backend",
      ),
      args: [],
    };
  }
  return {
    // Development mode uses the same uv project environment as the CLI and
    // packaging pipeline; it no longer assumes a manually created .venv.
    command: process.platform === "win32" ? "uv.exe" : "uv",
    args: ["run", "--project", root, "python", "-B", "-m", "agent.webapp"],
  };
}

function launchBackend() {
  const root = projectRoot();
  const runtime = backendCommand(root);
  const userData = app.getPath("userData");
  const configDir = app.isPackaged ? userData : root;
  backend = spawn(runtime.command, [...runtime.args, "--port", "0"], {
    // 研究报告、下载内容与记忆保存到用户可写的应用数据目录，而不是
    // 打包后的 resources（macOS/Windows 都可能是只读）。
    cwd: userData,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: "1",
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8",
      ...(app.isPackaged ? {} : {
        PYTHONPATH: root + path.delimiter + (process.env.PYTHONPATH || ""),
      }),
      PAPER_STUDIO_CONFIG_DIR: configDir,
      PAPER_STUDIO_DATA_DIR: path.join(userData, "downloads"),
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  const fail = (message) => {
    dialog.showErrorBox("Paper Studio 无法启动", message);
    app.quit();
  };
  backend.on("error", (error) => fail(error.code === "ENOENT"
    ? "未找到 uv。开发模式请先安装 uv，并在项目根目录执行 uv sync。"
    : `无法启动研究后端：${error.message}\n\n请重新安装 Paper Studio。`));
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
ipcMain.handle("secrets:load", () => loadProviderSecrets());
ipcMain.handle("secrets:save", (_event, value) => saveProviderSecrets(value));
// Compatibility bridge for an already-open v0.0.x renderer during upgrade.
ipcMain.handle("secret:load", () => loadProviderSecrets().deepseek || null);
ipcMain.handle("secret:save", (_event, value) => {
  const secrets = loadProviderSecrets();
  if (value) secrets.deepseek = String(value);
  else delete secrets.deepseek;
  return saveProviderSecrets(secrets);
});
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  if (backend && !backend.killed) backend.kill();
});
app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });

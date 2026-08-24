"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("agent", {
  // 所有文件访问均由主进程校验，渲染层没有 Node / 文件系统权限。
  revealReport: (reportPath) => ipcRenderer.invoke("report:reveal", reportPath),
  revealLibraryFile: (filePath) => ipcRenderer.invoke("library:reveal", filePath),
  openLibraryFile: (filePath) => ipcRenderer.invoke("library:open", filePath),
  loadDeepSeekKey: () => ipcRenderer.invoke("secret:load"),
  saveDeepSeekKey: (value) => ipcRenderer.invoke("secret:save", value),
  appInfo: () => ipcRenderer.invoke("app:info"),
});

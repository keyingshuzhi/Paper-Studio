"use strict";

/**
 * Validate the actual PyInstaller output rather than the source tree.
 *
 * The desktop backend contains dynamic Skill/MCP registries, which means a
 * successful source test alone cannot prove a release has every capability.
 * This smoke test launches the bundled executable in an empty temporary data
 * directory and exercises the user-facing knowledge-memory operations that
 * remain available in the packaged desktop application.
 */
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

const desktopDir = path.resolve(__dirname, "..");
const projectRoot = path.resolve(desktopDir, "..");
const packageInfo = require(path.join(desktopDir, "package.json"));
const executableName = process.platform === "win32"
  ? "paper-studio-backend.exe"
  : "paper-studio-backend";
const defaultExecutablePath = path.join(
  projectRoot, "build", "backend-dist", "paper-studio-backend", executableName,
);
// A release verification may pass the executable copied into an Electron app;
// defaulting to build/ keeps `npm run backend` simple.
const executablePath = path.resolve(
  process.argv[2] || process.env.PAPER_STUDIO_BACKEND_PATH || defaultExecutablePath,
);
const expectedVersion = packageInfo.version;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function request(baseUrl, method, target, payload) {
  return new Promise((resolve, reject) => {
    const body = payload === undefined ? null : Buffer.from(JSON.stringify(payload));
    const url = new URL(target, baseUrl);
    const req = http.request(url, {
      method,
      headers: body ? {
        "Content-Type": "application/json",
        "Content-Length": String(body.length),
      } : {},
      timeout: 10000,
    }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => {
        const text = Buffer.concat(chunks).toString("utf8");
        let json = null;
        try {
          json = JSON.parse(text);
        } catch {
          // The root document is intentionally HTML, not JSON.
        }
        resolve({ status: response.statusCode || 0, text, json });
      });
    });
    req.on("timeout", () => req.destroy(new Error(`request timeout: ${target}`)));
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

function waitForBackend(command, state) {
  return new Promise((resolve, reject) => {
    const deadline = setTimeout(() => {
      reject(new Error(`bundled backend did not announce a local URL\n${state.output()}`));
    }, 45000);
    let settled = false;
    const settle = (callback, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(deadline);
      callback(value);
    };
    command.stdout.on("data", (chunk) => {
      state.stdout += chunk.toString("utf8");
      const match = state.stdout.match(/http:\/\/127\.0\.0\.1:(\d+)/);
      if (match) settle(resolve, `http://127.0.0.1:${match[1]}`);
    });
    command.stderr.on("data", (chunk) => {
      state.stderr += chunk.toString("utf8");
    });
    command.once("error", (error) => settle(reject, error));
    command.once("close", (code) => {
      if (!settled) {
        settle(reject, new Error(
          `bundled backend exited before becoming ready (code ${code})\n${state.output()}`,
        ));
      }
    });
  });
}

async function stop(command) {
  if (!command || command.exitCode !== null || command.killed) return;
  await new Promise((resolve) => {
    const timer = setTimeout(() => {
      try { command.kill("SIGKILL"); } catch { /* already stopped */ }
      resolve();
    }, 5000);
    command.once("close", () => {
      clearTimeout(timer);
      resolve();
    });
    try { command.kill(); } catch {
      clearTimeout(timer);
      resolve();
    }
  });
}

async function main() {
  assert(fs.existsSync(executablePath), `missing bundled backend: ${executablePath}`);
  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "paper-studio-bundle-"));
  const dataDir = path.join(temporaryRoot, "data");
  const configDir = path.join(temporaryRoot, "config");
  fs.mkdirSync(dataDir, { recursive: true });
  fs.mkdirSync(configDir, { recursive: true });

  const state = {
    stdout: "",
    stderr: "",
    output() {
      return `stdout:\n${this.stdout}\nstderr:\n${this.stderr}`;
    },
  };
  const command = spawn(executablePath, ["--port", "0"], {
    cwd: temporaryRoot,
    env: {
      ...process.env,
      PAPER_STUDIO_DATA_DIR: dataDir,
      PAPER_STUDIO_CONFIG_DIR: configDir,
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  try {
    const baseUrl = await waitForBackend(command, state);
    const page = await request(baseUrl, "GET", "/");
    assert(page.status === 200, `bundled index returned HTTP ${page.status}`);
    assert(page.text.includes('id="memoryCleanup"') && page.text.includes('id="memoryMerge"'),
      "bundled UI is missing knowledge-memory management controls");
    assert(page.text.includes('id="memoryActionDialog"') && page.text.includes("function openMemoryAction"),
      "bundled UI is missing the in-app memory management dialog");
    const memoryHandlers = page.text.split("\n").filter((line) =>
      line.includes('$("memoryMerge").onclick') || line.includes('$("memoryCleanup").onclick'),
    ).join("\n");
    assert(memoryHandlers.includes("openMemoryAction")
      && !memoryHandlers.includes("prompt(") && !memoryHandlers.includes("confirm("),
    "bundled memory management still relies on Electron-incompatible native dialogs");

    const about = await request(baseUrl, "GET", "/api/about");
    assert(about.status === 200 && about.json?.version === expectedVersion,
      `bundled backend version is not ${expectedVersion}`);

    const catalog = await request(baseUrl, "GET", "/api/skills");
    const skillNames = new Set((catalog.json?.skills || []).map((skill) => skill.name));
    for (const name of [
      "memory_archive", "memory_merge", "memory_cleanup", "memory_graph",
      "research_template_survey", "research_template_opening",
      "research_template_competitor", "research_template_daily",
      "library_rag", "report_write",
    ]) {
      assert(skillNames.has(name), `bundled Skill registry is missing ${name}`);
    }
    const libraryRag = (catalog.json?.skills || []).find((skill) => skill.name === "library_rag");
    assert(libraryRag?.version === expectedVersion,
      `bundled library_rag version is not ${expectedVersion}`);

    const write = async (query, summary) => {
      const response = await request(baseUrl, "POST", "/api/memory-write", {
        query,
        analysis: { summary, gaps: [] },
        confirmed: true,
      });
      assert(response.status === 200 && response.json?.query === query,
        `could not create bundled memory: ${query}`);
    };
    await write("v0.1.1 目标主题", "目标主题结论");
    await write("v0.1.1 来源主题", "来源主题结论");

    const merged = await request(baseUrl, "POST", "/api/memory-merge", {
      target_query: "v0.1.1 目标主题",
      source_queries: ["v0.1.1 来源主题"],
      confirmed: true,
    });
    assert(merged.status === 200
      && merged.json?.entry?.merged_from?.includes("v0.1.1 来源主题"),
    "bundled memory merge did not merge the selected source");

    const archivedSource = await request(
      baseUrl, "GET", "/api/memory-entry?query=" + encodeURIComponent("v0.1.1 来源主题"),
    );
    assert(archivedSource.status === 200 && archivedSource.json?.archived === true,
      "bundled memory merge did not archive its source topic");

    const archive = await request(baseUrl, "POST", "/api/memory-archive", {
      query: "v0.1.1 目标主题", archived: true, confirmed: true,
    });
    assert(archive.status === 200 && archive.json?.entry?.archived === true,
      "bundled memory archive did not change the topic state");

    const restored = await request(baseUrl, "POST", "/api/memory-archive", {
      query: "v0.1.1 目标主题", archived: false, confirmed: true,
    });
    assert(restored.status === 200 && restored.json?.entry?.archived === false,
      "bundled memory archive could not restore a topic");

    const cleaned = await request(baseUrl, "POST", "/api/memory-cleanup", {
      max_age_days: 180, confirmed: true,
    });
    assert(cleaned.status === 200 && cleaned.json?.action === "archive"
      && Number.isInteger(cleaned.json?.count),
    "bundled long-term memory cleanup endpoint is unavailable");

    console.log(`Bundled backend v${expectedVersion} verified: full Skill registry and memory lifecycle work.`);
  } finally {
    await stop(command);
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(`Bundled backend verification failed: ${error.stack || error.message}`);
  process.exitCode = 1;
});

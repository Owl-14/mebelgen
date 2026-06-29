#!/usr/bin/env node

const BASE_URL = process.env.BAZIS_TASKS_BASE_URL || "https://cloud.bazissoft.ru";
const API_KEY = process.env.BAZIS_API_KEY || "";

const args = parseArgs(process.argv.slice(2));

if (!API_KEY) {
  fail("Set BAZIS_API_KEY first.");
}

if (args.help || Object.keys(args).length === 0) {
  printHelp();
  process.exit(0);
}

if (args.list) {
  const tasks = await requestJson("GET", "/api-cloud-tasks-public/tasks");
  console.log(JSON.stringify(tasks, null, 2));
} else if (args.modelConvert) {
  const convertType = Number(args.convertType ?? 0);
  const result = await createMultipartTask(
    `/api-cloud-tasks-public/tasks/model-convert?convertType=${convertType}`,
    "models",
    args.modelConvert
  );
  console.log("Created task:");
  console.log(JSON.stringify(result, null, 2));
  await pollAndDownload(result);
} else if (args.drawingConvert) {
  const format = Number(args.format ?? 0);
  const result = await createMultipartTask(
    `/api-cloud-tasks-public/tasks/drawing-convert?format=${format}`,
    "drawings",
    args.drawingConvert
  );
  console.log("Created task:");
  console.log(JSON.stringify(result, null, 2));
  await pollAndDownload(result);
} else {
  fail("Unknown command. Use --help.");
}

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--help" || arg === "-h") out.help = true;
    else if (arg === "--list") out.list = true;
    else if (arg === "--model-convert") out.modelConvert = argv[++i];
    else if (arg === "--drawing-convert") out.drawingConvert = argv[++i];
    else if (arg === "--convert-type") out.convertType = argv[++i];
    else if (arg === "--format") out.format = argv[++i];
    else if (arg === "--out") out.out = argv[++i];
    else fail(`Unknown arg: ${arg}`);
  }
  return out;
}

async function createMultipartTask(path, fieldName, filePath) {
  const { readFile } = await import("node:fs/promises");
  const { basename } = await import("node:path");
  const file = await readFile(filePath);
  const form = new FormData();
  form.append(fieldName, new Blob([file]), basename(filePath));

  return requestJson("POST", path, { body: form });
}

async function pollAndDownload(created) {
  const id = pickTaskId(created);
  if (!id) {
    console.log("Cannot infer task id from response; skipping polling.");
    return;
  }

  console.log(`Polling task ${id}...`);
  let task = null;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    task = await requestJson("GET", `/api-cloud-tasks-public/tasks/${id}`);
    const state = Number(task.state ?? task.State);
    console.log(`[${attempt + 1}] state=${state} ${stateName(state)}`);
    if (state === 2 || stateName(state) === "Success") break;
    if (state === 3 || stateName(state) === "Failed") {
      console.log(JSON.stringify(task, null, 2));
      fail("Task failed.");
    }
    await sleep(2000);
  }

  const out = args.out || `bazis-task-${id}-result.bin`;
  const response = await requestRaw("GET", `/api-cloud-tasks-public/tasks/${id}/download-result`);
  const buffer = Buffer.from(await response.arrayBuffer());
  const { writeFile } = await import("node:fs/promises");
  await writeFile(out, buffer);
  console.log(`Downloaded result: ${out} (${buffer.length} bytes)`);
}

function pickTaskId(value) {
  if (!value || typeof value !== "object") return null;
  return value.id ?? value.Id ?? value.taskId ?? value.TaskId ?? value.result?.id ?? null;
}

function stateName(state) {
  return {
    0: "Created",
    1: "Running",
    2: "Success",
    3: "Failed"
  }[state] || "Unknown";
}

async function requestJson(method, path, options = {}) {
  const response = await requestRaw(method, path, options);
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    console.log(text);
    fail("Response is not JSON.");
  }
}

async function requestRaw(method, path, options = {}) {
  const response = await fetch(`${BASE_URL}${path}`, {
    method,
    ...options,
    headers: {
      apiKey: API_KEY,
      ...(options.headers || {})
    }
  });

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    fail(`${method} ${path} failed: HTTP ${response.status}\n${text}`);
  }

  return response;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function fail(message) {
  console.error(message);
  process.exit(1);
}

function printHelp() {
  console.log(`
Usage:
  BAZIS_API_KEY=... node basis/apilist/check_tasks_api.mjs --list
  BAZIS_API_KEY=... node basis/apilist/check_tasks_api.mjs --model-convert model.b3d --convert-type 0 --out result.bin
  BAZIS_API_KEY=... node basis/apilist/check_tasks_api.mjs --drawing-convert drawing.ldw --format 0 --out drawing.pdf

Notes:
  --convert-type 0 = B3dToCfrn, 1 = CfrnToB3d
  --format 0 = Pdf, 1 = Jpeg, 2 = Wmf, 3 = Svg
`);
}


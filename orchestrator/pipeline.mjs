import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { validateFurnitureSpec } from "./validate-spec.mjs";

const providers = {
  mock: () => import("./providers/mock-provider.mjs"),
  gemini: () => import("./providers/gemini-provider.mjs")
};

export async function runPipeline(options = {}) {
  const inputPath = resolve(options.input || "orchestrator/sample-inputs/desk-brief.txt");
  const outputPath = resolve(options.out || "demo/three-spike/generated/latest-spec.json");
  const providerName = options.provider || "mock";
  const providerFactory = providers[providerName];
  if (!providerFactory) {
    throw new Error(`Unknown provider "${providerName}". Available: ${Object.keys(providers).join(", ")}`);
  }

  const text = await readFile(inputPath, "utf8");
  const provider = await providerFactory();
  const spec = await provider.extractSpec({ text, inputPath });
  const validation = validateFurnitureSpec(spec);
  if (!validation.ok) {
    throw new Error(`FurnitureSpec validation failed:\n${validation.errors.map((e) => `- ${e}`).join("\n")}`);
  }

  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(spec, null, 2)}\n`, "utf8");

  return { inputPath, outputPath, provider: providerName, spec };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  runPipeline(parseArgs(process.argv.slice(2)))
    .then((result) => {
      console.log(`FurnitureSpec written: ${result.outputPath}`);
      console.log(`Provider: ${result.provider}`);
      console.log(`Preview: http://127.0.0.1:4173/demo/three-spike/`);
    })
    .catch((error) => {
      console.error(error.message);
      process.exitCode = 1;
    });
}

function parseArgs(args) {
  const options = {};
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === "--input") options.input = args[++i];
    else if (arg === "--out") options.out = args[++i];
    else if (arg === "--provider") options.provider = args[++i];
  }
  return options;
}

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { validateFurnitureSpec } from "./validate-spec.mjs";

const INPUT = "output/specs.json";
const OUT_DIR = "demo/three-spike/generated/kabinety";
const LATEST = "demo/three-spike/generated/latest-spec.json";

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  importKabinety()
    .then((result) => {
      console.log(`Imported ${result.items.length} training specs`);
      console.log(`Manifest: ${result.manifestPath}`);
      console.log(`Latest: ${result.latestPath}`);
    })
    .catch((error) => {
      console.error(error.message);
      process.exitCode = 1;
    });
}

export async function importKabinety(options = {}) {
  const inputPath = resolve(options.input || INPUT);
  const outDir = resolve(options.outDir || OUT_DIR);
  const latestPath = resolve(options.latest || LATEST);
  const raw = JSON.parse(await readFile(inputPath, "utf8"));
  await mkdir(outDir, { recursive: true });
  await mkdir(dirname(latestPath), { recursive: true });

  const items = [];
  for (const item of raw) {
    const spec = convertSpec(item);
    const validation = validateFurnitureSpec(spec);
    if (!validation.ok) {
      throw new Error(`${item.name}: ${validation.errors.join("; ")}`);
    }
    const file = `${slug(spec.title)}.json`;
    const path = resolve(outDir, file);
    await writeFile(path, `${JSON.stringify(spec, null, 2)}\n`, "utf8");
    items.push({
      id: spec.id,
      title: spec.title,
      type: spec.type,
      spec: `./generated/kabinety/${file}`
    });
  }

  const manifest = {
    source: basename(inputPath),
    generatedAt: new Date().toISOString(),
    items
  };
  const manifestPath = resolve(outDir, "index.json");
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  const firstDesk = items.find((item) => item.type === "desk_panel") || items[0];
  const latest = await readFile(resolve(outDir, basename(firstDesk.spec)), "utf8");
  await writeFile(latestPath, latest, "utf8");
  return { items, manifestPath, latestPath };
}

function convertSpec(item) {
  const d = item.dimensions || {};
  const f = item.features || {};
  const raw = item.raw_characteristics || "";
  const widthMm = numberOr(d.w, d.diameter || 800);
  const depthMm = numberOr(d.d, d.diameter || widthMm);
  const heightMm = numberOr(d.h, item.archetype === "coffee_rect" ? 450 : 750);
  const color = detectColor(item, raw);
  const topThickness = numberOr(f.thickness?.worktop, item.archetype?.startsWith("coffee") ? 40 : 18);
  const sideThickness = item.archetype === "desk_panel" ? 50 : numberOr(f.thickness?.side, 18);
  const screenHeight = find(raw, /высот[аы]?\s+передн\w*\s+экран\w*\s*(\d+(?:[.,]\d+)?)/iu) || 300;
  const screenThickness = find(raw, /толщин[аы]?\s*(\d+(?:[.,]\d+)?)\s*мм/iu) || 25;

  const parts = {
    top: {
      label: item.archetype?.startsWith("coffee") ? "Столешница" : "Корпус / столешница",
      material: item.material?.body || "МДФ",
      thicknessMm: topThickness,
      colorSystem: color.system,
      colorCode: color.code,
      finish: item.material?.matte === false ? "глянцевое" : "матовое"
    },
    sidePanels: {
      label: item.archetype === "desk_panel" ? "Опоры" : "Корпус",
      material: item.material?.body || "МДФ",
      thicknessMm: sideThickness,
      colorSystem: color.system,
      colorCode: color.code
    }
  };

  if (item.archetype === "desk_panel") {
    parts.top.label = "Столешница";
    parts.frontScreen = {
      label: "Передний экран",
      material: "МДФ",
      thicknessMm: screenThickness,
      heightMm: screenHeight,
      colorSystem: color.system,
      colorCode: color.code
    };
    parts.brassInserts = { material: "латунь" };
    parts.pcHolder = { material: "проф. труба 40x20мм, RAL 9011" };
    parts.cableChannel = { material: "чёрный" };
  }

  if (item.archetype === "drawer_unit") {
    parts.drawers = { label: "Ящики", count: f.drawers || 3, material: "накладные фасады" };
    parts.plinth = { label: "Цоколь", material: "в цвет корпуса", heightMm: 25 };
  }

  if (item.archetype === "wardrobe" || item.archetype === "cabinet") {
    parts.doors = {
      label: "Двери",
      material: f.overlay_doors ? "накладные" : "push-to-open",
      count: 2
    };
    parts.plinth = {
      label: "Цоколь",
      material: f.plinth_black ? "чёрный матовый" : "в цвет корпуса",
      heightMm: item.archetype === "wardrobe" ? 70 : 80
    };
    if (f.brass) parts.brassInserts = { material: "латунная Т-вставка" };
    if (f.shelves) parts.shelves = { label: "Полки", count: f.shelves, material: "МДФ" };
    if (f.rod) parts.rod = { label: "Штанга", material: "выдвижная" };
  }

  if (item.archetype === "coffee_round" || item.archetype === "coffee_fluted") {
    parts.pedestal = {
      label: item.archetype === "coffee_fluted" ? "Рифлёная поверхность" : "Основание",
      material: item.material?.body || "МДФ"
    };
  }

  return {
    id: `${item.index || "item"}-${slug(item.name)}`,
    type: item.archetype,
    title: `${item.name} ${dimensionLabel(item, widthMm, depthMm, heightMm)}`.trim(),
    ai: {
      provider: "parsed-doc/mock",
      model: "mebelgen-python-parser"
    },
    dimensions: {
      widthMm,
      depthMm,
      heightMm,
      ...(d.diameter ? { diameterMm: d.diameter } : {})
    },
    geometry: {
      overhangMm: item.archetype === "desk_panel" ? 25 : 0
    },
    parts,
    features: {
      brass: Boolean(f.brass),
      pcHolder: Boolean(f.pc_holder),
      cableChannel: Boolean(f.cable_channel),
      pushOpen: Boolean(f.push_open),
      lock: Boolean(f.lock),
      lockRight: Boolean(f.lock_right),
      plinth: Boolean(f.plinth),
      plinthBlack: Boolean(f.plinth_black),
      shelves: f.shelves || 0,
      drawers: f.drawers || 0,
      fluted: Boolean(f.fluted),
      feltPads: Boolean(f.felt_pads),
      matteLacquer: Boolean(f.matte_lacquer),
      rod: Boolean(f.rod),
      hatShelf: Boolean(f.hat_shelf),
      shoeShelf: Boolean(f.shoe_shelf)
    },
    materials: materialLines(item),
    titleBlock: {
      sheetName: `${item.name} ${dimensionLabel(item, widthMm, depthMm, heightMm)}`.trim(),
      date: "28.05.2026",
      manager: "Петрова А.А."
    }
  };
}

function materialLines(item) {
  const lines = (item.material_lines || []).map((line) => line.replace(/…$/, "").trim()).filter(Boolean);
  if (!lines.some((line) => line.startsWith("*"))) {
    lines.push("*Фактические замеры обязательны.");
  }
  return lines.slice(0, 9);
}

function dimensionLabel(item, w, d, h) {
  if (item.dimensions?.diameter) return `Ø${format(item.dimensions.diameter)}x${format(h)}`;
  return `${format(w)}x${format(d)}x${format(h)}`;
}

function detectColor(item, raw) {
  const fromMaterial = item.material || {};
  if (fromMaterial.color_system && fromMaterial.color_code) {
    return { system: fromMaterial.color_system, code: fromMaterial.color_code };
  }
  const ral = raw.match(/RAL\s*([0-9]{3,4})/iu);
  if (ral) return { system: "RAL", code: ral[1] };
  const ncs = raw.match(/N[CS]S\s*([0-9A-Z-]+)/iu);
  if (ncs) return { system: "NCS", code: ncs[1].toUpperCase() };
  return { system: "", code: "" };
}

function find(text, pattern) {
  const hit = text.match(pattern);
  return hit ? Number(String(hit[1]).replace(",", ".")) : null;
}

function numberOr(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function format(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  return Math.abs(n - Math.round(n)) < 1e-6 ? String(Math.round(n)) : String(Number(n.toFixed(2)));
}

function slug(text) {
  const map = {
    а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z",
    и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r",
    с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "c", ч: "ch", ш: "sh",
    щ: "sch", ы: "y", э: "e", ю: "yu", я: "ya", ь: "", ъ: ""
  };
  return String(text || "item")
    .toLowerCase()
    .split("")
    .map((ch) => map[ch] ?? ch)
    .join("")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 80) || "item";
}

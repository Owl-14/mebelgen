export async function extractSpec({ text }) {
  const dimensions = parseDimensions(text);
  const topThickness = findNear(text, /столешниц/iu, /(\d+(?:[.,]\d+)?)\s*мм/iu) || 50;
  const sideThickness = findNear(text, /опор|боков/iu, /(\d+(?:[.,]\d+)?)\s*мм/iu) || topThickness;
  const screenThickness = findNear(text, /экран/iu, /(\d+(?:[.,]\d+)?)\s*мм/iu) || 25;
  const screenHeight = findNear(text, /экран/iu, /h\s*=\s*(\d+(?:[.,]\d+)?)\s*мм/iu) || 300;
  const ncs = match(text, /NCS\s*([0-9A-Z-]+)/iu) || "3000";
  const hasBrass = /латун/iu.test(text);
  const hasPcHolder = /подвес|системн/iu.test(text);
  const hasCable = /кабель/iu.test(text);

  return {
    id: "desk-workstation-generated",
    type: "desk_panel",
    title: `Рабочий стол ${dimensions.widthMm}x${dimensions.depthMm}x${dimensions.heightMm}`,
    ai: {
      provider: "mock/dev",
      model: "rules-v0"
    },
    dimensions,
    geometry: {
      overhangMm: findNear(text, /отрыв|свес/iu, /(\d+(?:[.,]\d+)?)\s*мм/iu) || 25
    },
    parts: {
      top: {
        label: "Столешница",
        material: "МДФ",
        thicknessMm: topThickness,
        colorSystem: "NCS",
        colorCode: ncs,
        finish: /матов/iu.test(text) ? "матовое" : ""
      },
      sidePanels: {
        label: "Опоры",
        material: "МДФ",
        thicknessMm: sideThickness,
        colorSystem: "NCS",
        colorCode: ncs
      },
      frontScreen: {
        label: "Передний экран",
        material: "МДФ",
        thicknessMm: screenThickness,
        heightMm: screenHeight,
        colorSystem: "NCS",
        colorCode: ncs
      },
      brassInserts: {
        material: "натуральная латунь"
      },
      pcHolder: {
        material: "проф. труба 40x20мм, RAL 9011"
      },
      cableChannel: {
        material: "чёрный"
      }
    },
    features: {
      brass: hasBrass,
      pcHolder: hasPcHolder,
      cableChannel: hasCable
    },
    materials: materialLines(text),
    titleBlock: {
      sheetName: `Рабочий стол ${dimensions.widthMm}x${dimensions.depthMm}x${dimensions.heightMm}`,
      date: "28.05.2026",
      manager: "Петрова А.А."
    }
  };
}

export async function reviewRender() {
  return {
    ok: true,
    provider: "mock/dev",
    notes: [
      "Mock review only checks the pipeline contract. Visual AI review will be wired through a provider adapter later."
    ],
    patches: []
  };
}

function parseDimensions(text) {
  const hit = text.match(/(\d+(?:[.,]\d+)?)\s*[xх×*]\s*(\d+(?:[.,]\d+)?)\s*[xх×*]\s*(\d+(?:[.,]\d+)?)/iu);
  if (!hit) {
    return { widthMm: 1800, depthMm: 800, heightMm: 750 };
  }
  return {
    widthMm: toNumber(hit[1]),
    depthMm: toNumber(hit[2]),
    heightMm: toNumber(hit[3])
  };
}

function materialLines(text) {
  const lines = text
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line) => !/^рабочий стол/i.test(line));
  return [...lines, "*Фактические замеры обязательны."];
}

function findNear(text, keyword, valuePattern) {
  const lines = text.split(/\n+/);
  for (const line of lines) {
    if (!keyword.test(line)) continue;
    const value = line.match(valuePattern);
    if (value) return toNumber(value[1]);
  }
  return null;
}

function match(text, pattern) {
  const hit = text.match(pattern);
  return hit ? hit[1] : null;
}

function toNumber(value) {
  return Number(String(value).replace(",", "."));
}

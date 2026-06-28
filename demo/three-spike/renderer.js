import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";

const furnitureSpec = await loadFurnitureSpec();
const spec = normalizeFurnitureSpec(furnitureSpec);

const state = {
  angle: "sample",
  outline: true,
  shadows: true,
};

applySheetData(furnitureSpec, spec);
setupCaseSelect(furnitureSpec.__sourcePath || "");

const front = createRenderer(
  document.querySelector("#front-view"),
  document.querySelector("#front-overlay"),
  "front",
);
const iso = createRenderer(
  document.querySelector("#iso-view"),
  document.querySelector("#iso-overlay"),
  "iso",
);

document.querySelector("#angle-select").addEventListener("change", (event) => {
  state.angle = event.target.value;
  renderAll();
});

document.querySelector("#outline-toggle").addEventListener("change", (event) => {
  state.outline = event.target.checked;
  renderAll();
});

document.querySelector("#shadow-toggle").addEventListener("change", (event) => {
  state.shadows = event.target.checked;
  renderAll();
});

window.addEventListener("resize", () => {
  front.resize();
  iso.resize();
  renderAll();
});

renderAll();

async function loadFurnitureSpec() {
  const params = new URLSearchParams(window.location.search);
  const requested = params.get("spec") || "./generated/latest-spec.json";
  const candidates = [...new Set([requested, "./specs/desk-workstation.json"])];
  for (const path of candidates) {
    try {
      const response = await fetch(path, { cache: "no-store" });
      if (response.ok) {
        const data = await response.json();
        data.__sourcePath = path;
        return data;
      }
    } catch {
      // Try the fallback candidate below.
    }
  }
  throw new Error("Не удалось загрузить FurnitureSpec JSON");
}

async function setupCaseSelect(activePath) {
  const select = document.querySelector("#case-select");
  if (!select) return;
  try {
    const manifests = await Promise.all([
      loadManifest("./generated/kabinety/index.json", "Кабинеты"),
      loadManifest("./generated/syktyvkar/index.json", "Сыктывкар"),
    ]);
    const items = manifests.flatMap((manifest) => manifest.items);
    if (!items.length) return;
    select.replaceChildren(
      option("", "latest-spec"),
      ...items.map((item) => option(item.spec, `${item.group}: ${item.title}`)),
    );
    const normalizedActive = normalizePath(activePath);
    const active = items.find((item) => normalizePath(item.spec) === normalizedActive);
    select.value = active ? active.spec : "";
    select.addEventListener("change", () => {
      const url = new URL(window.location.href);
      if (select.value) url.searchParams.set("spec", select.value);
      else url.searchParams.delete("spec");
      window.location.href = url.toString();
    });
  } catch {
    // The training manifest appears after running pnpm training:kabinety.
  }
}

async function loadManifest(path, group) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) return { items: [] };
  const manifest = await response.json();
  const items = Array.isArray(manifest.items) ? manifest.items : [];
  return {
    items: items.map((item) => ({ ...item, group })),
  };
}

function normalizeFurnitureSpec(raw) {
  const dimensions = raw.dimensions || {};
  const parts = raw.parts || {};
  const geometry = raw.geometry || {};
  const features = raw.features || {};
  const top = parts.top || {};
  const sidePanels = parts.sidePanels || {};
  const frontScreen = parts.frontScreen || {};
  const widthMm = numberOr(dimensions.widthMm, 1800);
  const depthMm = numberOr(dimensions.depthMm, 800);
  const heightMm = numberOr(dimensions.heightMm, 750);
  const diameterMm = numberOr(dimensions.diameterMm, 0);
  const topMm = numberOr(top.thicknessMm, 50);
  const sideMm = numberOr(sidePanels.thicknessMm, topMm);
  const screenHeightMm = numberOr(frontScreen.heightMm, 300);
  const overhangMm = numberOr(geometry.overhangMm, 25);
  const labelLine = (title, details) => [title, details].filter(Boolean);

  return {
    sourcePath: raw.__sourcePath || "",
    type: raw.type || "desk_panel",
    modules: Array.isArray(raw.modules) ? raw.modules : [],
    width: widthMm / 1000,
    depth: depthMm / 1000,
    height: heightMm / 1000,
    top: topMm / 1000,
    side: sideMm / 1000,
    screen: screenHeightMm / 1000,
    overhang: overhangMm / 1000,
    dimensions: { widthMm, depthMm, heightMm, diameterMm },
    labels: {
      width: formatMm(widthMm),
      depth: formatMm(depthMm),
      height: formatMm(heightMm),
      top: formatMm(topMm),
      side: formatMm(sideMm),
      screen: formatMm(screenHeightMm),
      overhang: formatMm(overhangMm),
    },
    callouts: {
      top: labelLine(top.label || "Столешница", materialText(top, "МДФ 50мм, NCS 3000")),
      sidePanels: labelLine(sidePanels.label || "Опоры", materialText(sidePanels, "МДФ 50мм, NCS 3000")),
      frontScreen: labelLine(
        frontScreen.label || "Передний экран",
        materialText(frontScreen, `МДФ ${formatMm(numberOr(frontScreen.thicknessMm, 25))}мм, h=${formatMm(screenHeightMm)}мм`),
      ),
      brass: labelLine("Вставки", (parts.brassInserts && parts.brassInserts.material) || "натуральная латунь"),
      pcHolder: labelLine("Подвес СБ", (parts.pcHolder && parts.pcHolder.material) || "RAL 9011"),
      cableChannel: labelLine("Гибкий кабель-канал", (parts.cableChannel && parts.cableChannel.material) || "чёрный"),
    },
    features: {
      brass: Boolean(features.brass),
      pcHolder: Boolean(features.pcHolder),
      cableChannel: Boolean(features.cableChannel),
      pushOpen: Boolean(features.pushOpen),
      lock: Boolean(features.lock),
      lockRight: Boolean(features.lockRight),
      plinth: Boolean(features.plinth),
      plinthBlack: Boolean(features.plinthBlack),
      shelves: Number(features.shelves || 0),
      drawers: Number(features.drawers || 0),
      fluted: Boolean(features.fluted),
      feltPads: Boolean(features.feltPads),
      matteLacquer: Boolean(features.matteLacquer),
      rod: Boolean(features.rod),
      hatShelf: Boolean(features.hatShelf),
      shoeShelf: Boolean(features.shoeShelf),
      sectionCount: Number(features.sectionCount || geometry.sectionCount || 0),
      metalFrame: Boolean(features.metalFrame),
      modules: Number(features.modules || (Array.isArray(raw.modules) ? raw.modules.length : 0)),
    },
    titleBlock: raw.titleBlock || {},
    materials: Array.isArray(raw.materials) ? raw.materials : [],
  };
}

function applySheetData(raw, normalized) {
  const materials = document.querySelector("#materials-list");
  if (materials && normalized.materials.length) {
    materials.replaceChildren(
      ...normalized.materials.map((line, index) => {
        const p = document.createElement("p");
        p.textContent = line;
        if (index === normalized.materials.length - 1 && line.startsWith("*")) {
          p.className = "note";
        }
        return p;
      }),
    );
  }

  setText("[data-field='sheet-name']", normalized.titleBlock.sheetName || raw.title || raw.name || "Рабочий стол");
  setText("[data-field='date']", normalized.titleBlock.date || "28.05.2026");
  setText("[data-field='manager']", normalized.titleBlock.manager || "Петрова А.А.");
  setText("#model-kind", normalized.type);
  setText("#spec-source", normalized.sourcePath || "inline");
  setText("#ai-provider", raw.ai?.provider || "mock/dev");
}

function createRenderer(canvas, overlay, mode) {
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.setClearColor(0xffffff, 0);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  const model = buildFurnitureModel(mode);
  scene.add(model);
  scene.add(buildShadowPlane());
  scene.add(new THREE.HemisphereLight(0xffffff, 0xd6cab8, 2.25));

  const key = new THREE.DirectionalLight(0xffffff, 2.45);
  key.position.set(2.2, 3.4, 2.1);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.camera.near = 0.1;
  key.shadow.camera.far = 8;
  scene.add(key);

  const fill = new THREE.DirectionalLight(0xf6ead6, 1.15);
  fill.position.set(-2.4, 1.8, -2.0);
  scene.add(fill);

  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 10);

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    renderer.setPixelRatio(pixelRatio);
    renderer.setSize(rect.width, rect.height, false);
    fitCamera(camera, rect.width / Math.max(rect.height, 1), mode);
  }

  function render() {
    model.traverse((obj) => {
      if (obj.userData.outline) obj.visible = state.outline;
      if (obj.userData.shadow) obj.visible = state.shadows;
    });
    const shadow = scene.getObjectByName("contact-shadow");
    if (shadow) shadow.visible = state.shadows;
    fitCamera(camera, canvas.clientWidth / Math.max(canvas.clientHeight, 1), mode);
    renderer.render(scene, camera);
    updateOverlay(mode, overlay, canvas, camera);
  }

  resize();
  return { resize, render };
}

function buildFurnitureModel(mode) {
  if (spec.type === "desk_panel") return buildDeskModel();
  if (spec.type === "built_in_run") return buildBuiltInRunModel();
  if (spec.type === "kitchen_run") return buildKitchenRunModel();
  if (spec.type === "countertop") return buildCountertopModel();
  if (spec.type === "lectern") return buildLecternModel();
  if (spec.type === "drawer_unit") return buildDrawerUnitModel();
  if (spec.type === "coffee_round" || spec.type === "coffee_fluted") return buildCoffeeRoundModel();
  if (spec.type === "coffee_rect") return buildCoffeeRectModel();
  return buildCaseGoodModel(mode);
}

function buildDeskModel() {
  const group = new THREE.Group();
  group.name = "desk_panel";

  const { matTop, matSide, matScreen, matBrass, matBlack, matBlackSoft } = createMaterials();

  const W = spec.width;
  const D = spec.depth;
  const H = spec.height;
  const topTh = spec.top;
  const side = spec.side;
  const over = spec.overhang;
  const legH = H - topTh;

  addBox(group, {
    name: "top",
    size: [W + over * 2, topTh, D + over * 2],
    pos: [0, H - topTh / 2, 0],
    material: matTop,
    radius: 0.014,
    cast: true,
  });

  for (const x of [-W / 2 + side / 2, W / 2 - side / 2]) {
    addBox(group, {
      name: "side-panel",
      size: [side, legH, D],
      pos: [x, legH / 2, 0],
      material: matSide,
      radius: 0.004,
      cast: true,
    });
    addBox(group, {
      name: "brass-front",
      size: [0.006, legH - 0.055, 0.008],
      pos: [x, (legH - 0.055) / 2 + 0.022, -D / 2 - 0.004],
      material: matBrass,
      cast: true,
    });
    addBox(group, {
      name: "brass-foot",
      size: [side * 0.72, 0.016, 0.055],
      pos: [x, 0.008, -D / 2 + 0.018],
      material: matBrass,
      cast: true,
    });
  }

  addBox(group, {
    name: "screen",
    size: [W - side * 2, spec.screen, 0.026],
    pos: [0, H - topTh - spec.screen / 2, -D / 2 + 0.03],
    material: matScreen,
    radius: 0.002,
    cast: true,
  });

  if (spec.features.cableChannel) {
    addBox(group, {
      name: "cable-tray",
      size: [0.38, 0.028, 0.055],
      pos: [0.22, H - topTh - 0.017, -0.16],
      material: matBlack,
      cast: true,
    });
    addCableHose(group, matBlack, matBlackSoft);
  }
  if (spec.features.pcHolder) {
    addPcHolder(group, matBlack, matBlackSoft);
  }

  addOutlines(group);
  group.position.y = -H / 2;
  return group;
}

function buildCaseGoodModel(mode) {
  const group = new THREE.Group();
  group.name = "casegood";
  const { matTop, matSide, matScreen, matBrass, matBlack, matBlackSoft, matCloth, matShoe } = createMaterials();
  const W = spec.width;
  const D = spec.depth;
  const H = spec.height;
  const th = clamp(spec.side || 0.018, 0.014, 0.05);
  const plinthH = spec.features.plinth || spec.features.plinthBlack ? (H > 1.2 ? 0.07 : 0.08) : 0.02;
  const bodyH = H - plinthH;
  const doorGap = 0.006;
  const openScene = mode === "iso" && (spec.type === "wardrobe" || spec.type === "cabinet");

  addBox(group, { name: "left-side", size: [th, bodyH, D], pos: [-W / 2 + th / 2, plinthH + bodyH / 2, 0], material: matSide, radius: 0.002 });
  addBox(group, { name: "right-side", size: [th, bodyH, D], pos: [W / 2 - th / 2, plinthH + bodyH / 2, 0], material: matSide, radius: 0.002 });
  addBox(group, { name: "top", size: [W, th, D], pos: [0, H - th / 2, 0], material: matTop, radius: 0.002 });
  addBox(group, { name: "bottom", size: [W, th, D], pos: [0, plinthH + th / 2, 0], material: matSide, radius: 0.002 });
  addBox(group, { name: "back", size: [W - th * 2, bodyH - th * 2, 0.012], pos: [0, plinthH + bodyH / 2, D / 2 - 0.006], material: matScreen, radius: 0.001 });

  if (openScene || spec.features.shelves || spec.features.rod || spec.features.hatShelf || spec.features.shoeShelf) {
    addInterior(group, { W, D, H, th, plinthH, matSide, matBlack, matCloth, matShoe, openScene });
  }

  const doorW = (W - doorGap) / 2;
  const doorH = bodyH - th * 1.2;
  if (openScene) {
    addOpenDoor(group, {
      side: "left",
      W,
      D,
      doorW,
      doorH,
      y: plinthH + doorH / 2 + th * 0.35,
      material: matTop,
    });
    addOpenDoor(group, {
      side: "right",
      W,
      D,
      doorW,
      doorH,
      y: plinthH + doorH / 2 + th * 0.35,
      material: matTop,
    });
  } else {
    for (const x of [-doorW / 2 - doorGap / 2, doorW / 2 + doorGap / 2]) {
      addBox(group, {
        name: "door",
        size: [doorW, doorH, 0.018],
        pos: [x, plinthH + doorH / 2 + th * 0.35, -D / 2 - 0.012],
        material: matTop,
        radius: 0.002,
      });
    }
  }
  if (spec.features.brass && !openScene) addDoorBrass(group, { W, D, H, plinthH, matBrass });
  if (spec.features.brass && openScene) addOpenSceneBrass(group, { W, D, H, plinthH, matBrass });
  if (spec.features.lock) {
    const lock = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, 0.004, 24), matBlack);
    lock.name = "lock";
    lock.rotation.x = Math.PI / 2;
    lock.position.set(W * 0.22, H * 0.5, -D / 2 - 0.024);
    group.add(lock);
  }
  addBox(group, {
    name: "plinth",
    size: [W, plinthH, D * 0.94],
    pos: [0, plinthH / 2, 0.02],
    material: spec.features.plinthBlack ? matBlack : matSide,
    radius: 0.002,
  });

  addOutlines(group);
  group.position.y = -H / 2;
  return group;
}

function buildBuiltInRunModel() {
  const group = new THREE.Group();
  group.name = "built_in_run";
  const { matTop, matSide, matScreen, matBlack } = createMaterials();
  const W = spec.width;
  const D = spec.depth;
  const H = spec.height;
  const th = clamp(spec.side || 0.016, 0.014, 0.03);
  const sections = Math.max(2, Math.min(12, Number(spec.features.sectionCount || Math.round(W / 0.5))));
  const sectionW = W / sections;
  const plinthH = 0.07;
  const bodyH = H - plinthH;

  addBox(group, { name: "back", size: [W, bodyH, 0.014], pos: [0, plinthH + bodyH / 2, D / 2 - 0.007], material: matScreen, radius: 0.001 });
  addBox(group, { name: "top", size: [W, th, D], pos: [0, H - th / 2, 0], material: matTop, radius: 0.002 });
  addBox(group, { name: "bottom", size: [W, th, D], pos: [0, plinthH + th / 2, 0], material: matSide, radius: 0.002 });
  for (let i = 0; i <= sections; i += 1) {
    const x = -W / 2 + i * sectionW;
    addBox(group, { name: "vertical-partition", size: [th, bodyH, D], pos: [x, plinthH + bodyH / 2, 0], material: matSide, radius: 0.001 });
  }
  for (let i = 0; i < sections; i += 1) {
    const cx = -W / 2 + sectionW * (i + 0.5);
    addBox(group, {
      name: "door",
      size: [sectionW - th * 1.5, bodyH * 0.72, 0.018],
      pos: [cx, plinthH + bodyH * 0.48, -D / 2 - 0.012],
      material: matTop,
      radius: 0.002,
    });
    if (i % 2 === 0) {
      addBox(group, {
        name: "wardrobe-rail",
        size: [0.018, bodyH * 0.34, 0.014],
        pos: [cx + sectionW * 0.28, plinthH + bodyH * 0.48, -D / 2 - 0.024],
        material: matBlack,
        radius: 0.003,
      });
    }
    for (const y of [plinthH + bodyH * 0.22, plinthH + bodyH * 0.78]) {
      addBox(group, {
        name: "shelf",
        size: [sectionW - th * 2, th, D * 0.82],
        pos: [cx, y, 0.02],
        material: matSide,
        radius: 0.001,
      });
    }
  }
  addBox(group, { name: "plinth", size: [W, plinthH, D * 0.94], pos: [0, plinthH / 2, 0.02], material: spec.features.plinthBlack ? matBlack : matSide, radius: 0.002 });

  addOutlines(group);
  group.position.y = -H / 2;
  return group;
}

function buildKitchenRunModel() {
  const group = new THREE.Group();
  group.name = "kitchen_run";
  const { matTop, matSide, matScreen, matBlack } = createMaterials();
  const W = spec.width;
  const D = Math.min(spec.depth, 0.72);
  const H = spec.height;
  const th = clamp(spec.side || 0.016, 0.014, 0.03);
  const modules = kitchenModules(W);
  const baseH = Math.min(0.86, H * 0.52);
  const plinthH = 0.09;
  const counterTh = 0.04;
  const wallH = Math.min(0.58, H * 0.34);
  const wallY = Math.min(H - wallH / 2, 1.45);

  addBox(group, { name: "countertop", size: [W + 0.04, counterTh, D + 0.04], pos: [0, baseH + counterTh / 2, -0.02], material: matTop, radius: 0.006 });
  let cursor = -W / 2;
  modules.forEach((module, index) => {
    const mw = module.widthMm / 1000;
    const cx = cursor + mw / 2;
    addBox(group, { name: "base-cabinet", size: [mw - 0.006, baseH - plinthH, D], pos: [cx, plinthH + (baseH - plinthH) / 2, 0], material: matSide, radius: 0.002 });
    addBox(group, { name: "base-front", size: [mw - 0.014, baseH * 0.58, 0.018], pos: [cx, plinthH + baseH * 0.36, -D / 2 - 0.012], material: matTop, radius: 0.002 });
    if (index % 3 === 1) {
      addBox(group, { name: "drawer-pull", size: [mw * 0.42, 0.018, 0.012], pos: [cx, plinthH + baseH * 0.58, -D / 2 - 0.028], material: matBlack, radius: 0.003 });
    }
    if (index < modules.length - 1) {
      addBox(group, { name: "module-seam", size: [0.006, baseH - plinthH, 0.012], pos: [cursor + mw, plinthH + (baseH - plinthH) / 2, -D / 2 - 0.022], material: matBlack, radius: 0.001 });
    }
    cursor += mw;
  });
  addBox(group, { name: "plinth", size: [W, plinthH, D * 0.92], pos: [0, plinthH / 2, 0.02], material: spec.features.plinthBlack ? matBlack : matSide, radius: 0.002 });

  cursor = -W / 2;
  modules.forEach((module, index) => {
    if (index % 4 === 3) {
      cursor += module.widthMm / 1000;
      return;
    }
    const mw = module.widthMm / 1000;
    const cx = cursor + mw / 2;
    addBox(group, { name: "wall-cabinet", size: [mw - 0.012, wallH, D * 0.46], pos: [cx, wallY, -D * 0.16], material: matScreen, radius: 0.002 });
    addBox(group, { name: "wall-front", size: [mw - 0.02, wallH * 0.82, 0.016], pos: [cx, wallY, -D * 0.39], material: matTop, radius: 0.002 });
    cursor += mw;
  });

  addOutlines(group);
  group.position.y = -H / 2;
  return group;
}

function buildCountertopModel() {
  const group = new THREE.Group();
  group.name = "countertop";
  const { matTop, matSide, matBrass } = createMaterials();
  const W = spec.width;
  const D = spec.depth;
  const H = spec.height;
  const topTh = clamp(spec.top || 0.04, 0.025, 0.07);

  addBox(group, { name: "countertop", size: [W, topTh, D], pos: [0, H - topTh / 2, 0], material: matTop, radius: 0.008 });
  for (const x of [-W / 2 + 0.055, W / 2 - 0.055]) {
    addBox(group, { name: "side-support", size: [0.05, H - topTh, D * 0.82], pos: [x, (H - topTh) / 2, 0], material: matSide, radius: 0.002 });
  }
  addBox(group, { name: "front-frame", size: [W, 0.026, 0.018], pos: [0, H - topTh - 0.018, -D / 2 - 0.012], material: matBrass, radius: 0.002 });
  addOutlines(group);
  group.position.y = -H / 2;
  return group;
}

function buildLecternModel() {
  const group = new THREE.Group();
  group.name = "lectern";
  const { matTop, matSide, matBlack } = createMaterials();
  const W = spec.width;
  const D = spec.depth;
  const H = spec.height;
  const topTh = clamp(spec.top || 0.025, 0.018, 0.05);
  addBox(group, { name: "lectern-body", size: [W, H - topTh, D], pos: [0, (H - topTh) / 2, 0], material: matSide, radius: 0.006 });
  const top = addBox(group, { name: "lectern-top", size: [W * 1.08, topTh, D * 1.08], pos: [0, H - topTh / 2, -0.02], material: matTop, radius: 0.006 });
  top.rotation.x = -0.08;
  addBox(group, { name: "front-panel", size: [W * 0.78, H * 0.56, 0.018], pos: [0, H * 0.42, -D / 2 - 0.012], material: matTop, radius: 0.004 });
  addBox(group, { name: "plinth", size: [W * 1.05, 0.04, D * 0.92], pos: [0, 0.02, 0], material: matBlack, radius: 0.004 });
  addOutlines(group);
  group.position.y = -H / 2;
  return group;
}

function kitchenModules(widthMeters) {
  if (spec.modules.length) {
    return spec.modules.map((module) => ({
      widthMm: Number(module.widthMm || 600),
    }));
  }
  const count = Math.max(3, Math.min(12, Number(spec.features.sectionCount || Math.round((widthMeters * 1000) / 600))));
  const widthMm = (widthMeters * 1000) / count;
  return Array.from({ length: count }, () => ({ widthMm }));
}

function buildDrawerUnitModel() {
  const group = new THREE.Group();
  group.name = "drawer_unit";
  const { matTop, matSide, matBlack } = createMaterials();
  const W = spec.width;
  const D = spec.depth;
  const H = spec.height;
  const topTh = clamp(spec.top || 0.05, 0.025, 0.06);
  const side = clamp(spec.side || 0.025, 0.018, 0.05);
  const drawers = Math.max(1, Number(spec.features.drawers || 3));
  const drawerW = W * 0.46;
  const leftOpenW = W - drawerW - side;
  const plinthH = 0.025;
  const bodyH = H - topTh - plinthH;

  addBox(group, { name: "top", size: [W, topTh, D], pos: [0, H - topTh / 2, 0], material: matTop, radius: 0.006 });
  addBox(group, { name: "left-side", size: [side, bodyH, D], pos: [-W / 2 + side / 2, plinthH + bodyH / 2, 0], material: matSide, radius: 0.002 });
  addBox(group, { name: "right-side", size: [side, bodyH, D], pos: [W / 2 - side / 2, plinthH + bodyH / 2, 0], material: matSide, radius: 0.002 });
  addBox(group, { name: "drawer-side", size: [side, bodyH, D], pos: [W / 2 - drawerW - side / 2, plinthH + bodyH / 2, 0], material: matSide, radius: 0.002 });
  addBox(group, { name: "drawer-back", size: [drawerW, bodyH, 0.018], pos: [W / 2 - drawerW / 2, plinthH + bodyH / 2, D / 2 - 0.009], material: matSide, radius: 0.002 });
  addBox(group, { name: "left-back", size: [leftOpenW, bodyH * 0.18, 0.018], pos: [-W / 2 + side + leftOpenW / 2, H - topTh - bodyH * 0.09, D / 2 - 0.009], material: matSide, radius: 0.002 });

  const gap = 0.008;
  const drawerH = (bodyH - gap * (drawers + 1)) / drawers;
  for (let i = 0; i < drawers; i += 1) {
    const y = plinthH + gap + drawerH / 2 + i * (drawerH + gap);
    addBox(group, {
      name: "drawer-front",
      size: [drawerW - gap, drawerH, 0.02],
      pos: [W / 2 - drawerW / 2, y, -D / 2 - 0.012],
      material: matTop,
      radius: 0.002,
    });
  }
  if (spec.features.lock) {
    const lock = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, 0.004, 24), matBlack);
    lock.name = "drawer-lock";
    lock.rotation.x = Math.PI / 2;
    lock.position.set(W / 2 - drawerW / 2, H * 0.48, -D / 2 - 0.027);
    group.add(lock);
  }
  addBox(group, { name: "plinth", size: [drawerW, plinthH, D * 0.92], pos: [W / 2 - drawerW / 2, plinthH / 2, 0.02], material: matSide, radius: 0.002 });

  addOutlines(group);
  group.position.y = -H / 2;
  return group;
}

function buildCoffeeRectModel() {
  const group = new THREE.Group();
  group.name = "coffee_rect";
  const { matTop, matSide } = createMaterials();
  const W = spec.width;
  const D = spec.depth;
  const H = spec.height;
  const topTh = clamp(spec.top || 0.05, 0.035, 0.07);
  addBox(group, { name: "top", size: [W, topTh, D], pos: [0, H - topTh / 2, 0], material: matTop, radius: 0.04 });
  for (const x of [-W * 0.28, W * 0.28]) {
    addCylinder(group, {
      name: "oval-support",
      radius: Math.min(D, W) * 0.16,
      height: H - topTh,
      pos: [x, (H - topTh) / 2, 0],
      material: matSide,
    });
  }
  addOutlines(group);
  group.position.y = -H / 2;
  return group;
}

function buildCoffeeRoundModel() {
  const group = new THREE.Group();
  group.name = spec.type;
  const { matTop, matSide, matBlackSoft } = createMaterials();
  const dia = (spec.dimensions.diameterMm || spec.dimensions.widthMm) / 1000;
  const H = spec.height;
  const topTh = clamp(spec.top || 0.04, 0.025, 0.06);
  addCylinder(group, { name: "round-top", radius: dia / 2, height: topTh, pos: [0, H - topTh / 2, 0], material: matTop, segments: 96 });
  addCylinder(group, { name: "pedestal", radius: dia * 0.34, height: H - topTh, pos: [0, (H - topTh) / 2, 0], material: matSide, segments: 96 });
  if (spec.features.fluted) {
    const count = 44;
    for (let i = 0; i < count; i += 1) {
      const a = (Math.PI * 2 * i) / count;
      const r = dia * 0.345;
      addBox(group, {
        name: "fluting",
        size: [0.006, H - topTh - 0.012, 0.01],
        pos: [Math.cos(a) * r, (H - topTh) / 2, Math.sin(a) * r],
        material: matBlackSoft,
        radius: 0.001,
      }).rotation.y = -a;
    }
  }
  addOutlines(group);
  group.position.y = -H / 2;
  return group;
}

function addBox(parent, opts) {
  const [w, h, d] = opts.size;
  const geometry = opts.radius
    ? new RoundedBoxGeometry(w, h, d, 5, opts.radius)
    : new THREE.BoxGeometry(w, h, d);
  const mesh = new THREE.Mesh(geometry, opts.material);
  mesh.name = opts.name;
  mesh.position.set(...opts.pos);
  mesh.castShadow = opts.cast ?? true;
  mesh.receiveShadow = true;
  parent.add(mesh);
  return mesh;
}

function addCylinder(parent, opts) {
  const geometry = new THREE.CylinderGeometry(opts.radius, opts.radius, opts.height, opts.segments || 48);
  const mesh = new THREE.Mesh(geometry, opts.material);
  mesh.name = opts.name;
  mesh.position.set(...opts.pos);
  mesh.castShadow = opts.cast ?? true;
  mesh.receiveShadow = true;
  parent.add(mesh);
  return mesh;
}

function addInterior(parent, { W, D, H, th, plinthH, matSide, matBlack, matCloth, matShoe, openScene }) {
  const bodyH = H - plinthH;
  const innerW = W - th * 2;
  addBox(parent, {
    name: "center-partition",
    size: [th, bodyH - th * 2, D * 0.92],
    pos: [0, plinthH + bodyH / 2, 0],
    material: matSide,
    radius: 0.001,
  });
  const shelfCount = Math.max(0, Number(spec.features.shelves || 0));
  const rodMode = spec.features.rod || spec.features.hatShelf || spec.features.shoeShelf;
  if (shelfCount) {
    for (let i = 1; i <= shelfCount; i += 1) {
      const y = plinthH + th + ((bodyH - th * 2) * i) / (shelfCount + 1);
      addBox(parent, {
        name: "shelf",
        size: [innerW / 2 - th, th, D * 0.88],
        pos: [-innerW / 4, y, 0.02],
        material: matSide,
        radius: 0.001,
      });
      addBox(parent, {
        name: "shelf",
        size: [innerW / 2 - th, th, D * 0.88],
        pos: [innerW / 4, y, 0.02],
        material: matSide,
        radius: 0.001,
      });
    }
  }
  if (rodMode) {
    const rod = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, innerW / 2 - th, 24), matBlack);
    rod.name = "clothes-rod";
    rod.rotation.z = Math.PI / 2;
    rod.position.set(-innerW / 4, H - bodyH * 0.33, -D * 0.16);
    parent.add(rod);
    for (const y of [H - bodyH * 0.18, plinthH + bodyH * 0.23]) {
      addBox(parent, {
        name: "wardrobe-shelf",
        size: [innerW / 2 - th, th, D * 0.84],
        pos: [-innerW / 4, y, 0.02],
        material: matSide,
        radius: 0.001,
      });
    }
    if (openScene) {
      addHangingCoat(parent, {
        x: -innerW / 4,
        y: H - bodyH * 0.45,
        z: -D * 0.34,
        material: matCloth,
        hangerMaterial: matBlack,
      });
      addShoes(parent, {
        x: -innerW / 4,
        y: plinthH + 0.04,
        z: -D * 0.34,
        material: matShoe,
      });
    }
  }
  if (openScene && !rodMode && shelfCount === 0) {
    for (let i = 1; i <= 3; i += 1) {
      const y = plinthH + th + ((bodyH - th * 2) * i) / 4;
      addBox(parent, {
        name: "open-shelf",
        size: [innerW / 2 - th, th, D * 0.84],
        pos: [innerW / 4, y, 0.02],
        material: matSide,
        radius: 0.001,
      });
    }
  }
}

function addDoorBrass(parent, { W, D, H, plinthH, matBrass }) {
  const bodyH = H - plinthH;
  const frontZ = -D / 2 - 0.026;
  const strip = 0.008;
  addBox(parent, { name: "brass-left", size: [strip, bodyH * 0.88, 0.008], pos: [-W / 2 + 0.035, plinthH + bodyH * 0.5, frontZ], material: matBrass, radius: 0.001 });
  addBox(parent, { name: "brass-right", size: [strip, bodyH * 0.88, 0.008], pos: [W / 2 - 0.035, plinthH + bodyH * 0.5, frontZ], material: matBrass, radius: 0.001 });
  addBox(parent, { name: "brass-center", size: [strip, bodyH * 0.88, 0.008], pos: [0, plinthH + bodyH * 0.5, frontZ], material: matBrass, radius: 0.001 });
}

function addOpenDoor(parent, { side, W, D, doorW, doorH, y, material }) {
  const frontZ = -D / 2 - 0.014;
  const hingeX = side === "left" ? -W / 2 + 0.018 : W / 2 - 0.018;
  const group = new THREE.Group();
  group.name = `${side}-open-door`;
  group.position.set(hingeX, y, frontZ);
  group.rotation.y = side === "left" ? 2.22 : -2.22;
  const door = addBox(group, {
    name: "open-door-panel",
    size: [doorW, doorH, 0.018],
    pos: [side === "left" ? doorW / 2 : -doorW / 2, 0, 0],
    material,
    radius: 0.002,
  });
  door.castShadow = true;
  parent.add(group);
}

function addOpenSceneBrass(parent, { W, D, H, plinthH, matBrass }) {
  const bodyH = H - plinthH;
  const frontZ = -D / 2 - 0.026;
  addBox(parent, {
    name: "brass-center-open",
    size: [0.008, bodyH * 0.86, 0.008],
    pos: [0, plinthH + bodyH * 0.5, frontZ],
    material: matBrass,
    radius: 0.001,
  });
}

function addHangingCoat(parent, { x, y, z, material, hangerMaterial }) {
  const coat = new THREE.Group();
  coat.name = "hanging-coat";
  coat.position.set(x, y, z);
  addBox(coat, {
    name: "hanger",
    size: [0.18, 0.008, 0.012],
    pos: [0, 0.24, -0.01],
    material: hangerMaterial,
    radius: 0.003,
  });
  addBox(coat, {
    name: "coat-body",
    size: [0.2, 0.52, 0.045],
    pos: [0, -0.05, 0],
    material,
    radius: 0.028,
  });
  addBox(coat, {
    name: "coat-left-sleeve",
    size: [0.052, 0.42, 0.04],
    pos: [-0.13, -0.08, 0.004],
    material,
    radius: 0.022,
  }).rotation.z = -0.12;
  addBox(coat, {
    name: "coat-right-sleeve",
    size: [0.052, 0.42, 0.04],
    pos: [0.13, -0.08, 0.004],
    material,
    radius: 0.022,
  }).rotation.z = 0.12;
  parent.add(coat);
}

function addShoes(parent, { x, y, z, material }) {
  for (const offset of [-0.055, 0.055]) {
    const shoe = addBox(parent, {
      name: "shoe",
      size: [0.09, 0.035, 0.16],
      pos: [x + offset, y, z],
      material,
      radius: 0.018,
    });
    shoe.rotation.y = offset < 0 ? -0.1 : 0.1;
  }
}

function createMaterials() {
  const bodyColor = colorForSpec();
  const topColor = lightenColor(bodyColor, 1.08);
  const screenColor = lightenColor(bodyColor, 1.03);
  return {
    matTop: new THREE.MeshStandardMaterial({ color: topColor, roughness: 0.76, metalness: 0.02 }),
    matSide: new THREE.MeshStandardMaterial({ color: bodyColor, roughness: 0.82, metalness: 0.02 }),
    matScreen: new THREE.MeshStandardMaterial({ color: screenColor, roughness: 0.88, metalness: 0.01 }),
    matBrass: new THREE.MeshStandardMaterial({ color: 0xc8a13c, roughness: 0.32, metalness: 0.72 }),
    matBlack: new THREE.MeshStandardMaterial({ color: 0x090909, roughness: 0.58, metalness: 0.22 }),
    matBlackSoft: new THREE.MeshStandardMaterial({ color: 0x171717, roughness: 0.74, metalness: 0.1 }),
    matCloth: new THREE.MeshStandardMaterial({ color: 0xf1eadb, roughness: 0.9, metalness: 0.01 }),
    matShoe: new THREE.MeshStandardMaterial({ color: 0x201712, roughness: 0.66, metalness: 0.04 }),
  };
}

function colorForSpec() {
  const allParts = Object.values(furnitureSpec.parts || {});
  const displayColor = allParts.find((part) => part?.displayColor)?.displayColor;
  const parsed = parseHexColor(displayColor);
  if (parsed) return parsed;
  const code = String(allParts.find((part) => part?.colorCode)?.colorCode || "").toUpperCase();
  const system = String(allParts.find((part) => part?.colorSystem)?.colorSystem || "").toUpperCase();
  const materialText = allParts.map((part) => `${part?.material || ""} ${part?.label || ""}`).join(" ").toLowerCase();
  if (materialText.includes("дуб денвер")) return 0xb09673;
  if (materialText.includes("светло-сер")) return 0xd7d8d2;
  if (materialText.includes("серый уголь")) return 0x575b5d;
  if (materialText.includes("бело-сер")) return 0xe3e1dc;
  if (system === "RAL" && code === "8019") return 0x403936;
  if (system === "RAL" && code === "9005") return 0x111111;
  if (system === "RAL" && code === "9011") return 0x111111;
  if (system === "NCS" && code.includes("3000")) return 0xf0eadf;
  if (system === "NCS" && code.includes("2000")) return 0xf3f1e9;
  return 0xeee6d7;
}

function parseHexColor(value) {
  const match = String(value || "").match(/^#?([0-9a-f]{6})$/i);
  return match ? Number.parseInt(match[1], 16) : null;
}

function lightenColor(color, factor) {
  const c = new THREE.Color(color);
  c.r = Math.min(1, c.r * factor);
  c.g = Math.min(1, c.g * factor);
  c.b = Math.min(1, c.b * factor);
  return c;
}

function addCableHose(parent, matBlack, matBlackSoft) {
  const curve = new THREE.CubicBezierCurve3(
    new THREE.Vector3(0.26, spec.height - spec.top - 0.04, -0.15),
    new THREE.Vector3(0.38, 0.5, -0.08),
    new THREE.Vector3(0.32, 0.24, -0.02),
    new THREE.Vector3(0.42, 0.035, -0.03),
  );
  const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 48, 0.018, 18), matBlack);
  tube.name = "flexible-cable-channel";
  tube.castShadow = true;
  tube.receiveShadow = true;
  parent.add(tube);

  const ringGeo = new THREE.TorusGeometry(0.021, 0.0024, 8, 28);
  for (let i = 0; i < 13; i += 1) {
    const t = i / 12;
    const point = curve.getPointAt(t);
    const tangent = curve.getTangentAt(t).normalize();
    const ring = new THREE.Mesh(ringGeo, matBlackSoft);
    ring.position.copy(point);
    ring.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), tangent);
    ring.castShadow = true;
    parent.add(ring);
  }

  const foot = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.06, 0.012, 32), matBlack);
  foot.position.set(0.42, 0.006, -0.03);
  foot.castShadow = true;
  parent.add(foot);
}

function addPcHolder(parent, matBlack, matBlackSoft) {
  const holder = new THREE.Group();
  holder.name = "pc-holder";
  holder.position.set(0.62, 0.33, -0.33);
  holder.rotation.y = -0.08;

  addBox(holder, {
    name: "holder-back",
    size: [0.12, 0.19, 0.012],
    pos: [0, 0, 0],
    material: matBlack,
    radius: 0.004,
  });
  for (const x of [-0.048, 0.048]) {
    addBox(holder, {
      name: "holder-rail",
      size: [0.012, 0.17, 0.028],
      pos: [x, 0, -0.018],
      material: matBlackSoft,
      radius: 0.002,
    });
  }
  for (const y of [-0.075, 0.075]) {
    addBox(holder, {
      name: "holder-cross",
      size: [0.11, 0.01, 0.03],
      pos: [0, y, -0.02],
      material: matBlackSoft,
      radius: 0.002,
    });
  }
  parent.add(holder);
}

function addOutlines(group) {
  const outlineMaterial = new THREE.LineBasicMaterial({
    color: 0x2b2924,
    transparent: true,
    opacity: 0.85,
  });
  const targets = [];
  group.traverse((obj) => {
    if (obj.isMesh && obj.geometry && !obj.name.includes("flexible")) {
      targets.push(obj);
    }
  });
  for (const mesh of targets) {
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, 28), outlineMaterial);
    edges.name = `${mesh.name}-outline`;
    edges.position.copy(mesh.position);
    edges.rotation.copy(mesh.rotation);
    edges.scale.copy(mesh.scale);
    edges.userData.outline = true;
    mesh.parent.add(edges);
  }
}

function buildShadowPlane() {
  const group = new THREE.Group();
  const shadow = new THREE.Mesh(
    new THREE.PlaneGeometry(Math.max(2.25, spec.width * 1.18), Math.max(1.12, spec.depth * 1.18)),
    new THREE.ShadowMaterial({ opacity: 0.18 }),
  );
  shadow.name = "contact-shadow";
  shadow.userData.shadow = true;
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = -spec.height / 2 - 0.004;
  shadow.receiveShadow = true;
  group.add(shadow);
  return group;
}

function fitCamera(camera, aspect, mode) {
  if (mode === "front") {
    const span = Math.max(2.92, spec.width * 1.58, spec.height * aspect * 1.32);
    camera.left = -span / 2;
    camera.right = span / 2;
    camera.top = span / aspect / 2;
    camera.bottom = -span / aspect / 2;
    camera.position.set(0, 0.06, 3.2);
    camera.lookAt(0, -0.02, 0);
  } else {
    const openWardrobePad = spec.type === "wardrobe" ? 1.18 : 1;
    const runPad = spec.type === "built_in_run" || spec.type === "kitchen_run" ? 1.08 : 1;
    const span = Math.max(2.8, spec.width * 1.55, spec.height * aspect * 1.18) * openWardrobePad * runPad;
    camera.left = -span / 2;
    camera.right = span / 2;
    camera.top = span / aspect / 2;
    camera.bottom = -span / aspect / 2;
    const presets = {
      sample: [-1.35, 0.78, -1.55],
      left: [-1.45, 0.82, -1.35],
      right: [1.45, 0.82, -1.35],
    };
    camera.position.set(...presets[state.angle]);
    camera.lookAt(0.03, -0.12, -0.05);
  }
  camera.near = 0.01;
  camera.far = 10;
  camera.updateProjectionMatrix();
}

function renderAll() {
  front.render();
  iso.render();
}

function updateOverlay(mode, svg, canvas, camera) {
  if (!svg) return;
  const box = svg.getBoundingClientRect();
  svg.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
  if (mode === "front") {
    svg.innerHTML = makeOverlayDefs("front-arrow") + frontOverlay(projector(camera, canvas, svg));
  } else {
    svg.innerHTML = makeOverlayDefs("iso-arrow") + isoOverlay(projector(camera, canvas, svg));
  }
}

function projector(camera, canvas, svg) {
  const canvasBox = canvas.getBoundingClientRect();
  const svgBox = svg.getBoundingClientRect();
  return (x, y, z = -spec.depth / 2) => {
    const p = new THREE.Vector3(x, y - spec.height / 2, z).project(camera);
    return {
      x: canvasBox.left - svgBox.left + (p.x + 1) * 0.5 * canvasBox.width,
      y: canvasBox.top - svgBox.top + (-p.y + 1) * 0.5 * canvasBox.height,
    };
  };
}

function makeOverlayDefs(id) {
  return `
    <defs>
      <marker id="${id}" viewBox="0 0 8 8" markerWidth="6" markerHeight="6" refX="4" refY="4" orient="auto">
        <path d="M0.8,0.8 L7.2,4 L0.8,7.2 Z" fill="#151515"></path>
      </marker>
    </defs>
  `;
}

function frontOverlay(P) {
  if (spec.type !== "desk_panel") return genericFrontOverlay(P);
  const W = spec.width;
  const D = spec.depth;
  const H = spec.height;
  const top = spec.top;
  const over = spec.overhang;
  const side = spec.side;
  const screen = spec.screen;
  const leftTop = P(-W / 2 - over, H, -D / 2);
  const rightTop = P(W / 2 + over, H, -D / 2);
  const leftTopBottom = P(-W / 2 - over, H - top, -D / 2);
  const rightTopBottom = P(W / 2 + over, H - top, -D / 2);
  const leftBottom = P(-W / 2, 0, -D / 2);
  const screenTop = P(-W / 2 + side, H - top, -D / 2);
  const screenBottom = P(-W / 2 + side, H - top - screen, -D / 2);
  const topPoint = P(-W * 0.34, H - top / 2, -D / 2);
  const screenPoint = P(-W * 0.18, H - top - screen * 0.52, -D / 2);
  const legPoint = P(-W / 2 + side / 2, H * 0.38, -D / 2);
  const hose = P(0.42, H * 0.38, -0.04);
  const holder = P(0.62, 0.34, -D / 2);

  const widthY = leftTop.y - 33;
  const heightX = Math.max(56, leftTop.x - 74);
  const screenX = leftTop.x + 70;
  const topX = rightTop.x + 31;
  const overX = Math.max(28, leftTop.x - 118);
  const leftLabelX = Math.max(188, leftTop.x - 166);
  const rightLabelX = Math.min(548, rightTop.x + 24);

  const callouts = [
    callout(topPoint, { x: leftLabelX, y: leftTop.y + 24, w: 190 }, spec.callouts.top),
    callout(legPoint, { x: leftLabelX, y: leftBottom.y - 52, w: 164 }, spec.callouts.sidePanels),
    callout(screenPoint, { x: rightLabelX, y: screenTop.y + 18, maxChars: 22 }, spec.callouts.frontScreen),
  ];
  if (spec.features.pcHolder) {
    callouts.push(callout(holder, { x: rightLabelX, y: screenBottom.y + 24, maxChars: 20 }, spec.callouts.pcHolder));
  }
  if (spec.features.cableChannel) {
    callouts.push(callout(hose, { x: rightLabelX, y: leftBottom.y - 26, maxChars: 22 }, spec.callouts.cableChannel));
  }

  return `
    <g class="dim">
      ${dimH(leftTop.x, rightTop.x, leftTop.y, widthY, spec.labels.width, "front-arrow")}
      ${dimV(leftTop.y, leftBottom.y, leftTop.x, heightX, spec.labels.height, "front-arrow", "left")}
      ${dimV(screenTop.y, screenBottom.y, screenTop.x, screenX, spec.labels.screen, "front-arrow", "right")}
      ${dimV(rightTop.y, rightTopBottom.y, rightTop.x, topX, spec.labels.top, "front-arrow", "right")}
      ${dimV(leftTop.y - 2, leftTopBottom.y, leftTop.x, overX, spec.labels.overhang, "front-arrow", "left")}
    </g>
    <g class="callout">
      ${callouts.join("")}
    </g>
  `;
}

function isoOverlay(P) {
  if (spec.type !== "desk_panel") return genericIsoOverlay(P);
  const W = spec.width;
  const D = spec.depth;
  const H = spec.height;
  const side = spec.side;
  const topA = P(-W / 2, H, -D / 2);
  const depthB = P(-W / 2, H, D / 2);
  const brassPt = P(W / 2 - side / 2, 0.34, -D / 2);
  const callouts = [];
  if (spec.features.brass) {
    callouts.push(callout(brassPt, { x: 512, y: 168 }, spec.callouts.brass));
  }

  return `
    <g class="dim">
      ${dimAlong(topA, depthB, -22, spec.labels.depth, "iso-arrow")}
    </g>
    <g class="callout">
      ${callouts.join("")}
    </g>
  `;
}

function genericFrontOverlay(P) {
  const W = spec.width;
  const D = spec.depth;
  const H = spec.height;
  const leftTop = P(-W / 2, H, -D / 2);
  const rightTop = P(W / 2, H, -D / 2);
  const leftBottom = P(-W / 2, 0, -D / 2);
  const bodyPoint = P(-W * 0.2, H * 0.68, -D / 2);
  const featurePoint = P(W * 0.26, H * 0.5, -D / 2);
  const lowerPoint = P(W * 0.18, H * 0.12, -D / 2);
  const widthY = leftTop.y - 28;
  const heightX = Math.max(42, leftTop.x - 64);
  const rightLabelX = Math.min(548, rightTop.x + 24);
  const leftLabelX = Math.max(170, leftTop.x - 132);
  const callouts = [];
  if (shouldShowPrimaryOnFront()) {
    callouts.push(callout(bodyPoint, { x: rightLabelX, y: leftTop.y + 40 }, primaryCalloutLines()));
  }
  const feature = featureCalloutLines();
  if (feature.length && shouldShowFeatureOnFront()) {
    callouts.push(callout(featurePoint, { x: rightLabelX, y: leftTop.y + 116 }, feature));
  }
  const lower = lowerCalloutLines();
  if (lower.length && shouldShowLowerOnFront()) {
    callouts.push(callout(lowerPoint, { x: leftLabelX, y: leftBottom.y - 44 }, lower));
  }

  return `
    <g class="dim">
      ${dimH(leftTop.x, rightTop.x, leftTop.y, widthY, spec.labels.width, "front-arrow")}
      ${dimV(leftTop.y, leftBottom.y, leftTop.x, heightX, spec.labels.height, "front-arrow", "left")}
    </g>
    <g class="callout">
      ${callouts.join("")}
    </g>
  `;
}

function genericIsoOverlay(P) {
  if (spec.type === "wardrobe" && (spec.features.rod || spec.features.hatShelf || spec.features.shoeShelf)) {
    return wardrobeIsoOverlay(P);
  }
  const W = spec.width;
  const D = spec.depth;
  const H = spec.height;
  const topA = P(-W / 2, H, -D / 2);
  const topB = P(W / 2, H, -D / 2);
  const depthB = P(W / 2, H, D / 2);
  const featurePoint = P(W * 0.32, H * 0.46, -D / 2);
  const lowerPoint = P(-W * 0.28, H * 0.22, -D / 2);
  const callouts = [];
  if (shouldShowPrimaryOnIso()) {
    const bodyPoint = P(W * 0.05, H * 0.72, -D / 2);
    callouts.push(callout(bodyPoint, { x: 512, y: 60 }, primaryCalloutLines()));
  }
  const feature = featureCalloutLines();
  if (feature.length && shouldShowFeatureOnIso()) {
    callouts.push(callout(featurePoint, { x: 512, y: 132 }, feature));
  }
  const lower = lowerCalloutLines();
  if (lower.length && shouldShowLowerOnIso()) {
    callouts.push(callout(lowerPoint, { x: 168, y: 228 }, lower));
  }

  return `
    <g class="dim">
      ${dimAlong(topA, topB, -26, spec.labels.width, "iso-arrow")}
      ${dimAlong(topB, depthB, -22, spec.labels.depth, "iso-arrow")}
    </g>
    <g class="callout">
      ${callouts.join("")}
    </g>
  `;
}

function wardrobeIsoOverlay(P) {
  const W = spec.width;
  const D = spec.depth;
  const H = spec.height;
  const topA = P(-W / 2, H, -D / 2);
  const topB = P(W / 2, H, -D / 2);
  const depthB = P(W / 2, H, D / 2);
  const rodPoint = P(-W * 0.28, H * 0.68, -D * 0.16);
  const coatPoint = P(-W * 0.25, H * 0.46, -D * 0.17);
  const shoePoint = P(-W * 0.26, H * 0.13, -D * 0.17);
  const doorPoint = P(W * 0.5, H * 0.5, -D / 2);

  return `
    <g class="dim">
      ${dimAlong(topA, topB, -26, spec.labels.width, "iso-arrow")}
      ${dimAlong(topB, depthB, -22, spec.labels.depth, "iso-arrow")}
    </g>
    <g class="callout">
      ${callout(doorPoint, { x: 510, y: 76 }, ["Двери", "распашные, открыты"])}
      ${callout(rodPoint, { x: 510, y: 142 }, ["Штанга", "выдвижная"])}
      ${callout(coatPoint, { x: 166, y: 118 }, ["Одежда", "визуализация наполнения"])}
      ${callout(shoePoint, { x: 166, y: 238 }, ["Полка для обуви", "глубина 300мм"])}
    </g>
  `;
}

function shouldShowPrimaryOnFront() {
  return true;
}

function shouldShowPrimaryOnIso() {
  return spec.type === "coffee_round" || spec.type === "coffee_fluted";
}

function shouldShowFeatureOnFront() {
  return ["drawer_unit", "cabinet", "built_in_run", "kitchen_run", "countertop", "lectern"].includes(spec.type);
}

function shouldShowFeatureOnIso() {
  return spec.type === "coffee_round" || spec.type === "coffee_fluted";
}

function shouldShowLowerOnFront() {
  return spec.type !== "coffee_round" && spec.type !== "coffee_fluted";
}

function shouldShowLowerOnIso() {
  return spec.type === "coffee_round" || spec.type === "coffee_fluted";
}

function primaryCalloutLines() {
  const top = spec.callouts.top || [];
  if (spec.type === "coffee_round" || spec.type === "coffee_fluted") {
    return top[0] ? top : ["Каркас", "МДФ"];
  }
  if (spec.type === "wardrobe" || spec.type === "cabinet") {
    return ["Корпус и фасады", top[1] || "МДФ"];
  }
  if (spec.type === "built_in_run") {
    return ["Система шкафов", top[1] || "ЛДСП, модульная линия"];
  }
  if (spec.type === "kitchen_run") {
    return ["Кухонные модули", top[1] || "ЛДСП, фасады и корпус"];
  }
  if (spec.type === "drawer_unit") {
    return ["Столешница / корпус", top[1] || "МДФ"];
  }
  if (spec.type === "countertop") {
    return ["Столешница", top[1] || "материал по ТЗ"];
  }
  if (spec.type === "lectern") {
    return ["Корпус трибуны", top[1] || "ЛДСП"];
  }
  return top;
}

function featureCalloutLines() {
  const parts = furnitureSpec.parts || {};
  if (spec.type === "drawer_unit") {
    return ["Ящики", `${spec.features.drawers || 3} шт., накладные фасады`];
  }
  if (spec.type === "wardrobe" || spec.type === "cabinet") {
    if (spec.features.rod) return ["Внутреннее наполнение", "штанга, полки, обувная полка"];
    if (spec.features.shelves) return ["Полки", `${spec.features.shelves} шт., регулируемые`];
    return ["Двери", parts.doors?.material || "push-to-open"];
  }
  if (spec.type === "built_in_run") {
    return ["Секции", `${spec.features.sectionCount || 2} модулей, фасады по ширине`];
  }
  if (spec.type === "kitchen_run") {
    return ["Модули", `${spec.features.sectionCount || spec.modules.length || 3} секций, верх/низ`];
  }
  if (spec.type === "countertop") {
    return ["Обрамление", "видимая рамка / опоры по ТЗ"];
  }
  if (spec.type === "lectern") {
    return ["Фронтальная панель", "накладная, в цвет корпуса"];
  }
  if (spec.type === "coffee_fluted") {
    return ["Поверхность", "рифлёные вертикальные канелюры"];
  }
  if (spec.type === "coffee_round") {
    return ["Покрытие", spec.features.matteLacquer ? "прозрачный матовый лак" : "по согласованию"];
  }
  return [];
}

function lowerCalloutLines() {
  if (spec.features.brass) return ["Вставки", "латунь"];
  if (spec.features.plinth || spec.features.plinthBlack) {
    return ["Цоколь", spec.features.plinthBlack ? "чёрный матовый" : "в цвет корпуса"];
  }
  if (spec.features.metalFrame) return ["Опоры", "металл RAL 9005"];
  if (spec.features.feltPads) return ["Подпятники", "фетровые"];
  return [];
}

function dimH(x1, x2, yObj, yDim, label, markerId) {
  const over = yDim < yObj ? 8 : -8;
  const textY = yDim - 12;
  return `
    <path d="M${f(x1)} ${f(yObj)} L${f(x1)} ${f(yDim + over)} M${f(x2)} ${f(yObj)} L${f(x2)} ${f(yDim + over)}"></path>
    <path d="M${f(x1)} ${f(yDim)} L${f(x2)} ${f(yDim)}" marker-start="url(#${markerId})" marker-end="url(#${markerId})"></path>
    <text x="${f((x1 + x2) / 2)}" y="${f(textY)}" text-anchor="middle">${escapeXml(label)}</text>
  `;
}

function dimV(y1, y2, xObj, xDim, label, markerId, side = "left") {
  const a = Math.min(y1, y2);
  const b = Math.max(y1, y2);
  const over = xDim < xObj ? 8 : -8;
  const anchor = side === "right" ? "start" : "end";
  const tx = side === "right" ? xDim + 8 : xDim - 8;
  return `
    <path d="M${f(xObj)} ${f(a)} L${f(xDim + over)} ${f(a)} M${f(xObj)} ${f(b)} L${f(xDim + over)} ${f(b)}"></path>
    <path d="M${f(xDim)} ${f(a)} L${f(xDim)} ${f(b)}" marker-start="url(#${markerId})" marker-end="url(#${markerId})"></path>
    <text x="${f(tx)}" y="${f((a + b) / 2)}" text-anchor="${anchor}">${escapeXml(label)}</text>
  `;
}

function dimAlong(a, b, offset, label, markerId) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const nx = -dy / len;
  const ny = dx / len;
  const ax = a.x + nx * offset;
  const ay = a.y + ny * offset;
  const bx = b.x + nx * offset;
  const by = b.y + ny * offset;
  const mx = (ax + bx) / 2;
  const my = (ay + by) / 2 - 10;
  return `
    <path d="M${f(a.x)} ${f(a.y)} L${f(ax)} ${f(ay)} M${f(b.x)} ${f(b.y)} L${f(bx)} ${f(by)}"></path>
    <path d="M${f(ax)} ${f(ay)} L${f(bx)} ${f(by)}" marker-start="url(#${markerId})" marker-end="url(#${markerId})"></path>
    <text x="${f(mx)}" y="${f(my)}" text-anchor="middle">${escapeXml(label)}</text>
  `;
}

function callout(point, label, lines) {
  const bendX = label.x > point.x ? label.x - 18 : label.x + 18;
  const bendY = label.y + 8;
  const textAnchor = label.x > point.x ? "start" : "end";
  const wrapped = wrapCalloutLines(lines, label.maxChars || 28);
  return `
    <circle class="dot" cx="${f(point.x)}" cy="${f(point.y)}" r="3"></circle>
    <path d="M${f(point.x)} ${f(point.y)} L${f(bendX)} ${f(bendY)} L${f(label.x)} ${f(bendY)}"></path>
    <text x="${f(label.x)}" y="${f(label.y)}" text-anchor="${textAnchor}">
      ${wrapped.map((line, i) => `<tspan x="${f(label.x)}" dy="${i === 0 ? 0 : 15}">${escapeXml(line)}</tspan>`).join("")}
    </text>
  `;
}

function wrapCalloutLines(lines, maxChars) {
  const out = [];
  for (const line of lines || []) {
    const text = String(line || "").trim();
    if (text.length <= maxChars) {
      out.push(text);
      continue;
    }
    let current = "";
    for (const word of text.split(/\s+/)) {
      if (!current) {
        current = word;
      } else if (`${current} ${word}`.length <= maxChars) {
        current += ` ${word}`;
      } else {
        out.push(current);
        current = word;
      }
    }
    if (current) out.push(current);
  }
  return out.slice(0, 4);
}

function f(value) {
  return Number(value).toFixed(2).replace(/\.?0+$/, "");
}

function numberOr(value, fallback) {
  const num = Number(value);
  return Number.isFinite(num) && num > 0 ? num : fallback;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function formatMm(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "";
  if (Math.abs(num - Math.round(num)) < 1e-6) return String(Math.round(num));
  return String(Number(num.toFixed(2)));
}

function materialText(part, fallback) {
  const material = part.material || "МДФ";
  const chunks = [];
  if (part.thicknessMm) {
    chunks.push(`${material} ${formatMm(part.thicknessMm)}мм`);
  } else if (material) {
    chunks.push(material);
  }
  if (part.heightMm) chunks.push(`h=${formatMm(part.heightMm)}мм`);
  const color = [part.colorSystem, part.colorCode].filter(Boolean).join(" ");
  if (color) chunks.push(color);
  if (part.finish) chunks.push(part.finish);
  return chunks.join(", ") || fallback;
}

function setText(selector, value) {
  const el = document.querySelector(selector);
  if (el) el.textContent = value;
}

function option(value, label) {
  const item = document.createElement("option");
  item.value = value;
  item.textContent = label;
  return item;
}

function normalizePath(path) {
  return String(path || "").replace(/^\.\//, "");
}

function escapeXml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

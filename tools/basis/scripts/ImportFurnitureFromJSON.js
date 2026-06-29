/**
 * БАЗИС-Мебельщик: импорт JSON → готовая модель (панели + фурнитура).
 * Версия API >= 2. БАЗИС 2026.x.
 *
 * horizont: NewPanel(X, Z)
 * vertical: NewPanel(Z, Y)
 * front:    NewPanel(X, Y)
 *
 * Фурнитура: hardware.*.furniture_encoded (один раз выбрать в БАЗИС → вставить в JSON).
 */

// eslint-disable-next-line no-undef
const fileName = system.askFileName(
  'JSON проекта',
  'JSON (*.json)|*.json|Все файлы (*.*)|*.*'
);

if (!fileName) {
  // eslint-disable-next-line no-undef
  alert('Файл не выбран');
} else {
  runImport(fileName);
}

function runImport(path) {
  // eslint-disable-next-line no-undef
  const raw = system.readTextFile(path);
  const project = JSON.parse(raw);
  const placementByName = buildPlacementMap(project);
  const panelMap = buildPanels(project);

  let handleCount = 0;
  let guideResult = { ok: 0, fail: 0, notes: [] };

  handleCount = buildHandles(project, panelMap, placementByName);
  guideResult = buildDrawerGuides(project, panelMap, placementByName);

  let msg =
    project.project_name +
    '\n\nПанелей: ' +
    project.panels.length +
    '\nРучек: ' +
    handleCount +
    '\nНаправляющие (успешных установок): ' +
    guideResult.ok;
  if (guideResult.fail > 0) {
    msg += '\nНе установлено: ' + guideResult.fail;
  }
  if (guideResult.notes.length > 0) {
    msg += '\n\n' + guideResult.notes.join('\n');
  }
  msg += '\n\nДальше в БАЗИС: материалы/кромка → отчёты → раскрой/чертежи.';
  // eslint-disable-next-line no-undef
  alert(msg);
}

function buildPlacementMap(project) {
  const map = {};
  if (!project || !Array.isArray(project.panels)) return map;
  for (let i = 0; i < project.panels.length; i++) {
    const p = project.panels[i];
    if (p && p.name && p.placement) map[p.name] = p.placement;
  }
  return map;
}

function buildPanels(project) {
  const thick = project.materials.board_thickness || 16;
  const matName = project.materials.color_code
    ? project.materials.color + ' (' + project.materials.color_code + ')'
    : project.materials.color;

  const panelMap = {};
  for (let i = 0; i < project.panels.length; i++) {
    const p = project.panels[i];
    const obj = buildOnePanel(p, thick, matName);
    if (obj && p && p.name) panelMap[p.name] = obj;
  }
  return panelMap;
}

function buildOnePanel(p, thick, matName) {
  const orient = getPanelOrientation(p);
  let sizeW;
  let sizeH;

  if (p.placement) {
    const pl = p.placement;
    const spanX = pl.x2 - pl.x1;
    const spanZ = pl.z2 - pl.z1;
    // eslint-disable-next-line no-undef
    const PO = objects3d.PanelOrientation;
    if (orient === PO.horizont) {
      sizeW = spanX;
      sizeH = spanZ;
    } else if (orient === PO.vertical) {
      sizeW = spanZ;
      sizeH = pl.y2 - pl.y1;
    } else {
      sizeW = spanX;
      sizeH = pl.y2 - pl.y1;
    }
  } else {
    sizeW = p.dimensions.width;
    sizeH = p.dimensions.height;
  }

  // eslint-disable-next-line no-undef
  const panel = objects3d.NewPanel(sizeW, sizeH, orient);
  panel.Thickness = p.thickness || thick;
  panel.Name = p.name;
  if (matName) {
    try {
      panel.MaterialName = matName;
    } catch (e) {
      /* материал может отсутствовать в базе */
    }
  }
  panel.Build();

  if (p.placement) {
    alignGabMin(panel, p.placement.x1, p.placement.y1, p.placement.z1);
  } else if (p.position) {
    alignGabMin(panel, p.position.x, p.position.y, p.position.z);
  }

  return panel;
}

function isTopBottom(p) {
  const t = (p.type || '').toLowerCase();
  return t === 'bottom' || t === 'top';
}

function isInternalHorizont(p) {
  const t = (p.type || '').toLowerCase();
  return t === 'shelf' || t === 'drawer_bottom';
}

function getPanelOrientation(p) {
  // eslint-disable-next-line no-undef
  const PO = objects3d.PanelOrientation;
  const key = (p.basis_orientation || '').toLowerCase();
  if (key === 'horizont' || key === 'horizontal') return PO.horizont;
  if (key === 'vertical') return PO.vertical;
  if (key === 'front') return PO.front;
  const t = (p.type || '').toLowerCase();
  if (isTopBottom(p) || isInternalHorizont(p)) return PO.horizont;
  if (
    t === 'side_left' ||
    t === 'side_right' ||
    t === 'vertical_partition' ||
    t === 'drawer_side_left' ||
    t === 'drawer_side_right'
  ) {
    return PO.vertical;
  }
  if (t === 'drawer_back' || t === 'drawer_front' || t === 'door_front') {
    return PO.front;
  }
  return PO.front;
}

function alignGabMin(panel, x, y, z) {
  const gmin = panel.GabMin;
  const dx = x - gmin.x;
  const dy = y - gmin.y;
  const dz = z - gmin.z;
  if (Math.abs(dx) < 0.01 && Math.abs(dy) < 0.01 && Math.abs(dz) < 0.01) {
    return;
  }
  // eslint-disable-next-line no-undef
  panel.TranslateGCS(geometry3d.VectorMake(dx, dy, dz));
}

function getActionProperties() {
  // eslint-disable-next-line no-undef
  const Action3D = Action;
  if (!Action3D || !Action3D.Properties || !Action3D.Properties.NewFurnitureValue) {
    return null;
  }
  return Action3D.Properties;
}

/**
 * Получить furniture_encoded (выбор в БАЗИС один раз → строка в JSON).
 * @returns {string|null}
 */
function loadFurnitureEncoded(config, chooseTitle, chooseHint, jsonKey) {
  if (!config) return null;
  const props = getActionProperties();
  if (!props) return null;

  let encoded = (config.furniture_encoded || '').trim();
  const filePath = (config.furniture_file || '').trim();

  if (encoded) {
    try {
      const test = newFurnitureFromEncoded(encoded);
      if (test) return encoded;
    } catch (e) {
      // eslint-disable-next-line no-undef
      alert(chooseTitle + '\n\nНе удалось прочитать furniture_encoded. Выберите заново.');
      encoded = '';
    }
  }

  if (filePath) {
    try {
      // eslint-disable-next-line no-undef
      const fromFile = OpenFurniture(filePath);
      if (fromFile) {
        return fromFile.EncodeToString();
      }
    } catch (e) {
      /* файл не найден */
    }
  }

  // eslint-disable-next-line no-undef
  const furn = props.NewFurnitureValue();
  // eslint-disable-next-line no-undef
  alert(chooseTitle + '\n\n' + chooseHint);
  if (!furn.Choose()) return null;

  const out = furn.EncodeToString();
  // eslint-disable-next-line no-undef
  alert(
    'Фурнитура выбрана. Скопируйте строку в JSON:\n' +
      jsonKey +
      '\n\n' +
      out
  );
  return out;
}

/** Новый экземпляр фурнитуры на каждую установку (иначе в модель попадает только последняя). */
function newFurnitureFromEncoded(encoded) {
  const props = getActionProperties();
  if (!props || !encoded) return null;
  // eslint-disable-next-line no-undef
  const furn = props.NewFurnitureValue();
  furn.DecodeFromString(encoded);
  return furn;
}

function isObject3(obj) {
  return obj && typeof obj === 'object';
}

/**
 * Mount1: сначала координаты в ГСК (как у ручек), затем в ЛСК панели.
 */
function mountOnPanelFace(encoded, panel, gx, gy, gz) {
  if (!panel || !encoded) return null;

  const furn = newFurnitureFromEncoded(encoded);
  if (!furn) return null;

  try {
    const obj = furn.Mount1(panel, gx, gy, gz, 0);
    if (isObject3(obj)) return obj;
  } catch (e1) {
    /* пробуем ЛСК */
  }

  try {
    // eslint-disable-next-line no-undef
    const g = geometry3d.VectorMake(gx, gy, gz);
    const loc = panel.ToObject(g);
    const obj = furn.Mount1(panel, loc.x, loc.y, loc.z, 0);
    if (isObject3(obj)) return obj;
  } catch (e2) {
    /* */
  }

  return null;
}

/**
 * Направляющие «в секцию ящика» — для фурнитуры с типом монтажа box.
 */
function mountGuideInDrawerBox(encoded, plLeft, plRight) {
  if (!plLeft || !plRight || !encoded) return null;

  const posX = plLeft.x1;
  const posY = plLeft.y1;
  const posZ = plLeft.z1;
  const sizeX = plRight.x2 - plLeft.x1;
  const sizeY = plLeft.y2 - plLeft.y1;
  const sizeZ = plLeft.z2 - plLeft.z1;

  if (sizeX <= 0 || sizeY <= 0 || sizeZ <= 0) return null;

  const furn = newFurnitureFromEncoded(encoded);
  if (!furn || typeof furn.MountBox !== 'function') return null;

  try {
    // eslint-disable-next-line no-undef
    const pos = geometry3d.VectorMake(posX, posY, posZ);
    // eslint-disable-next-line no-undef
    const size = geometry3d.VectorMake(sizeX, sizeY, sizeZ);
    // eslint-disable-next-line no-undef
    const axisZ = geometry3d.VectorMake(0, 0, 1);
    // eslint-disable-next-line no-undef
    const axisY = geometry3d.VectorMake(0, 1, 0);
    const obj = furn.MountBox(pos, size, axisZ, axisY);
    if (isObject3(obj)) return obj;
  } catch (e) {
    /* */
  }
  return null;
}

/**
 * Панели корпуса и ящика не соприкасаются (зазор ~16 мм) — Mount(panel1,panel2) не создаёт объект.
 * Ставим направляющую на грань: корпус (внутренняя) + ящик (наружная).
 */
function mountGuidePairOnFaces(encoded, panelCab, panelDr, plCab, plDr, cabFaceX, drFaceX) {
  let count = 0;
  const yMid = (plDr.y1 + plDr.y2) / 2;
  const zMid = (plDr.z1 + plDr.z2) / 2;

  if (mountOnPanelFace(encoded, panelCab, cabFaceX, yMid, zMid)) count++;
  if (mountOnPanelFace(encoded, panelDr, drFaceX, yMid, zMid)) count++;

  return count;
}

function buildHandles(project, panelMap, placementByName) {
  if (!project.hardware || !project.hardware.handles) return 0;

  const handles = project.hardware.handles;
  if (!handles || !handles.count || handles.count <= 0) return 0;
  const offsetFromTop = typeof handles.offset_from_top === 'number' ? handles.offset_from_top : 32;

  const encoded = loadFurnitureEncoded(
    handles,
    'Ручки',
    'ТЗ: профильная, межосевое 128 мм, чёрная.\nВыберите ручку в каталоге БАЗИС.',
    'hardware.handles.furniture_encoded'
  );
  if (!encoded) return 0;

  // 1) Старые проекты: фиксированные имена фасадов
  const targetNames = ['Фасад ящик 1', 'Фасад ящик 2', 'Фасад ящик 3', 'Фасад двери'];
  let count = 0;
  for (let i = 0; i < targetNames.length; i++) {
    const name = targetNames[i];
    const panel = panelMap[name];
    const pl = placementByName[name];
    if (!panel || !pl) continue;

    const x = (pl.x1 + pl.x2) / 2;
    const y = Math.max(pl.y1, pl.y2 - offsetFromTop);
    const z = pl.z1;
    if (mountOnPanelFace(encoded, panel, x, y, z)) count++;
  }
  if (count > 0) return count;

  // 2) Универсально: все фасады типа door_front / drawer_front
  if (!project || !Array.isArray(project.panels)) return 0;
  for (let i = 0; i < project.panels.length; i++) {
    const p = project.panels[i];
    if (!p || !p.name) continue;
    const t = String(p.type || '').toLowerCase();
    if (t !== 'door_front' && t !== 'drawer_front') continue;
    const panel = panelMap[p.name];
    const pl = placementByName[p.name];
    if (!panel || !pl) continue;

    const x = (pl.x1 + pl.x2) / 2;
    const y = Math.max(pl.y1, pl.y2 - offsetFromTop);
    const z = pl.z1;
    if (mountOnPanelFace(encoded, panel, x, y, z)) count++;
  }
  return count;
}

function drawerSidePanelName(drawerId, side) {
  const n = String(drawerId).replace('drawer_', '');
  return 'Ящик ' + n + ' боковина ' + (side === 'left' ? 'левая' : 'правая');
}

function buildDrawerGuides(project, panelMap, placementByName) {
  const result = { ok: 0, fail: 0, notes: [] };
  const guides = project.hardware && project.hardware.drawer_guides;
  if (!guides || !Array.isArray(project.drawers)) return result;

  const cabLeftName = guides.cabinet_left_panel || 'Боковина левая';
  const cabRightName = guides.cabinet_right_panel || 'Перегородка 1';

  const encoded = loadFurnitureEncoded(
    guides,
    'Направляющие ящиков',
    'Выберите НАСТОЯЩУЮ направляющую (не ручку-кнопку).\n' +
      'ТЗ: шариковые, длина ~350 мм, с доводчиком.\n' +
      'Тип монтажа в каталоге: «секция/ящик» или «параллельные грани».',
    'hardware.drawer_guides.furniture_encoded'
  );
  if (!encoded) {
    result.notes.push('Направляющие не выбраны.');
    return result;
  }

  for (let i = 0; i < project.drawers.length; i++) {
    const dr = project.drawers[i];
    if (!dr || !dr.id) continue;

    const leftName = drawerSidePanelName(dr.id, 'left');
    const rightName = drawerSidePanelName(dr.id, 'right');
    const plDrL = placementByName[leftName];
    const plDrR = placementByName[rightName];
    const plCabL = placementByName[cabLeftName];
    const plCabR = placementByName[cabRightName];

    let drawerOk = 0;

    // 1) Монтаж в объём ящика (предпочтительно для направляющих)
    if (mountGuideInDrawerBox(encoded, plDrL, plDrR)) {
      drawerOk += 2;
    } else {
      // 2) На грани: внутр. корпус + наружн. боковина ящика (зазор 16 мм — Mount между панелями не работает)
      if (plCabL && plDrL) {
        drawerOk += mountGuidePairOnFaces(
          encoded,
          panelMap[cabLeftName],
          panelMap[leftName],
          plCabL,
          plDrL,
          plCabL.x2,
          plDrL.x1
        );
      }
      if (plCabR && plDrR) {
        drawerOk += mountGuidePairOnFaces(
          encoded,
          panelMap[cabRightName],
          panelMap[rightName],
          plCabR,
          plDrR,
          plCabR.x1,
          plDrR.x2
        );
      }
    }

    if (drawerOk > 0) {
      result.ok += drawerOk;
    } else {
      result.fail++;
      result.notes.push(dr.id + ': направляющая не создана (проверьте тип фурнитуры в каталоге).');
    }
  }

  if (result.ok === 0 && result.fail > 0) {
    result.notes.push(
      'Ручка-кнопка не подходит для направляющих. Нужна фурнитура «направляющая» из каталога БАЗИС.'
    );
  }

  return result;
}

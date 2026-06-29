// Minimal BasisProductionModel importer for BAZIS-Mebelshchik.
// Scope of this PoC: rectangular panels, blocks, materials, and edge banding.

const fs = require('fs');

const inputFile = chooseInputFile();
const spec = JSON.parse(fs.readFileSync(inputFile, 'utf8'));
const report = {
  inputFile,
  outputFile: spec && spec.output ? spec.output.b3d : null,
  createdPanels: [],
  warnings: [],
  errors: []
};

try {
  validateSpec(spec);
  newModel();

  for (const blockSpec of spec.blocks || []) {
    const owner = beginBlock(blockSpec.name || blockSpec.id);
    for (const panelSpec of blockSpec.panels || []) {
      const panel = createPanel(panelSpec, owner);
      report.createdPanels.push({
        id: panelSpec.id,
        name: panelSpec.name,
        orientation: panelSpec.orientation
      });
    }
    endBlock(owner);
  }

  commitChanges('Import BasisProductionModel ' + (spec.order && spec.order.id ? spec.order.id : ''));
  saveModel(spec.output.b3d);
  writeReport(spec.output.report || 'basis-import-report.json');
} catch (error) {
  report.errors.push(error.message || String(error));
  writeReport(spec && spec.output && spec.output.report ? spec.output.report : 'basis-import-report.json');
  throw error;
}

function chooseInputFile() {
  if (typeof UI !== 'undefined' && UI.dialogs && UI.dialogs.RunOpenFileDialog) {
    const chosen = UI.dialogs.RunOpenFileDialog({
      title: 'Choose BasisProductionModel JSON',
      extensions: ['json']
    });
    if (chosen) return chosen;
  }
  if (typeof system !== 'undefined' && system.askFileName) {
    return system.askFileName('json');
  }
  throw new Error('Cannot open file dialog in this BAZIS scripting environment.');
}

function validateSpec(value) {
  assert(value && typeof value === 'object', 'Spec must be an object.');
  assert(value.schemaVersion === 'basis-production-model-v1', 'Unsupported schemaVersion.');
  assert(value.units === 'mm', 'Only millimeters are supported.');
  assert(value.output && value.output.b3d, 'output.b3d is required.');
  assert(value.materials && value.materials.panels, 'materials.panels is required.');
  assert(Array.isArray(value.blocks), 'blocks must be an array.');
}

function createPanel(panelSpec, owner) {
  assert(panelSpec.id, 'panel.id is required.');
  assert(panelSpec.orientation, 'panel.orientation is required for ' + panelSpec.id);
  assert(panelSpec.coords, 'panel.coords is required for ' + panelSpec.id);

  setActivePanelMaterial(panelSpec.material);

  const panel = createRectPanel(panelSpec, owner);
  panel.Name = panelSpec.name || panelSpec.id;
  panel.ArtPos = panelSpec.id;

  if (panelSpec.userProperties && panel.UserProperty) {
    for (const key in panelSpec.userProperties) {
      panel.UserProperty[key] = String(panelSpec.userProperties[key]);
    }
  }

  for (const edge of panelSpec.edges || []) {
    addPanelEdge(panel, panelSpec, edge);
  }

  if (panel.Build) panel.Build();
  return panel;
}

function createRectPanel(panelSpec, owner) {
  const c = panelSpec.coords;
  if (hasLegacyPanelHelpers()) {
    if (panelSpec.orientation === 'vertical') {
      return AddVertPanel(c.z1Mm, c.y1Mm, c.z2Mm, c.y2Mm, c.xMm);
    }
    if (panelSpec.orientation === 'horizont') {
      return AddHorizPanel(c.x1Mm, c.z1Mm, c.x2Mm, c.z2Mm, c.yMm);
    }
    if (panelSpec.orientation === 'front') {
      return AddFrontPanel(c.x1Mm, c.y1Mm, c.x2Mm, c.y2Mm, c.zMm);
    }
  }

  if (typeof objects3d !== 'undefined' && objects3d.NewPanel) {
    const size = panelSize(panelSpec);
    const orientation = objects3d.PanelOrientation[panelSpec.orientation];
    const panel = objects3d.NewPanel(size.widthMm, size.heightMm, orientation, owner);
    placeNewApiPanel(panel, panelSpec);
    return panel;
  }

  throw new Error('No supported panel creation API found.');
}

function panelSize(panelSpec) {
  const c = panelSpec.coords;
  if (panelSpec.orientation === 'vertical') {
    return { widthMm: c.z2Mm - c.z1Mm, heightMm: c.y2Mm - c.y1Mm };
  }
  if (panelSpec.orientation === 'horizont') {
    return { widthMm: c.x2Mm - c.x1Mm, heightMm: c.z2Mm - c.z1Mm };
  }
  if (panelSpec.orientation === 'front') {
    return { widthMm: c.x2Mm - c.x1Mm, heightMm: c.y2Mm - c.y1Mm };
  }
  throw new Error('Unknown panel orientation: ' + panelSpec.orientation);
}

function placeNewApiPanel(panel, panelSpec) {
  // This is intentionally conservative. Test 0/1 must verify exact positioning
  // for the installed BAZIS version before we rely on the new API path.
  const c = panelSpec.coords;
  if (panelSpec.orientation === 'vertical') {
    panel.PositionX = c.xMm;
    panel.PositionY = c.y1Mm;
    panel.PositionZ = c.z1Mm;
  } else if (panelSpec.orientation === 'horizont') {
    panel.PositionX = c.x1Mm;
    panel.PositionY = c.yMm;
    panel.PositionZ = c.z1Mm;
  } else if (panelSpec.orientation === 'front') {
    panel.PositionX = c.x1Mm;
    panel.PositionY = c.y1Mm;
    panel.PositionZ = c.zMm;
  }
}

function addPanelEdge(panel, panelSpec, edge) {
  const material = edgeMaterial(edge.material);
  const elemIndex = edgeIndex(panelSpec.orientation, edge.side);
  if (typeof panelOperations !== 'undefined' && panelOperations.AddButt) {
    panelOperations.AddButt(panel, elemIndex, material);
    return;
  }
  if (panel.AddButt) {
    setActiveEdgeMaterial(edge.material);
    panel.AddButt(null, elemIndex);
    return;
  }
  report.warnings.push('Cannot add edge for panel ' + panelSpec.id);
}

function edgeIndex(orientation, side) {
  // For a default rectangular contour, BAZIS examples iterate contour elements
  // 0..3. This mapping must be verified in Test 3 on the real installation.
  const bySide = {
    bottom: 0,
    right: 1,
    top: 2,
    left: 3,
    front: 0,
    back: 2
  };
  if (typeof bySide[side] !== 'number') {
    throw new Error('Unknown edge side "' + side + '" for orientation ' + orientation);
  }
  return bySide[side];
}

function setActivePanelMaterial(materialId) {
  const material = spec.materials.panels[materialId];
  assert(material, 'Unknown panel material: ' + materialId);
  if (typeof materialData !== 'undefined' && materialData.SetupActiveMaterial) {
    materialData.SetupActiveMaterial(material.basisName, material.thicknessMm, material.widthMm || 0);
    return;
  }
  if (typeof ActiveMaterial !== 'undefined' && ActiveMaterial.Make) {
    ActiveMaterial.Make(material.basisName, material.thicknessMm);
    return;
  }
  report.warnings.push('Cannot set active panel material: ' + materialId);
}

function setActiveEdgeMaterial(materialId) {
  const edge = spec.materials.edges[materialId];
  assert(edge, 'Unknown edge material: ' + materialId);
  if (typeof materialData !== 'undefined' && materialData.SetupActiveButtMaterial) {
    materialData.SetupActiveButtMaterial(
      edge.basisName,
      edge.thicknessMm,
      edge.widthMm || 0,
      edge.isTape !== false,
      edge.sign || '',
      edge.overhungMm || 0,
      edge.clipPanel !== false,
      edge.allowanceMm || 0
    );
  }
}

function edgeMaterial(materialId) {
  const edge = spec.materials.edges[materialId];
  assert(edge, 'Unknown edge material: ' + materialId);
  if (typeof materialData !== 'undefined' && materialData.CreateButtMaterialData) {
    return materialData.CreateButtMaterialData(
      edge.basisName,
      edge.thicknessMm,
      edge.widthMm || 0,
      edge.isTape !== false,
      edge.sign || '',
      edge.overhungMm || 0,
      edge.clipPanel !== false,
      edge.allowanceMm || 0
    );
  }
  return null;
}

function newModel() {
  if (typeof modelIOOperations !== 'undefined' && modelIOOperations.NewModel) {
    modelIOOperations.NewModel();
  } else if (typeof NewModel === 'function') {
    NewModel();
  }
}

function beginBlock(name) {
  if (typeof BeginBlock === 'function') {
    return BeginBlock(name);
  }
  if (typeof objects3d !== 'undefined' && objects3d.NewBlock) {
    return objects3d.NewBlock(name);
  }
  return null;
}

function endBlock(owner) {
  if (typeof EndBlock === 'function') EndBlock();
}

function commitChanges(name) {
  if (typeof historyOperations !== 'undefined' && historyOperations.CommitCurrentChanges) {
    historyOperations.CommitCurrentChanges(name || 'Import BasisProductionModel');
  } else if (typeof Action !== 'undefined' && Action.Commit) {
    Action.Commit();
  }
}

function saveModel(filename) {
  if (typeof modelIOOperations !== 'undefined' && modelIOOperations.SaveModelToFile) {
    modelIOOperations.SaveModelToFile(filename);
  } else if (typeof SaveModel === 'function') {
    SaveModel(filename);
  } else if (typeof Action !== 'undefined' && Action.SaveModel) {
    Action.SaveModel(filename);
  } else {
    throw new Error('No supported save model API found.');
  }
}

function writeReport(filename) {
  fs.writeFileSync(filename, JSON.stringify(report, null, 2), 'utf8');
}

function hasLegacyPanelHelpers() {
  return typeof AddVertPanel === 'function' &&
    typeof AddHorizPanel === 'function' &&
    typeof AddFrontPanel === 'function';
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

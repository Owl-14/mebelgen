#!/usr/bin/env node
// Execute the real ImportFurnitureFromJSON.js against a minimal in-memory mock.

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

function usage() {
  console.error('usage: node run_basis_import_harness.mjs <project.json> [report.json]');
  process.exit(2);
}

const fixtureArg = process.argv[2];
if (!fixtureArg) usage();
const fixture = path.resolve(fixtureArg);
const output = process.argv[3] ? path.resolve(process.argv[3]) : null;
const importer = path.resolve(path.dirname(fileURLToPath(import.meta.url)), 'ImportFurnitureFromJSON.js');
const source = fs.readFileSync(importer, 'utf8');
const project = JSON.parse(fs.readFileSync(fixture, 'utf8'));
const builtPanels = [];
const alerts = [];

const orientation = { horizont: 'horizont', vertical: 'vertical', front: 'front' };

function makePanel(width, height, orient) {
  const panel = {
    Name: '',
    Thickness: 0,
    MaterialName: '',
    GabMin: { x: 0, y: 0, z: 0 },
    GabMax: { x: 0, y: 0, z: 0 },
    Build() {
      if (orient === orientation.horizont) this.GabMax = { x: width, y: this.Thickness, z: height };
      if (orient === orientation.vertical) this.GabMax = { x: this.Thickness, y: height, z: width };
      if (orient === orientation.front) this.GabMax = { x: width, y: height, z: this.Thickness };
      builtPanels.push(this);
    },
    TranslateGCS(vector) {
      for (const axis of ['x', 'y', 'z']) {
        this.GabMin[axis] += vector[axis];
        this.GabMax[axis] += vector[axis];
      }
    },
    ToObject(vector) { return vector; },
    _requested: { width, height, orientation: orient }
  };
  return panel;
}

const sandbox = {
  JSON,
  Math,
  Array,
  String,
  Object,
  system: {
    askFileName() { return fixture; },
    readTextFile(fileName) { return fs.readFileSync(fileName, 'utf8'); }
  },
  objects3d: {
    PanelOrientation: orientation,
    NewPanel: makePanel
  },
  geometry3d: {
    VectorMake(x, y, z) { return { x, y, z }; }
  },
  Action: {},
  alert(message) { alerts.push(String(message)); }
};

vm.runInNewContext(source, sandbox, { filename: importer });

const expectedByName = new Map(project.panels.map((panel) => [panel.name, panel]));
const errors = [];
const panels = builtPanels.map((panel) => {
  const expected = expectedByName.get(panel.Name);
  if (!expected) errors.push(`${panel.Name}: unexpected panel`);
  const actual = {
    name: panel.Name,
    orientation: panel._requested.orientation,
    new_panel: { width: panel._requested.width, height: panel._requested.height, thickness: panel.Thickness },
    material_name: panel.MaterialName,
    gab_min: panel.GabMin,
    gab_max: panel.GabMax
  };
  if (expected) {
    const placement = expected.placement;
    for (const axis of ['x', 'y', 'z']) {
      if (Math.abs(actual.gab_min[axis] - placement[`${axis}1`]) > 0.001) {
        errors.push(`${panel.Name}: GabMin.${axis} mismatch`);
      }
      if (Math.abs(actual.gab_max[axis] - placement[`${axis}2`]) > 0.001) {
        errors.push(`${panel.Name}: GabMax.${axis} mismatch`);
      }
    }
  }
  return actual;
});

if (panels.length !== project.panels.length) {
  errors.push(`panel count ${panels.length} != ${project.panels.length}`);
}

const report = {
  report_version: 1,
  fixture: fixtureArg.replaceAll('\\', '/'),
  importer: 'scripts/ImportFurnitureFromJSON.js',
  status: errors.length ? 'fail' : 'pass',
  panel_count: panels.length,
  panels,
  alerts,
  errors,
  limitations: [
    'mock proves importer control flow, NewPanel arguments and final AABBs only',
    'native MatBase, edge assignment, drilling objects and .b3d require licensed BAZIS'
  ]
};
const rendered = `${JSON.stringify(report, null, 2)}\n`;
if (output) fs.writeFileSync(output, rendered, 'utf8');
else process.stdout.write(rendered);
process.exitCode = errors.length ? 1 : 0;

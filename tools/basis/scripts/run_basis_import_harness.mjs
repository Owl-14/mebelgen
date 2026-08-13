#!/usr/bin/env node
// Execute the real importer in a mock that records every supported API call.

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

function usage() {
  console.error(
    'usage: node run_basis_import_harness.mjs <project.json> [report.json] ' +
    '[--require-call-domain <panels|materials|edge_banding|drilling|native_b3d>]'
  );
  process.exit(2);
}

const argv = process.argv.slice(2);
const fixtureArg = argv.shift();
if (!fixtureArg) usage();
let outputArg = null;
if (argv[0] && !argv[0].startsWith('--')) outputArg = argv.shift();
const requiredDomains = [];
while (argv.length) {
  const flag = argv.shift();
  if (flag !== '--require-call-domain' || !argv.length) usage();
  requiredDomains.push(argv.shift());
}

const fixture = path.resolve(fixtureArg);
const output = outputArg ? path.resolve(outputArg) : null;
const importer = path.resolve(path.dirname(fileURLToPath(import.meta.url)), 'ImportFurnitureFromJSON.js');
const source = fs.readFileSync(importer, 'utf8');
const project = JSON.parse(fs.readFileSync(fixture, 'utf8'));
const builtPanels = [];
const alerts = [];
const apiCallLog = [];
let panelSequence = 0;

const orientation = { horizont: 'horizont', vertical: 'vertical', front: 'front' };

function record(api, args = {}) {
  apiCallLog.push({ sequence: apiCallLog.length + 1, api, arguments: args });
}

function displayPath(fileName) {
  return path.resolve(fileName) === fixture ? fixtureArg.replaceAll('\\', '/') : path.basename(fileName);
}

function makePanel(width, height, orient) {
  const id = `panel_${++panelSequence}`;
  record('objects3d.NewPanel', { width, height, orientation: orient, returns: id });
  const values = { Name: '', Thickness: 0, MaterialName: '' };
  const panel = {
    GabMin: { x: 0, y: 0, z: 0 },
    GabMax: { x: 0, y: 0, z: 0 },
    Build() {
      record('panel.Build', { panel: id });
      if (orient === orientation.horizont) this.GabMax = { x: width, y: values.Thickness, z: height };
      if (orient === orientation.vertical) this.GabMax = { x: values.Thickness, y: height, z: width };
      if (orient === orientation.front) this.GabMax = { x: width, y: height, z: values.Thickness };
      builtPanels.push(this);
    },
    TranslateGCS(vector) {
      record('panel.TranslateGCS', { panel: id, vector });
      for (const axis of ['x', 'y', 'z']) {
        this.GabMin[axis] += vector[axis];
        this.GabMax[axis] += vector[axis];
      }
    },
    ToObject(vector) {
      record('panel.ToObject', { panel: id, vector });
      return vector;
    },
    _id: id,
    _requested: { width, height, orientation: orient }
  };
  for (const property of ['Name', 'Thickness', 'MaterialName']) {
    Object.defineProperty(panel, property, {
      enumerable: true,
      get() { return values[property]; },
      set(value) {
        record(`panel.${property}.set`, { panel: id, value });
        values[property] = value;
      }
    });
  }
  return panel;
}

const sandbox = {
  JSON,
  Math,
  Array,
  String,
  Object,
  system: {
    askFileName(title, filter) {
      record('system.askFileName', { title, filter, returns: fixtureArg.replaceAll('\\', '/') });
      return fixture;
    },
    readTextFile(fileName) {
      record('system.readTextFile', { path: displayPath(fileName) });
      return fs.readFileSync(fileName, 'utf8');
    }
  },
  objects3d: {
    PanelOrientation: orientation,
    NewPanel: makePanel
  },
  geometry3d: {
    VectorMake(x, y, z) {
      record('geometry3d.VectorMake', { x, y, z });
      return { x, y, z };
    }
  },
  Action: {},
  alert(message) {
    const value = String(message);
    record('alert', { message: value });
    alerts.push(value);
  }
};

vm.runInNewContext(source, sandbox, { filename: importer });

const expectedByName = new Map(project.panels.map((panel) => [panel.name, panel]));
const errors = [];
const panels = builtPanels.map((panel) => {
  const expected = expectedByName.get(panel.Name);
  if (!expected) errors.push(`${panel.Name}: unexpected panel`);
  const actual = {
    mock_object: panel._id,
    name: panel.Name,
    orientation: panel._requested.orientation,
    new_panel: { width: panel._requested.width, height: panel._requested.height, thickness: panel.Thickness },
    material_name_property: panel.MaterialName,
    gab_min: panel.GabMin,
    gab_max: panel.GabMax
  };
  if (expected) {
    const placement = expected.placement;
    for (const axis of ['x', 'y', 'z']) {
      if (Math.abs(actual.gab_min[axis] - placement[`${axis}1`]) > 0.001) {
        errors.push(`${panel.Name}: mock GabMin.${axis} mismatch`);
      }
      if (Math.abs(actual.gab_max[axis] - placement[`${axis}2`]) > 0.001) {
        errors.push(`${panel.Name}: mock GabMax.${axis} mismatch`);
      }
    }
  }
  return actual;
});

if (panels.length !== project.panels.length) {
  errors.push(`mock panel count ${panels.length} != source panel count ${project.panels.length}`);
}

const domainPatterns = {
  panels: /objects3d\.NewPanel|panel\.(Build|TranslateGCS)/,
  materials: /panel\.MaterialName\.set/,
  edge_banding: /butt|edge|band/i,
  drilling: /hole|drill|bore/i,
  native_b3d: /b3d|modelIO|SaveModel/i
};
function domainEvidence(domain) {
  const pattern = domainPatterns[domain];
  const calls = apiCallLog.filter((call) => pattern.test(call.api));
  return {
    status: calls.length ? 'observed_in_mock' : 'not_observed',
    call_count: calls.length,
    call_sequences: calls.map((call) => call.sequence)
  };
}

const observedImporterCalls = Object.fromEntries(
  Object.keys(domainPatterns).map((domain) => [domain, domainEvidence(domain)])
);
for (const domain of requiredDomains) {
  if (!Object.hasOwn(observedImporterCalls, domain)) usage();
  if (observedImporterCalls[domain].call_count === 0) {
    errors.push(`required importer API call domain was not observed: ${domain}`);
  }
}

const blocked = 'blocked_not_observed';
const report = {
  report_version: 2,
  evidence_taxonomy: {
    kind: 'mock_observed_importer_calls',
    native_basis_observed: false,
    description: 'calls observed while executing the real importer against a mock; not a native import result'
  },
  fixture: fixtureArg.replaceAll('\\', '/'),
  importer: 'scripts/ImportFurnitureFromJSON.js',
  mock_execution_status: errors.length ? 'fail' : 'pass',
  acceptance_status: 'blocked_missing_licensed_basis',
  observed_importer_calls: observedImporterCalls,
  api_call_log: apiCallLog,
  mock_panel_comparison: {
    source_panel_count: project.panels.length,
    mock_built_panel_count: panels.length,
    panels
  },
  imported_result: {
    status: blocked,
    panels: blocked,
    materials: blocked,
    edge_banding: blocked,
    drilling: blocked,
    native_b3d: blocked
  },
  alerts,
  errors,
  limitations: [
    'mock calls prove JS control flow and arguments only; they are not calls inside licensed BAZIS',
    'MaterialName property assignment does not prove MatBase resolution',
    'no edge-banding, drilling or native .b3d API call is made by the current importer'
  ]
};
const rendered = `${JSON.stringify(report, null, 2)}\n`;
if (output) fs.writeFileSync(output, rendered, 'utf8');
else process.stdout.write(rendered);
process.exitCode = errors.length ? 1 : 0;

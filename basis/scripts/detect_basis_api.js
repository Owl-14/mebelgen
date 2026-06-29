// Run inside BAZIS-Mebelshchik to learn which scripting API is available.
// The script writes a JSON report next to the current model or into the scripts folder.

const fs = require('fs');

function has(name) {
  try {
    return typeof eval(name) !== 'undefined';
  } catch (error) {
    return false;
  }
}

function typeOf(name) {
  try {
    return typeof eval(name);
  } catch (error) {
    return 'missing';
  }
}

const report = {
  generatedAt: new Date().toISOString(),
  system: {
    apiVersion: has('system') ? system.apiVersion : null,
    developerApiVersion: has('system') ? system.developerApiVersion : null
  },
  modules: {
    objects3d: typeOf('objects3d'),
    materialData: typeOf('materialData'),
    panelOperations: typeOf('panelOperations'),
    fastenerOperations: typeOf('fastenerOperations'),
    modelIOOperations: typeOf('modelIOOperations'),
    historyOperations: typeOf('historyOperations'),
    UI: typeOf('UI')
  },
  legacyGlobals: {
    AddPanel: typeOf('AddPanel'),
    AddFrontPanel: typeOf('AddFrontPanel'),
    AddVertPanel: typeOf('AddVertPanel'),
    AddHorizPanel: typeOf('AddHorizPanel'),
    BeginBlock: typeOf('BeginBlock'),
    EndBlock: typeOf('EndBlock'),
    SaveModel: typeOf('SaveModel'),
    NewModel: typeOf('NewModel')
  }
};

const output = 'basis-api-report.json';
fs.writeFileSync(output, JSON.stringify(report, null, 2), 'utf8');

if (typeof alert === 'function') {
  alert('BAZIS API report written: ' + output);
} else if (typeof console !== 'undefined') {
  console.log('BAZIS API report written: ' + output);
}

/**
 * Read-only capability probe for the BAZIS desktop JavaScript runtime.
 *
 * Run this script inside the exact licensed BAZIS installation used for the
 * acceptance test. It does not create model objects or call cloud services.
 * When supported, it writes basis-api-report.json to the runtime working
 * directory; the same JSON is always shown in an alert for manual capture.
 */

function hasFunction(owner, name) {
  try {
    return !!owner && typeof owner[name] === 'function';
  } catch (e) {
    return false;
  }
}

function hasValue(owner, name) {
  try {
    return !!owner && typeof owner[name] !== 'undefined';
  } catch (e) {
    return false;
  }
}

function detectBasisApi() {
  // All probes are property reads. In particular NewPanel/NewFurnitureValue are
  // not invoked, so running detection cannot mutate the open model.
  const sys = typeof system !== 'undefined' ? system : null;
  const geometry = typeof geometry3d !== 'undefined' ? geometry3d : null;
  const objects = typeof objects3d !== 'undefined' ? objects3d : null;
  const action = typeof Action !== 'undefined' ? Action : null;
  const report = {
    report_version: 1,
    read_only: true,
    capabilities: {
      system: {
        askFileName: hasFunction(sys, 'askFileName'),
        readTextFile: hasFunction(sys, 'readTextFile'),
        writeTextFile: hasFunction(sys, 'writeTextFile')
      },
      geometry3d: {
        VectorMake: hasFunction(geometry, 'VectorMake')
      },
      objects3d: {
        NewPanel: hasFunction(objects, 'NewPanel'),
        PanelOrientation: hasValue(objects, 'PanelOrientation'),
        orientation_horizont: hasValue(objects && objects.PanelOrientation, 'horizont'),
        orientation_vertical: hasValue(objects && objects.PanelOrientation, 'vertical'),
        orientation_front: hasValue(objects && objects.PanelOrientation, 'front')
      },
      furniture: {
        Action_Properties: hasValue(action, 'Properties'),
        NewFurnitureValue:
          hasValue(action, 'Properties') &&
          hasFunction(action.Properties, 'NewFurnitureValue'),
        OpenFurniture: typeof OpenFurniture === 'function'
      }
    }
  };

  const required = [
    report.capabilities.system.askFileName,
    report.capabilities.system.readTextFile,
    report.capabilities.geometry3d.VectorMake,
    report.capabilities.objects3d.NewPanel,
    report.capabilities.objects3d.PanelOrientation,
    report.capabilities.objects3d.orientation_horizont,
    report.capabilities.objects3d.orientation_vertical,
    report.capabilities.objects3d.orientation_front
  ];
  report.importer_core_ready = required.every(function (value) { return value; });
  report.furniture_ready = report.capabilities.furniture.NewFurnitureValue;
  return report;
}

const basisApiReport = detectBasisApi();
const basisApiJson = JSON.stringify(basisApiReport, null, 2);
try {
  if (typeof system !== 'undefined' && typeof system.writeTextFile === 'function') {
    system.writeTextFile('basis-api-report.json', basisApiJson);
  }
} catch (e) {
  // The alert remains the portable evidence path when the runtime forbids writes.
}
if (typeof alert === 'function') {
  alert(basisApiJson);
}

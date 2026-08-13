# MEB-138: source expectations, mock call evidence and licensed acceptance

The artifacts use three non-interchangeable evidence classes:

1. `offline-preflight-report.json` is **source/preflight expectations** computed
   from project JSON and the Python engine. Geometry, edge data, drilling and
   CFRN parity here do not describe the JS import result.
2. `importer-harness-report.json` is **mock-observed JS calls and arguments**
   produced by executing the exact `ImportFurnitureFromJSON.js`. It proves that
   the script calls `NewPanel`, `Build`, `TranslateGCS` and assigns
   `MaterialName` in the mock. It also proves that the current script makes zero
   edge-banding, drilling and native `.b3d` API calls.
3. Native imported results remain **blocked_not_observed** until a licensed
   BAZIS run produces and inspects the model.

## Reproduce offline

From `tools/basis` with Python dependencies installed:

```bash
python scripts/preflight_basis_import.py \
  projects/cabinet_700x400x500.json \
  --output qa/basis_import/offline-preflight-report.json
node scripts/run_basis_import_harness.mjs \
  projects/cabinet_700x400x500.json \
  qa/basis_import/importer-harness-report.json
```

Both commands must exit zero. The first report records expected dimensions,
orientations, material input, source edge-band fields, Python drilling geometry,
offline CFRN encoding and a stable expected drilling fingerprint. The second
records the actual mock API call log and arguments. Neither is a native BAZIS
import result.

The negative gate is reproducible too. It must exit non-zero because the
current importer does not call any edge-banding API:

```bash
node scripts/run_basis_import_harness.mjs \
  projects/cabinet_700x400x500.json \
  --require-call-domain edge_banding
```

`fixtures.json` pins the repository project used by both reports. Reports are
committed so an auditor can diff the evidence. Re-running is deterministic; no
timestamp or machine-specific absolute path is stored.

## Licensed acceptance (still required)

1. Run `scripts/detect_basis_api.js` inside the exact licensed BAZIS version.
   Preserve `basis-api-report.json`; `importer_core_ready` must be `true`.
2. Copy `scripts/ImportFurnitureFromJSON.js` to the BAZIS scripts directory and
   import the pinned fixture.
3. Save the native `.b3d` without invoking cloud or Cutting services.
4. Record native panel AABBs/orientations and compare them with
   `source_expectations.panel_calls` and the mock call arguments.
5. Confirm each intended MatBase material and all four edge assignments per
   panel. The current importer only assigns `MaterialName`; native edge API
   support must be detected and implemented/verified before edge acceptance.
6. Implement/execute actual edge and drilling APIs before claiming those
   domains. Export/inspect native drilling and compare count, purpose summary
   and the expected SHA-256 fingerprint from the source preflight. Preserve any
   errors and the resulting `.b3d` path/hash in the task evidence.

Until those steps are performed on a real licensed installation, MEB-138 must
remain blocked or in review, never marked done solely from these offline files.

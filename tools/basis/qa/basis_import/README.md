# MEB-138: offline preflight and licensed BAZIS evidence

The offline checks intentionally stop before claiming that a native `.b3d`
exists. They exercise the production validators, CFRN encoder/hole parity and
the exact desktop importer control flow without cloud, Cutting or LLM calls.

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

Both commands must exit zero. The first report covers dimensions,
orientations, material metadata, every edge-band assignment, drilling geometry,
offline CFRN encoding and a stable drilling fingerprint. The Node harness runs
the unmodified `ImportFurnitureFromJSON.js` in a read-only mock and records the
actual `NewPanel` arguments and final panel AABBs.

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
   `importer_projection`/`importer-harness-report.json`.
5. Confirm each intended MatBase material and all four edge assignments per
   panel. The current importer only assigns `MaterialName`; native edge API
   support must be detected and implemented/verified before edge acceptance.
6. Export/inspect native drilling and compare count, purpose summary and the
   SHA-256 fingerprint from `offline-preflight-report.json`. Preserve any error
   messages and the resulting `.b3d` path/hash in the task evidence.

Until those steps are performed on a real licensed installation, MEB-138 must
remain blocked or in review, never marked done solely from these offline files.

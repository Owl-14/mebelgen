# BASIS integration notes

This folder is the first production-side research track for BAZIS-Mebelshchik.
It is separate from the visual Constante sheet renderer.

Target flow:

```text
FurnitureSpec JSON -> BasisProductionModel JSON -> BAZIS import script -> .b3d
```

## Files

- `samples/wardrobe-basic.json` - minimal `BasisProductionModel` sample.
- `scripts/detect_basis_api.js` - run inside BAZIS to detect available scripting
  modules and legacy globals.
- `scripts/import_basis_model.js` - proof-of-concept importer for rectangular
  panels, materials, edge banding, blocks, reports, and `.b3d` save.
- `apilist/check_tasks_api.mjs` - helper for public BAZIS Cloud Tasks API checks.

## Local BAZIS check

1. Open BAZIS-Mebelshchik.
2. Run `scripts/detect_basis_api.js`.
3. Save the produced `basis-api-report.json`.
4. Run `scripts/import_basis_model.js`.
5. Select `samples/wardrobe-basic.json` when prompted.
6. Open the generated `.b3d` and verify panel orientation, positions, edge
   banding, material names, and report output.

The importer intentionally stays conservative until the real installed BAZIS API
version is known. Coordinate and contour-edge mappings must be verified in the
actual production environment before this path is trusted.

## BAZIS Cloud check

The public Tasks API currently confirms listing tasks, model conversion, drawing
conversion, and result download. It does not publicly expose a confirmed
`ExecuteClientScript` task creation endpoint.

Use:

```bash
BAZIS_API_KEY=... node basis/apilist/check_tasks_api.mjs --list
BAZIS_API_KEY=... node basis/apilist/check_tasks_api.mjs --model-convert model.b3d --convert-type 0 --out result.bin
```

See `../BASIS_APILIST_RESEARCH.md` for the current evidence and open questions.

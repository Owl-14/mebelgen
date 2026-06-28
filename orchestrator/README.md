# MEBELGEN Orchestrator

This folder is the future AI control layer.

Current mode is deliberately API-free:

```text
text brief -> mock provider -> FurnitureSpec JSON -> Three.js sheet preview
```

The renderer reads:

```text
demo/three-spike/generated/latest-spec.json
```

Run:

```bash
pnpm pipeline:mock
```

Training importers:

```bash
pnpm training:kabinety
pnpm training:syktyvkar
pnpm training:all
```

`training:syktyvkar` reads the local production artifacts supplied by the user:

- `/Users/macintosheesh/Downloads/Сыктывкар 3 часть .Чертежи .zip`
- `/Users/macintosheesh/Downloads/ТЗ мебель Коми РФ (1).doc`
- `/Users/macintosheesh/Downloads/База материала.xlsx`

It extracts the order XLSX from the ZIP, filters manufactured modular furniture
from procurement-only items, maps material names to the material workbook, and
writes `demo/three-spike/generated/syktyvkar/*.json`.

Then open:

```text
http://127.0.0.1:4173/demo/three-spike/
```

Later the `gemini-provider.mjs` adapter should implement the same methods:

- `extractSpec({ text, files })`
- `reviewRender({ image, spec })`

That lets us swap the AI provider without rewriting the Three.js renderer.

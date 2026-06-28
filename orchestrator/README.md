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

Then open:

```text
http://127.0.0.1:4173/demo/three-spike/
```

Later the `gemini-provider.mjs` adapter should implement the same methods:

- `extractSpec({ text, files })`
- `reviewRender({ image, spec })`

That lets us swap the AI provider without rewriting the Three.js renderer.

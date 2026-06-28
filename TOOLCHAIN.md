# Development Toolchain

This project is moving toward a production renderer that can run outside Codex:
web UI, Telegram bot, or a backend job.

## Loaded / prepared in this workspace

- Python virtualenv `.venv` for the existing parser and SVG generator.
- Bundled Codex runtime paths for Node, Python, Poppler/PDF tools, and document
  processing helpers.
- Browser-control skill for local app verification and screenshots.
- Node REPL tool with access to bundled packages and project `node_modules`.
- Project JS dependency:
  - `three` for the upcoming technical 3D/isometry renderer spike.
- Bundled JS packages available through the workspace runtime:
  - `playwright` for browser screenshots;
  - `pixelmatch`, `pngjs`, `sharp` for visual diff / image processing.

## Skills / capabilities we will use

- Browser verification: local app testing, screenshots, visual QA.
- PDF rendering/inspection: final PDF approval sheets.
- DOCX handling: source specs and potential Word deliverables.
- GitHub research tools: repository inspection and upstream candidate analysis.
- Image generation: optional only for small visual assets/textures, not final
  client sheets.

## Near-term renderer stack

```text
FurnitureSpec JSON
  -> Three.js technical scene
  -> orthographic cameras: front / top / isometric
  -> PNG screenshots through browser automation
  -> SVG/HTML overlay: dimensions, callouts, title block
  -> final PNG/PDF/SVG approval sheet
```

## Guardrails

- Do not rely on an interactive Codex session in the production architecture.
- Do not use raw image generation for final approval sheets.
- Keep geometry and annotations deterministic.
- Use AI only before rendering, primarily for strict JSON extraction from messy
  specs and for validated CAD-code drafting experiments.

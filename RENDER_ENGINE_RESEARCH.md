# Rendering / CAD Engine Research

This document records the first GitHub + web search pass for a production
pipeline that can generate ME-RA-like furniture approval sheets without relying
on an interactive Codex session.

## Product target

Input:

- Word/PDF/spec text from a furniture client.
- Optional reference images from the spec.

Output:

- Client approval sheet: front/top views, 3D/presentation isometry, dimensions,
  callouts, materials, title block.
- Later: structured geometry/manufacturing data for Basis-Mebelshchik or another
  production pipeline.

The visual target is not pure photorealism. It is a deterministic technical
presentation sheet, close to SketchUp/Layout/CAD illustration style.

## Main conclusion

I did not find a single mature open-source project that already does our exact
job: parse arbitrary furniture specs and produce polished approval sheets.

The closest practical strategy is to assemble the product from proven building
blocks:

1. Keep our own domain model and furniture archetype library.
2. Use a deterministic renderer for drawings and annotations.
3. Use AI only for parsing/spec normalization and optional CAD-code drafting.
4. Use a real CAD/3D engine for geometry where it improves quality, but keep the
   sheet composition deterministic.

## Candidate categories

### 1. SketchUp + OpenCutList style pipeline

Relevant repository:

- https://github.com/lairdubois/lairdubois-opencutlist-sketchup-extension

Why it matters:

- OpenCutList is a real woodworking/SketchUp extension: parts lists, cutting
  diagrams, nesting, labels, estimates, exploded views.
- The provided reference sheets visually resemble a SketchUp/Layout workflow:
  simple shaded 3D, orthographic views, dimensions, annotations, title block.

Strengths:

- Closest visual/manual workflow to the reference examples.
- Real furniture/woodworking ecosystem.
- Good fit for cut lists and later production thinking.

Risks:

- SketchUp is proprietary desktop software.
- Automation is awkward for a web/Telegram product.
- OpenCutList is GPL-3.0; integrating/distributing modified code requires GPL
  compliance.
- Great for production shop workflow, less ideal as the server-side engine for
  our product.

Verdict:

- Study as a workflow reference.
- Do not make it the core of our automated product unless the client accepts a
  desktop SketchUp-based workflow.

### 2. FreeCAD furniture workbenches

Relevant repositories:

- https://github.com/yelloish6/AIGenFurniture-freecad-workbench
- https://github.com/foreachidea/Cubinets

Why they matter:

- They are specifically about parametric cabinet/furniture generation.
- Cubinets uses parametric FreeCAD templates and generates cut lists.
- AIGenFurniture has cabinet architectures, features, manufacturing export code,
  JSON export, STL/export/manufacturing modules.

Strengths:

- Real CAD kernel path.
- Better long-term bridge toward manufacturing.
- Can produce actual geometry and part lists instead of only pretty drawings.

Risks:

- Heavy FreeCAD dependency.
- UI/workbench orientation, not headless web-first.
- Visual approval sheet quality is not solved by these projects.
- Cubinets is GPL-3.0. AIGenFurniture is LGPL-2.1+.
- FreeCAD is not currently installed on this machine, so this pass was code
  inspection only.

Verdict:

- High-value research path for the second stage: manufacturing geometry.
- Not the fastest route to beautiful ME-RA-like client sheets.
- Worth a separate spike: install FreeCAD headless, generate one cabinet, export
  SVG/PNG/STEP/cutlist, measure automation pain.

### 3. CadQuery / Build123d / OpenCascade code-first CAD

Relevant repository:

- https://github.com/cadquery/cadquery

Why it matters:

- Python code-first parametric CAD.
- Good for deterministic furniture primitives and later manufacturing geometry.
- Easier to run server-side than FreeCAD GUI workflows.

Strengths:

- Python-native, testable, scriptable.
- Can export real CAD formats through OpenCascade.
- Good match for LLM-assisted code generation with validation.

Risks:

- Does not provide beautiful approval-sheet rendering by itself.
- We would still need a viewer/sheet composer.
- More engineering work than using a ready furniture workbench.

Verdict:

- Strong candidate for the internal geometry kernel if Three.js geometry becomes
  too weak.
- Pair with Three.js/HTML/SVG for visual sheets.

### 4. CADAM / OpenSCAD text-to-CAD

Relevant repository:

- https://github.com/Adam-CAD/CADAM

What it is:

- An open-source text-to-CAD web app.
- Uses React/TypeScript, Three.js/React Three Fiber, OpenSCAD WASM, AI SDKs, and
  exports STL/SCAD/DXF.

Strengths:

- Best found example of a modern AI-to-parametric-CAD app.
- Browser-based CAD compilation through OpenSCAD WASM is very relevant.
- Architecture is useful: AI produces code, compiler validates, UI exposes
  parameters, Three.js previews geometry.

Risks:

- GPL-3.0 license.
- Complex app with Supabase/billing/auth/provider integrations.
- General-purpose text-to-CAD, not furniture sheets.
- OpenSCAD is not ideal for polished presentation drawings or furniture-specific
  BOM/cutlist workflows.

Verdict:

- Very useful as architecture inspiration.
- Avoid copying code unless we are comfortable with GPL obligations.
- The pattern is the lesson: AI -> parametric code -> deterministic compiler ->
  preview/export.

### 5. Three.js / React Three Fiber configurators

Relevant repositories:

- https://github.com/karlosmatos/wardrobe-3d-designer
- https://github.com/furnishup/blueprint3d
- https://github.com/charmlinn/blueprint3d-modern
- https://github.com/CodeHole7/threejs-3d-room-designer
- https://github.com/mr-akashdesai/kitchen-kreation

Why they matter:

- These show product/room configurator patterns: state model, dimensions,
  materials, real-time preview, save/load JSON.
- Wardrobe 3D Designer is especially close in UI/data shape: wardrobe dimensions,
  components, shelves, drawers, rails, dividers, materials.

Strengths:

- Web-first and productizable.
- Good fit for a future standalone web UI or Telegram bot backend.
- Easy to generate multiple camera views and screenshots.
- We can overlay precise SVG dimensions and title blocks.
- MIT examples exist, including Wardrobe 3D Designer.

Risks:

- Most demos use simplistic geometry or ready-made assets.
- They do not solve technical drawings, sheet layout, or manufacturing export.
- We still need our own furniture archetype and drawing rules.

Verdict:

- Best candidate for the approval-sheet visual engine.
- Use Three.js/R3F for 3D views and SVG/HTML for dimensions/sheet composition.

### 6. AI / neural CAD research

Relevant links:

- https://github.com/SadilKhan/Text2CAD
- https://github.com/rundiwu/DeepCAD
- https://github.com/filaPro/cad-recode
- https://github.com/Text-to-CadQuery

What these do:

- Text2CAD / DeepCAD explore text-to-parametric CAD sequence generation.
- CAD-Recode converts point clouds into CadQuery code.
- Text-to-CadQuery explores generating CadQuery code directly from text.

Strengths:

- Useful research direction for future automation.
- Confirms the general industry trend: generate executable CAD/code, not pixels.

Risks:

- Research-grade, not furniture-business production tools.
- Usually trained on mechanical CAD datasets, not custom cabinetry.
- Not enough for dimensions, material callouts, client sheets, or manufacturing
  rules without a deterministic system around it.

Verdict:

- Do not base MVP on these models.
- Use LLMs pragmatically: parse specs, propose structured JSON, maybe draft
  CadQuery/OpenSCAD snippets that are validated by tests.

## Recommended production architecture

```text
Word/PDF/images
  -> parser + LLM normalizer
  -> FurnitureSpec JSON
  -> deterministic archetype engine
  -> renderer:
       - 2D orthographic SVG views
       - Three.js/R3F technical isometry screenshots
       - SVG/HTML dimensions + callouts + title block
  -> PNG/PDF/SVG approval sheet
  -> later: CadQuery/FreeCAD/Basis export
```

This keeps the business-critical output deterministic while still allowing AI to
help with messy input.

## Suggested next spikes

### Spike A: Three.js technical renderer

Build a minimal renderer outside the current hand-written SVG isometry:

- React Three Fiber or plain Three.js.
- One desk, one cabinet, one wardrobe.
- Orthographic camera.
- Toon/technical materials, outlines, ambient occlusion/contact shadow.
- View presets: front, top, isometric.
- Browser screenshots via Playwright.
- SVG overlay for dimensions and title block.

Success criterion:

- The desk isometry must look closer to the reference than the current SVG-only
  prototype.

### Spike B: FreeCAD/CadQuery geometry export

Try one deterministic cabinet model in a CAD kernel:

- Generate box panels/shelves/doors from JSON.
- Export STEP/STL/DXF or SVG.
- Extract cut list.
- Measure installation and headless automation cost.

Success criterion:

- We know whether CAD kernel output can later bridge to production/Basis.

### Spike C: AI normalization

Create a strict schema for furniture specs and use an LLM only to fill it:

- dimensions;
- material/color;
- archetype;
- features;
- hardware;
- notes;
- uncertainty flags.

Success criterion:

- The renderer never reads raw prompt text. It only reads validated JSON.

## Tools/skills useful for our development workflow

- Browser/Playwright visual verification: mandatory for screenshot regression.
- Image comparison: compare generated sheets against reference sheets.
- PDF/DOCX parsing: required for specs from clients.
- LLM structured extraction: useful for messy Russian specs, but must be schema
  validated.
- Image generation: optional only for textures/assets, not for final sheets.
- CAD/code generation: optional future path through CadQuery/OpenSCAD, always
  compiler-validated.

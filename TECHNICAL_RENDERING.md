# Technical Rendering Direction

Goal: generate client approval sheets from furniture specs, matching the ME-RA
reference style: orthographic views, presentation isometry, dimensions, material
notes, and title block.

## Current hypothesis

The reference sheets are not a single AI-generated image and are not simply one
photorealistic 3D model rendered from different cameras. They look like a
composited technical sheet:

1. Parse the Word spec into structured furniture data.
2. Classify the item into an archetype.
3. Render separate view-specific drawings:
   - front/top orthographic views with exact dimensions;
   - presentation isometry with simplified, readable hardware;
   - per-view symbols for details such as cable channels, PC holders, hinges.
4. Compose an A4 sheet with material notes and the ME-RA title block.
5. Export SVG first, then PNG/PDF for delivery.

## Why this direction

- Exact sizes and text must remain deterministic.
- Front and isometric views are allowed to differ in level of detail.
- Hardware can be represented with reusable 2D/2.5D symbols instead of a heavy
  photoreal model.
- The same structured model can later feed a Basis-Mebelshchik automation layer.

## Prototype status

`python -m mebelgen ... --style technical` enables the experimental renderer.
It currently contains a tuned `desk_panel` renderer for the working desk sheet
and falls back to the classic renderer for other archetypes.

The desk renderer is pure SVG:

- no Blender dependency;
- separate front and isometric drawings;
- custom symbols for flexible cable channel and system-unit holder;
- ME-RA-like layout, dimensions, material block, and title block.

## Next implementation steps

1. Tune the desk sheet against the provided reference image.
2. Add technical renderers for:
   - `drawer_unit`;
   - `wardrobe` / `cabinet`;
   - `coffee_rect`;
   - `coffee_round`;
   - `coffee_fluted`.
3. Add regression fixtures for parsed specs and generated SVG structure.
4. Add a production PNG/PDF export path that does not depend on Rust builds in
   non-ASCII project paths.
5. Wrap the CLI in a small web or Telegram-bot interface after rendering quality
   is acceptable.

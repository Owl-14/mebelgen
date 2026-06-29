"""Global configuration: palette, fonts, A4 sheet geometry, brand, RAL/NCS maps."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Sheet geometry (A4 portrait), in SVG user units == px at 96 dpi-ish.
# We use a generous canvas so text stays crisp; everything scales together.
# ---------------------------------------------------------------------------
SHEET_W = 794          # ~A4 width  (210mm @ 96dpi)
SHEET_H = 1123         # ~A4 height (297mm @ 96dpi)
SHEET_MARGIN = 18      # outer frame margin

# ---------------------------------------------------------------------------
# Palette - tuned to the ME-RA reference look.
# ---------------------------------------------------------------------------
COL_PAPER = "#ffffff"
COL_FRAME = "#111111"
COL_INK = "#1a1a1a"          # primary line / text
COL_INK_SOFT = "#444444"     # secondary text
COL_DIM = "#222222"          # dimension lines / text

# Furniture material shading (warm off-white / cream like the samples)
COL_MAT_TOP = "#f3efe6"      # top faces (lightest)
COL_MAT_FRONT = "#e9e4d7"    # front faces
COL_MAT_SIDE = "#dcd6c6"     # side faces (darkest)
COL_MAT_LINE = "#9a9382"     # furniture outline
COL_MAT_LINE_SOFT = "#bdb6a4"

# Brass / latun accents
COL_BRASS = "#c8a13c"
COL_BRASS_LIGHT = "#e3c878"
COL_BRASS_DARK = "#9c7a26"

# Dark plinth / black hardware
COL_DARK = "#2c2c2c"
COL_BLACK = "#111111"

# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------
FONT_SANS = "Arial, 'Helvetica Neue', Helvetica, sans-serif"
FONT_SERIF = "Georgia, 'Times New Roman', serif"

FS_TITLE = 13          # view labels (ВИД СПЕРЕДИ ...)
FS_DIM = 12            # dimension numbers
FS_CALLOUT = 11        # callout text
FS_BODY = 12           # materials block body
FS_BRAND = 30          # ME-RA logo
FS_SLOGAN = 11

# ---------------------------------------------------------------------------
# Brand / title block
# ---------------------------------------------------------------------------
BRAND_NAME = "ME-RA"
BRAND_SLOGAN = "мебель.пространство.качество"
DEFAULT_DATE = "28.05.2026"
DEFAULT_MANAGER = "Петрова А.А."

# ---------------------------------------------------------------------------
# Drawing constants
# ---------------------------------------------------------------------------
ARROW = 6              # dimension arrow size
EXT_GAP = 4            # gap between object and extension line start
EXT_OVER = 6          # extension line overshoot beyond dimension line
ISO_COS = 0.86602540378  # cos(30)
ISO_SIN = 0.5            # sin(30)

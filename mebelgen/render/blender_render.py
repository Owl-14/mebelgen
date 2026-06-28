"""Drive Blender (headless) to render photoreal 3D heroes for items.

Locates a Blender executable, writes an augmented specs JSON (with output
slugs), and runs ``blender/build_scene.py`` once for all items.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional

_SCENE = os.path.join(os.path.dirname(__file__), "blender", "build_scene.py")


def find_blender() -> Optional[str]:
    env = os.environ.get("MEBELGEN_BLENDER")
    if env and os.path.exists(env):
        return env
    cand = shutil.which("blender")
    if cand:
        return cand
    patterns = [
        r"D:\tools\blender*\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender*\blender.exe",
        r"C:\Program Files (x86)\Blender Foundation\Blender*\blender.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Blender Foundation\Blender*\blender.exe"),
        "/Applications/Blender.app/Contents/MacOS/Blender",
        "/usr/bin/blender",
    ]
    for pat in patterns:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def render_all(specs: List[dict], out_dir: str,
               blender: Optional[str] = None,
               timeout: int = 1800) -> Dict[str, str]:
    """Render every spec; returns {slug: png_path} for successful renders."""
    blender = blender or find_blender()
    if not blender:
        raise RuntimeError(
            "Blender не найден. Установите его или задайте MEBELGEN_BLENDER."
        )
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    specs_path = os.path.join(out_dir, "_render_specs.json")
    with open(specs_path, "w", encoding="utf-8") as fh:
        json.dump(specs, fh, ensure_ascii=False)

    cmd = [blender, "--background", "--factory-startup", "--python", _SCENE,
           "--", specs_path, out_dir]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    sys.stdout.write(proc.stdout[-4000:] if proc.stdout else "")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-4000:] if proc.stderr else "")

    result: Dict[str, str] = {}
    for spec in specs:
        slug = spec.get("_slug")
        png = os.path.join(out_dir, slug + ".png")
        if slug and os.path.exists(png):
            result[slug] = png
    return result

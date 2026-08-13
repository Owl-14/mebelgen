"""Capture MEB-094 review CSS on the real local Studio page.

The styles are injected by Playwright after the page has loaded.  Nothing in
the production Studio imports these files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
REVIEW = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.studio import _Studio, make_handler  # noqa: E402


DIRECTIONS = {
    "a": ("direction-a-precision-light.css", "#1f5faf"),
    "b": ("direction-b-warm-workshop.css", "#8a4b16"),
    "c": ("direction-c-blueprint.css", "#0b5d78"),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()


def _head_sha256(path: Path) -> str:
    repo_root = Path(_git("rev-parse", "--show-toplevel"))
    relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
    content = subprocess.check_output(
        ["git", "cat-file", "blob", f"HEAD:{relative}"], cwd=ROOT
    )
    return hashlib.sha256(content).hexdigest()


def _listen_for_diagnostics(page: Page, diagnostics: dict[str, list[str]]) -> None:
    def on_console(message: object) -> None:
        message_type = getattr(message, "type", "")
        text = getattr(message, "text", str(message))
        if message_type == "error":
            diagnostics["console_errors"].append(text)
        elif message_type == "warning":
            diagnostics["console_warnings"].append(text)

    page.on("console", on_console)
    page.on(
        "pageerror",
        lambda error: diagnostics["page_errors"].append(str(error)),
    )


def _inject(page: Page, key: str, css_path: Path, expected_accent: str) -> dict[str, str]:
    page.add_style_tag(path=str(css_path))
    page.evaluate(
        "key => document.documentElement.setAttribute('data-meb094-direction', key)",
        key,
    )
    applied = page.evaluate(
        """() => ({
            direction: document.documentElement.dataset.meb094Direction,
            accent: getComputedStyle(document.documentElement)
                .getPropertyValue('--accent').trim().toLowerCase(),
            workspaceBackground: getComputedStyle(document.querySelector('#main')).backgroundColor,
        })"""
    )
    assert applied["direction"] == key
    assert applied["accent"] == expected_accent
    return applied


def _motion_probe(page: Page) -> dict[str, object]:
    return page.evaluate(
        """() => {
            const toMs = value => Math.max(...value.split(',').map(raw => {
                const token = raw.trim();
                return token.endsWith('ms') ? parseFloat(token) : parseFloat(token) * 1000;
            }));
            const samples = [...document.querySelectorAll('*')].map(element => {
                const style = getComputedStyle(element);
                return {
                    tag: element.tagName.toLowerCase(),
                    id: element.id,
                    animationMs: toMs(style.animationDuration),
                    transitionMs: toMs(style.transitionDuration),
                };
            });
            return {
                mediaMatches: matchMedia('(prefers-reduced-motion: reduce)').matches,
                maxAnimationMs: Math.max(...samples.map(item => item.animationMs)),
                maxTransitionMs: Math.max(...samples.map(item => item.transitionMs)),
                offenders: samples.filter(item =>
                    item.animationMs > 0.011 || item.transitionMs > 0.011
                ),
            };
        }"""
    )


def capture(output_dir: Path, base_sha: str) -> dict[str, object]:
    merge_base = _git("merge-base", "HEAD", "origin/master")
    if merge_base != base_sha:
        raise RuntimeError(f"merge-base {merge_base} is not requested base {base_sha}")

    output_dir.mkdir(parents=True, exist_ok=True)
    source = ROOT / "paramspecs" / "komi_72_tumba_podkatnaya.json"
    evidence: dict[str, object] = {
        "base_sha": base_sha,
        "source": "paramspecs/komi_72_tumba_podkatnaya.json",
        "viewport": {"width": 1440, "height": 900},
        "capture": "real local Studio with Playwright-injected review CSS",
        "directions": {},
    }

    with tempfile.TemporaryDirectory(prefix="meb094-live-") as temp_name:
        workspace = Path(temp_name)
        spec_path = workspace / "current.json"
        spec_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        out_dir = workspace / "out"
        out_dir.mkdir()
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(_Studio(spec_path, out_dir)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    args=["--enable-unsafe-swiftshader", "--use-angle=swiftshader"],
                )
                normal = browser.new_context(viewport={"width": 1440, "height": 900})
                baseline = normal.new_page()
                baseline_diagnostics = {
                    "console_errors": [], "console_warnings": [], "page_errors": []
                }
                _listen_for_diagnostics(baseline, baseline_diagnostics)
                baseline.goto(url, wait_until="domcontentloaded")
                baseline.wait_for_function("() => viewportModelState === 'ready'")
                baseline_path = output_dir / "origin-master-1440x900.jpg"
                baseline.screenshot(path=str(baseline_path), type="jpeg", quality=90)
                evidence["baseline_sha256"] = _sha256(baseline_path)
                evidence["baseline_diagnostics"] = baseline_diagnostics
                normal.close()

                for key, (css_name, accent) in DIRECTIONS.items():
                    diagnostics: dict[str, list[str]] = {
                        "console_errors": [], "console_warnings": [], "page_errors": []
                    }
                    context = browser.new_context(viewport={"width": 1440, "height": 900})
                    page = context.new_page()
                    _listen_for_diagnostics(page, diagnostics)
                    page.goto(url, wait_until="domcontentloaded")
                    page.wait_for_function("() => viewportModelState === 'ready'")
                    css_path = REVIEW / css_name
                    applied = _inject(page, key, css_path, accent)
                    image_path = output_dir / f"direction-{key}-1440x900.jpg"
                    page.screenshot(path=str(image_path), type="jpeg", quality=90)
                    context.close()

                    reduced = browser.new_context(
                        viewport={"width": 1440, "height": 900}, reduced_motion="reduce"
                    )
                    reduce_page = reduced.new_page()
                    _listen_for_diagnostics(reduce_page, diagnostics)
                    reduce_page.goto(url, wait_until="domcontentloaded")
                    reduce_page.wait_for_function("() => viewportModelState === 'ready'")
                    _inject(reduce_page, key, css_path, accent)
                    motion = _motion_probe(reduce_page)
                    reduced.close()
                    assert motion["mediaMatches"] is True
                    assert motion["offenders"] == []

                    evidence["directions"][key] = {
                        "css": css_name,
                        "css_sha256": _head_sha256(css_path),
                        "screenshot": image_path.name,
                        "screenshot_sha256": _sha256(image_path),
                        "applied": applied,
                        **diagnostics,
                        "reduced_motion": motion,
                    }
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    evidence_path = output_dir / "browser-evidence.json"
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=REVIEW)
    parser.add_argument("--base-sha", default=_git("rev-parse", "origin/master"))
    args = parser.parse_args()
    capture(args.output_dir.resolve(), args.base_sha)

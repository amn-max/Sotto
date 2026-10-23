"""Renders demo.html into assets/demo.gif: steps the page's clock frame by frame in Chrome, then
encodes the frames with ffmpeg.

    uv run --with playwright python assets/demo/render.py
    uv run --with playwright python assets/demo/render.py --preview 6.5 13 out-dir
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "demo.gif"
FPS = 12
SECONDS = 18.5
WIDTH = 1200


def open_page(playwright):
    browser = playwright.chromium.launch(channel="chrome")
    page = browser.new_page(viewport={"width": 1440, "height": 810})
    page.goto((HERE / "demo.html").as_uri())
    page.wait_for_function("window.ready === true")
    page.evaluate("document.fonts.ready.then(() => true)")
    return browser, page


def preview(times, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser, page = open_page(playwright)
        for t in times:
            page.evaluate(f"render({t})")
            page.screenshot(path=str(out / f"frame-{t}.png"))
        browser.close()


def render():
    ffmpeg = shutil.which("ffmpeg") or sys.exit("ffmpeg is needed on PATH")
    with tempfile.TemporaryDirectory() as frames, sync_playwright() as playwright:
        browser, page = open_page(playwright)
        for i in range(int(SECONDS * FPS)):
            page.evaluate(f"render({i / FPS})")
            page.screenshot(path=f"{frames}/{i:04d}.png")
        browser.close()
        palette = (
            f"scale={WIDTH}:-1:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=192:stats_mode=diff[p];"
            "[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle"
        )
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", f"{frames}/%04d.png",
             "-vf", palette, "-loop", "0", str(OUT)],
            check=True,
        )
    print(f"{OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--preview":
        *times, out_dir = sys.argv[2:]
        preview([float(t) for t in times], out_dir)
    else:
        render()

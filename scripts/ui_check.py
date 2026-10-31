"""Playwright headless Chromium UI verification & screenshot script (Section 11.4)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_ROOT.parent))


def _find_free_port(default: int = 8018) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", default)) != 0:
            return default
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> int:
    port = _find_free_port(8018)
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["MOCK_LLM"] = "1"

    print(f"Starting TowerTwin server on {base_url} for Playwright UI check...", flush=True)
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "towertwin"],
        cwd=str(_ROOT.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        t0 = time.perf_counter()
        ready = False
        while time.perf_counter() - t0 < 40.0:
            try:
                r = httpx.get(f"{base_url}/api/config", timeout=2.0)
                if r.status_code == 200:
                    ready = True
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.25)

        if not ready:
            print("ERROR: Server did not become ready within 40s.", flush=True)
            return 1

        out_png = _ROOT / "ui_check.png"
        with sync_playwright() as p:
            browser = None
            webgl_args = ["--enable-webgl", "--ignore-gpu-blocklist", "--use-gl=angle"]
            for chan in (None, "msedge", "chrome"):
                try:
                    if chan is None:
                        browser = p.chromium.launch(headless=True, args=webgl_args)
                    else:
                        browser = p.chromium.launch(headless=True, channel=chan, args=webgl_args)
                    break
                except Exception:  # noqa: BLE001
                    continue
            if browser is None:
                raise RuntimeError("Could not launch Chromium, Edge, or Chrome via Playwright.")
            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = context.new_page()

            console_errors: list[str] = []
            page.on(
                "console",
                lambda msg: console_errors.append(f"[{msg.type}] {msg.text}")
                if msg.type == "error"
                else None,
            )
            page.on("pageerror", lambda exc: console_errors.append(f"[pageerror] {exc}"))
            page.on(
                "response",
                lambda res: console_errors.append(f"[http-{res.status}] {res.url}")
                if res.status >= 400
                else None,
            )

            page.goto(base_url, wait_until="domcontentloaded")
            page.wait_for_selector("#limit-slider", timeout=10000)
            page.wait_for_timeout(500)

            # 1. Set slider to 60 lots
            page.eval_on_selector(
                "#limit-slider",
                """(el) => {
                    el.value = '60';
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
            )

            # 2. Click Run Repair
            page.click("#btn-run")

            # 3. Wait for sign-off modal to open (meaning loop + sweep + holdout verification completed)
            page.wait_for_selector("#signoff-modal:not(.hidden)", timeout=90000)

            # 4. Assert all 7 check pills are green (.check-pill.pass)
            green_pills = page.locator(".check-pill.pass").count()
            print(f"Green check pills count at sign-off: {green_pills}/7", flush=True)
            if green_pills != 7:
                print(f"ERROR: Expected 7 green check pills, got {green_pills}", flush=True)
                return 1

            # 5. Click Approve inside the sign-off modal
            page.click("#btn-modal-approve")
            page.wait_for_selector("#signoff-modal.hidden", state="attached", timeout=10000)
            page.wait_for_timeout(300)

            # 6. Trigger a flash crash and wait briefly for crash + circuit breaker halt banner
            page.click("#btn-crash")
            page.wait_for_selector("#twin-halted-banner:not(.hidden)", timeout=10000)
            page.wait_for_timeout(400)

            # 7. Trigger Kill Switch and verify KILLED BY HUMAN banner/status
            page.click("#btn-kill")
            page.wait_for_selector("#twin-killed-banner:not(.hidden)", timeout=10000)
            page.wait_for_timeout(300)

            # 8. Save 1920x1080 screenshot to towertwin/ui_check.png
            page.screenshot(path=str(out_png), full_page=False)
            print(f"Saved 1920x1080 screenshot to {out_png}", flush=True)

            # 9. Also check 1366x768 viewport
            out_png_1366 = _ROOT / "ui_check_1366x768.png"
            page.set_viewport_size({"width": 1366, "height": 768})
            page.wait_for_timeout(400)
            page.screenshot(path=str(out_png_1366), full_page=False)
            print(f"Saved 1366x768 screenshot to {out_png_1366}", flush=True)

            print(f"Browser console errors ({len(console_errors)}): {console_errors}", flush=True)

            browser.close()
        return 0

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5.0)
        except Exception:  # noqa: BLE001
            server_proc.kill()


if __name__ == "__main__":
    sys.exit(main())

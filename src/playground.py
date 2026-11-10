import argparse
import asyncio
import io
from pathlib import Path
import shutil
import sys
import urllib.request
import zipfile

from typing import Optional

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

# Ensure src/ is in sys.path
SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

EXTENSION_DIR_CHROME = PROJECT_ROOT / "extensions" / "ubolite"
EXTENSION_DIR_FIREFOX = PROJECT_ROOT / "extensions" / "firefox"
PROFILE_DIR_CHROME = PROJECT_ROOT / ".chrome_profile"
PROFILE_DIR_FIREFOX = PROJECT_ROOT / ".firefox_profile"

DEFAULT_URL = "https://rdaily.live/sports/football"
UBOLITE_RELEASE_URL = "https://github.com/uBlockOrigin/uBOL-home/releases/download/2026.1103.1743/uBOLite_2026.1103.1743.chromium.zip"
FIREFOX_UBLOCK_RELEASE_URL = "https://github.com/gorhill/uBlock/releases/download/1.76.0/uBlock0_1.76.0.firefox.signed.xpi"


def ensure_ubol_installed() -> Path:
    """Ensures uBlock Origin Lite (MV3) is downloaded and ready for Chromium."""
    if (EXTENSION_DIR_CHROME / "manifest.json").exists():
        return EXTENSION_DIR_CHROME

    print("📦 uBlock Origin Lite not found locally. Downloading official Chromium release...")
    EXTENSION_DIR_CHROME.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(
            UBOLITE_RELEASE_URL,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(EXTENSION_DIR_CHROME)
        print("✅ uBlock Origin Lite downloaded and unpacked.")
    except Exception as e:
        print(f"⚠️ Warning: Could not automatically download uBlock Origin Lite: {e}")

    return EXTENSION_DIR_CHROME


def ensure_firefox_ublock_installed() -> Path:
    """Ensures uBlock Origin signed XPI is downloaded and ready for Firefox."""
    EXTENSION_DIR_FIREFOX.mkdir(parents=True, exist_ok=True)
    target_xpi = EXTENSION_DIR_FIREFOX / "uBlock0@raymondhill.net.xpi"
    if target_xpi.exists():
        return target_xpi

    print("📦 uBlock Origin for Firefox not found locally. Downloading official signed release...")
    try:
        req = urllib.request.Request(
            FIREFOX_UBLOCK_RELEASE_URL,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
        with open(target_xpi, "wb") as f:
            f.write(data)
        print("✅ uBlock Origin for Firefox downloaded.")
    except Exception as e:
        print(f"⚠️ Warning: Could not download uBlock Origin for Firefox: {e}")

    return target_xpi


async def reveal_window(context: BrowserContext, page: Optional[Page] = None):
    """Brings an offscreen/hidden browser window onto the main screen and focuses it."""
    target_page = page if page else (context.pages[0] if context.pages else None)
    if target_page:
        try:
            client = await context.new_cdp_session(target_page)
            window_info = await client.send("Browser.getWindowForTarget")
            window_id = window_info["windowId"]
            await client.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window_id,
                    "bounds": {
                        "windowState": "normal",
                        "left": 100,
                        "top": 100,
                        "width": 1360,
                        "height": 860,
                    },
                },
            )
            await target_page.bring_to_front()
            return
        except Exception:
            pass

    if page:
        try:
            await page.bring_to_front()
        except Exception:
            pass


async def launch_browser_context(
    p: Playwright,
    browser_type: str = "chromium",
    use_adblock: bool = True,
    headless: bool = False,
    start_hidden: bool = False,
) -> tuple[BrowserContext, Page]:
    """Launches a persistent browser context for Chromium or Firefox with uBlock Origin."""
    browser_type = browser_type.lower()

    if browser_type == "chromium":
        args = [
            "--disable-blink-features=AutomationControlled",
            "--autoplay-policy=no-user-gesture-required",
        ]

        if start_hidden and not headless:
            # Place window offscreen so it stays completely hidden until reveal_window is called
            args.extend(["--window-position=5000,5000", "--window-size=1280,800"])
        else:
            args.append("--start-maximized")

        if use_adblock:
            ext_path = ensure_ubol_installed().resolve()
            if (ext_path / "manifest.json").exists():
                args.extend([
                    f"--disable-extensions-except={ext_path}",
                    f"--load-extension={ext_path}",
                ])

        PROFILE_DIR_CHROME.mkdir(parents=True, exist_ok=True)
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR_CHROME.resolve()),
            headless=headless,
            args=args,
            viewport=None,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        return context, page

    elif browser_type == "firefox":
        PROFILE_DIR_FIREFOX.mkdir(parents=True, exist_ok=True)
        firefox_prefs = {
            "extensions.autoDisableScopes": 0,
            "extensions.enabledScopes": 15,
        }

        if use_adblock:
            xpi_path = ensure_firefox_ublock_installed().resolve()
            ext_target = PROFILE_DIR_FIREFOX / "extensions"
            ext_target.mkdir(parents=True, exist_ok=True)

            target_xpi = ext_target / "uBlock0@raymondhill.net.xpi"
            if xpi_path.exists() and not target_xpi.exists():
                shutil.copy(xpi_path, target_xpi)

            target_unpacked = ext_target / "uBlock0@raymondhill.net"
            if xpi_path.exists() and not target_unpacked.exists():
                try:
                    with zipfile.ZipFile(xpi_path, "r") as zf:
                        zf.extractall(target_unpacked)
                except Exception:
                    pass

        context = await p.firefox.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR_FIREFOX.resolve()),
            headless=headless,
            firefox_user_prefs=firefox_prefs,
            viewport={"width": 1400, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        return context, page

    else:
        raise ValueError(f"Unsupported browser engine: {browser_type}. Use 'chromium' or 'firefox'.")


async def launch_playground(url: str, browser_type: str, disable_adblock: bool):
    browser_type = browser_type.lower()

    print("\n" + "=" * 65)
    print(" 🎮 Playwright Interactive Playground")
    print(f" URL:        {url}")
    print(f" Browser:    {browser_type.upper()}")
    print(
        f" Adblocker:  {'Disabled' if disable_adblock else f'uBlock Origin ({browser_type.capitalize()} 🛡️)'}"
    )
    print("=" * 65 + "\n")

    async with async_playwright() as p:
        context, page = await launch_browser_context(
            p=p,
            browser_type=browser_type,
            use_adblock=not disable_adblock,
            headless=False,
        )

        print(f"\n🌐 Navigating to {url}...")
        await page.goto(url, wait_until="domcontentloaded")

        print("\n" + "-" * 65)
        print(" 👉 The browser is now open and fully interactive.")
        print(" 👉 Feel free to click matches, scroll, and test.")
        print(" 👉 When finished, press ENTER in this terminal to exit.")
        print("-" * 65 + "\n")

        await asyncio.to_thread(input, "Press ENTER to close browser and exit... ")

        print("\nShutting down browser...")
        await context.close()
        print("Done.")


def main():
    parser = argparse.ArgumentParser(
        description="Launch an interactive browser playground for rdaily.live."
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=DEFAULT_URL,
        help=f"Target URL to open (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--browser",
        "-b",
        choices=["chromium", "firefox"],
        default="chromium",
        help="Browser engine to run (default: chromium).",
    )
    parser.add_argument(
        "--no-adblock",
        action="store_true",
        help="Disable uBlock Origin.",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            launch_playground(
                url=args.url,
                browser_type=args.browser,
                disable_adblock=args.no_adblock,
            )
        )
    except KeyboardInterrupt:
        print("\nPlayground session closed.")
        sys.exit(0)


if __name__ == "__main__":
    main()

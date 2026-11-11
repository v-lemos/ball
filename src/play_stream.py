import asyncio
import logging
from pathlib import Path
import sys
from typing import List, Optional

from playwright.async_api import BrowserContext, Page, async_playwright

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cli_args import create_parser
from playground import launch_browser_context

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("stream_player")


async def extract_jwplayer_source(page: Page) -> Optional[str]:
    """Extracts the current stream URL from JW Player's JavaScript API."""
    try:
        return await page.evaluate("""() => {
            try {
                if (window.jwplayer) {
                    const item = window.jwplayer().getPlaylistItem();
                    if (item && item.file) return item.file;
                    if (item && item.sources && item.sources.length) return item.sources[0].file;
                }
            } catch(e) {}
            return null;
        }""")
    except Exception:
        return None


async def auto_play_jwplayer(page: Page, timeout_ms: int = 15000) -> bool:
    """Detects and activates playback on the player (checking main page and all iframes with retries)."""
    logger.info("Waiting for video player and Play button to become interactive...")

    start_time = asyncio.get_event_loop().time()
    deadline = start_time + (timeout_ms / 1000.0)

    # Initial settling wait
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(1000)

    while asyncio.get_event_loop().time() < deadline:
        # Check all targets (main page + all iframes)
        targets = [page] + page.frames

        for target in targets:
            # 1. Check if video is already playing
            try:
                is_playing = await target.evaluate("""() => {
                    const video = document.querySelector('video');
                    if (video && !video.paused && video.currentTime > 0) return true;
                    if (window.jwplayer) {
                        try {
                            const p = window.jwplayer('player') || window.jwplayer(0) || window.jwplayer();
                            if (p && typeof p.getState === 'function' && p.getState() === 'playing') return true;
                        } catch(e) {}
                    }
                    const playerDiv = document.querySelector('.jwplayer');
                    if (playerDiv && playerDiv.classList.contains('jw-state-playing')) return true;
                    return false;
                }""")
                if is_playing:
                    logger.info("Video playback confirmed active (jw-state-playing)!")
                    return True
            except Exception:
                pass

            # 2. Try JavaScript JW Player API first (most direct and reliable)
            try:
                played_via_js = await target.evaluate("""() => {
                    try {
                        if (window.jwplayer) {
                            const p = window.jwplayer('player') || window.jwplayer(0) || window.jwplayer();
                            if (p && typeof p.play === 'function') {
                                p.play();
                                return true;
                            }
                        }
                    } catch(e) {}
                    return false;
                }""")
                if played_via_js:
                    logger.info("Invoked window.jwplayer().play() via JavaScript.")
            except Exception:
                pass

            # 3. Try native click on play button selectors and mouse events
            play_selectors = [
                'div.jw-icon-display[aria-label="Play"]',
                'div[aria-label="Play"]',
                'div.jw-icon-display[role="button"]',
                'div.jw-icon-display',
                'div.jw-display-icon-display',
                '.jw-display-controls',
                '#player .jw-display-container',
                '#player .jw-media',
                'video.jw-video',
                'video',
            ]

            for sel in play_selectors:
                try:
                    btn = await target.query_selector(sel)
                    if btn:
                        logger.info(f"Clicking play element '{sel}'...")
                        try:
                            await btn.click(timeout=1000, force=True)
                        except Exception:
                            await btn.evaluate("""el => {
                                el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                                el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                                el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                                if (typeof el.click === 'function') el.click();
                            }""")
                        break
                except Exception:
                    continue

            # 4. Try HTML5 video element play() directly
            try:
                await target.evaluate("""() => {
                    const video = document.querySelector('video');
                    if (video && video.paused) {
                        const playPromise = video.play();
                        if (playPromise !== undefined) {
                            playPromise.catch(() => {
                                video.muted = true;
                                video.play();
                            });
                        }
                    }
                }""")
            except Exception:
                pass

        await asyncio.sleep(1.0)

    # Final check
    for target in [page] + page.frames:
        try:
            is_playing = await target.evaluate("""() => {
                const video = document.querySelector('video');
                if (video && !video.paused) return true;
                const playerDiv = document.querySelector('.jwplayer');
                if (playerDiv && playerDiv.classList.contains('jw-state-playing')) return true;
                return false;
            }""")
            if is_playing:
                logger.info("Video playback confirmed active!")
                return True
        except Exception:
            pass

    logger.warning("Could not automatically verify playback within timeout.")
    return False


async def open_and_play_stream(
    stream_url: str,
    page: Optional[Page] = None,
    browser_type: str = "chromium",
    use_adblock: bool = True,
):
    """Opens a stream URL, intercepts the M3U8 source, clicks the play button, and keeps it playing."""
    captured_m3u8: List[str] = []

    def on_request(request):
        url = request.url
        if ".m3u8" in url or "master.m3u8" in url or "playlist.m3u8" in url:
            if url not in captured_m3u8:
                captured_m3u8.append(url)
                print(f"\n🔥 [M3U8 Stream Detected]: {url}")

    # If page is already provided (e.g. from main.py unified flow)
    if page:
        page.on("request", on_request)
        print(f"\n🌐 Loading stream in current browser: {stream_url}...")
        await page.goto(stream_url, wait_until="domcontentloaded")
        await auto_play_jwplayer(page)

        # Check if JW Player has direct playlist source via JS
        js_source = await extract_jwplayer_source(page)
        if js_source and js_source not in captured_m3u8:
            captured_m3u8.append(js_source)
            print(f"🔥 [M3U8 Stream from JWPlayer API]: {js_source}")

        return captured_m3u8

    # Standalone run: Launch browser
    print("\n" + "=" * 65)
    print(" 🎬 Stream Player (Auto-Play & M3U8 Extractor)")
    print(f" Stream URL: {stream_url}")
    print(f" Browser:    {browser_type.upper()}")
    print(f" Adblock:    {'uBlock Origin (Enabled 🛡️)' if use_adblock else 'Disabled'}")
    print("=" * 65 + "\n")

    async with async_playwright() as p:
        context, new_page = await launch_browser_context(
            p=p,
            browser_type=browser_type,
            use_adblock=use_adblock,
            headless=False,
        )

        new_page.on("request", on_request)

        print(f"🌐 Navigating to {stream_url}...")
        await new_page.goto(stream_url, wait_until="domcontentloaded")

        # Automatically click the center play button
        clicked = await auto_play_jwplayer(new_page)
        if clicked:
            print("\n▶️ Play button clicked automatically!")
        else:
            print("\n⚠️ Note: You can also click the play button in the browser window if prompted.")

        # Wait a moment for stream requests to fire
        await new_page.wait_for_timeout(2000)

        # Check JW Player API for the stream source
        js_source = await extract_jwplayer_source(new_page)
        if js_source and js_source not in captured_m3u8:
            captured_m3u8.append(js_source)
            print(f"🔥 [M3U8 Stream from JWPlayer API]: {js_source}")

        if captured_m3u8:
            print("\n" + "-" * 65)
            print(" 📡 External Player Info:")
            print(f" Stream URL: {captured_m3u8[0]}")
            print(" You can play this directly in VLC, IINA, or mpv:")
            print(f'   • VLC:  vlc --http-referrer="https://hls.st/" "{captured_m3u8[0]}"')
            print(f'   • IINA: open -a IINA "{captured_m3u8[0]}"')
            print(f'   • mpv:  mpv --referrer="https://hls.st/" "{captured_m3u8[0]}"')
            print("-" * 65)

        print("\n👉 Stream is playing in browser. Press ENTER here to close and exit.")
        await asyncio.to_thread(input, "\nPress ENTER to close stream... ")

        print("Closing browser...")
        await context.close()
        print("Done.")

    return captured_m3u8


def main():
    parser = create_parser(
        "Open a stream URL (e.g. hls.st), auto-click the play button, and extract the M3U8 link.",
        "stream_url",
        "no_adblock",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            open_and_play_stream(
                stream_url=args.stream_url,
                use_adblock=not args.no_adblock,
            )
        )
    except KeyboardInterrupt:
        print("\nStream session closed.")
        sys.exit(0)


if __name__ == "__main__":
    main()

import asyncio
import logging
from pathlib import Path
import random
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from playwright.async_api import BrowserContext, Page, async_playwright

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

DEFAULT_DATA_FILE = PROJECT_ROOT / "output" / "football_games.json"

from cli_args import create_parser
from playground import launch_browser_context, reveal_window
from terminal_menu import interactive_select
from play_stream import auto_play_jwplayer, extract_jwplayer_source
from player_launcher import play_stream_with_mpv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("stream_selector")


async def extract_streams_from_page(
    page: Page,
    timeout_ms: int = 15000,
    post_click_wait_ms: int = 2500,
) -> List[Dict[str, str]]:
    """Clicks the Broadcast tab, waits for content to hydrate inside #tabContent, and extracts all stream options."""
    # 1. Click Broadcast Tab button (Image 1)
    logger.info("Looking for Broadcast tab button ('data-tab=broadcast')...")
    broadcast_selector = (
        'button[data-tab="broadcast"], '
        '.tab-btn[data-tab="broadcast"], '
        'button:has-text("Transmissão"), '
        'button:has-text("Broadcast")'
    )

    try:
        broadcast_btn = await page.wait_for_selector(broadcast_selector, timeout=timeout_ms)
        if broadcast_btn:
            await broadcast_btn.scroll_into_view_if_needed()
            logger.info("Clicking Broadcast tab button...")
            try:
                await broadcast_btn.click(timeout=3000)
            except Exception:
                await broadcast_btn.evaluate("el => el.click()")

            logger.info(f"Waiting {post_click_wait_ms}ms after clicking Broadcast tab...")
            await page.wait_for_timeout(post_click_wait_ms)
        else:
            logger.warning("Broadcast button not found via selector.")
    except Exception as e:
        logger.warning(f"Could not find or click broadcast tab button: {e}. Checking if streams are already present.")

    # 2. Wait for stream links to appear inside #tabContent (Image 2)
    logger.info("Waiting for stream options inside #tabContent to render...")
    stream_link_selector = (
        '#tabContent a[href], '
        '#tabContent div[data-slot="card-content"] a, '
        'a[href*="hls.st"], '
        'div.flex-wrap a[target="_blank"]'
    )

    try:
        await page.wait_for_selector(stream_link_selector, timeout=timeout_ms)
    except Exception as e:
        logger.warning(f"Timed out waiting for stream selectors ({stream_link_selector}): {e}")

    await page.wait_for_timeout(500)

    # 3. Collect stream options specifically from #tabContent
    tab_content = await page.query_selector('#tabContent')
    if tab_content:
        logger.info("Found #tabContent container.")
        link_elements = await tab_content.query_selector_all('a[href]')
    else:
        logger.info("#tabContent not found, querying page directly for stream links...")
        link_elements = await page.query_selector_all(
            'div[data-slot="card-content"] a[href], a[href*="hls.st"], div.flex-wrap a[target="_blank"]'
        )

    streams: List[Dict[str, str]] = []
    seen_urls = set()

    for el in link_elements:
        href = await el.get_attribute("href")
        if not href or href in seen_urls:
            continue

        if href.startswith("/") and not href.startswith("/match/"):
            continue

        span = await el.query_selector("span")
        if span:
            name = (await span.inner_text()).strip()
        else:
            name = (await el.inner_text()).strip()

        if not name:
            name = href

        name = " ".join(name.split())

        seen_urls.add(href)
        streams.append({
            "name": name,
            "url": href,
        })
        logger.info(f"Detected stream: {name} -> {href}")

    return streams


def pick_stream(streams: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Stream selection logic:

    - If only 1 stream available: automatically select that one.
    - If multiple streams:
      - If any end with 'PT' (case-insensitive): pick that one (random if > 1).
      - If none end with 'PT': let user choose via terminal arrows and Enter.
    """
    if not streams:
        return None

    # 1. If only 1 stream available, automatically pick it
    if len(streams) == 1:
        chosen = streams[0]
        print(f"\n🎯 Only 1 stream available. Automatically selected: \033[1;32m{chosen['name']}\033[0m")
        return chosen

    # 2. Filter streams ending in 'PT' (e.g. "Sport TV3 PT", "SPORT TV 3 PT")
    pt_streams = [
        s for s in streams
        if s["name"].strip().upper().endswith("PT")
    ]

    if len(pt_streams) == 1:
        chosen = pt_streams[0]
        print(f"\n🎯 Found 1 Portuguese stream: \033[1;32m{chosen['name']}\033[0m")
        return chosen

    elif len(pt_streams) > 1:
        chosen = random.choice(pt_streams)
        print(f"\n🎲 Found {len(pt_streams)} Portuguese streams. Randomly selected: \033[1;32m{chosen['name']}\033[0m")
        return chosen

    else:
        # 3. No PT streams found -> Let user choose via terminal menu
        print("\nℹ️  No streams ending in 'PT' found. Please select an available stream:")
        options = [f"{s['name']}  ({s['url']})" for s in streams]
        idx = interactive_select(
            options=options,
            title="Available Streams",
            header_info="Use ↑/↓ to choose a stream, then press ENTER to open.",
        )
        if idx is not None:
            return streams[idx]
        return None


async def navigate_and_play_stream(
    page: Page,
    match_url: str,
    post_click_wait_ms: int = 2500,
) -> Optional[Dict[str, Any]]:
    """Uses an already-open Page: navigates to match, clicks Broadcast, picks stream, clicks button, and captures M3U8."""
    print(f"\n🌐 Loading match page: {match_url}...")
    await page.goto(match_url, wait_until="domcontentloaded")

    # Track M3U8 requests on the browser context
    captured_m3u8: List[str] = []
    context = page.context

    def on_request(request):
        req_url = request.url
        # Filter out tracking beacons/analytics (e.g. jwpltx.com/ping.gif)
        if any(tracker in req_url for tracker in ("ping.gif", "jwpltx.com", "telemetry", "analytics", "doubleclick")):
            return
        if (".m3u8" in req_url or "playlist" in req_url) and not req_url.endswith(".js"):
            if req_url not in captured_m3u8:
                captured_m3u8.append(req_url)
                logger.info(f"Captured M3U8 URL: {req_url}")

    context.on("request", on_request)

    streams = await extract_streams_from_page(page, post_click_wait_ms=post_click_wait_ms)

    if not streams:
        print("❌ No streams found on this match page.")
        return None

    print(f"\n📋 Detected {len(streams)} stream option(s):")
    for s in streams:
        print(f"   • {s['name']} -> {s['url']}")

    selected = pick_stream(streams)
    if not selected:
        print("\nStream selection cancelled.")
        return None

    stream_url = selected["url"]
    stream_name = selected["name"]
    print(f"\n🚀 Selecting Stream Button: \033[1;32m{stream_name}\033[0m")
    print(f"🔗 Stream Page URL: {stream_url}\n")

    logger.info(f"Clicking the stream button for '{stream_name}'...")
    stream_btn = await page.query_selector(f'#tabContent a[href="{stream_url}"]')
    if not stream_btn:
        stream_btn = await page.query_selector(f'a[href="{stream_url}"]')
    if not stream_btn:
        stream_btn = await page.query_selector(f'#tabContent a:has-text("{stream_name}")')

    stream_target_page = page
    if stream_btn:
        initial_pages = set(page.context.pages)
        await stream_btn.scroll_into_view_if_needed()
        try:
            await stream_btn.click(timeout=3000)
        except Exception:
            await stream_btn.evaluate("el => el.click()")

        await page.wait_for_timeout(1500)
        current_pages = set(page.context.pages)
        new_pages = current_pages - initial_pages
        if new_pages:
            stream_target_page = list(new_pages)[0]
            await stream_target_page.bring_to_front()
            logger.info("New stream tab opened and brought to front.")
    else:
        logger.warning(f"Could not locate stream button on page, navigating to {stream_url}...")
        await page.goto(stream_url)
        stream_target_page = page

    # Auto-click the center Play button in the player (JW Player)
    logger.info("Auto-clicking stream Play button...")
    await auto_play_jwplayer(stream_target_page)

    # Reveal browser window on screen now that play has been pressed
    logger.info("Play button pressed. Revealing browser window on screen...")
    await reveal_window(context, stream_target_page)

    # Wait up to 5 seconds to capture the .m3u8 network request
    for _ in range(10):
        if captured_m3u8:
            break
        await page.wait_for_timeout(500)

    # Also check if JW Player provides playlist source via JS API
    js_source = await extract_jwplayer_source(stream_target_page)
    if js_source and js_source not in captured_m3u8:
        captured_m3u8.append(js_source)

    result_data = {
        "name": stream_name,
        "page_url": stream_url,
        "m3u8_url": captured_m3u8[0] if captured_m3u8 else None,
        "all_m3u8": captured_m3u8,
        "target_page": stream_target_page,
    }

    if result_data["m3u8_url"]:
        print("\n" + "=" * 65)
        print(f" 🎯 CAPTURED M3U8 STREAM:")
        print(f" {result_data['m3u8_url']}")
        print("=" * 65 + "\n")

    return result_data


async def resolve_stream(
    match_url: str,
    post_click_wait_ms: int = 2500,
    headless: bool = True,
    **kwargs,
) -> Optional[Dict[str, str]]:
    """Resolves stream options from match page using Chromium (headless by default)."""
    logger.info(f"Resolving stream (headless={headless}) for: {match_url}...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page()

        await page.goto(match_url, wait_until="domcontentloaded")
        streams = await extract_streams_from_page(page, post_click_wait_ms=post_click_wait_ms)
        await browser.close()

        if not streams:
            print("❌ No streams found on this match page.")
            return None

        print(f"\n📋 Detected {len(streams)} stream option(s):")
        for s in streams:
            print(f"   • {s['name']} -> {s['url']}")

        selected = pick_stream(streams)
        return selected


# Alias for backward compatibility
resolve_stream_headless = resolve_stream


async def get_match_streams(
    match_url: str,
    post_click_wait_ms: int = 2500,
    headless: bool = True,
) -> List[Dict[str, str]]:
    """Scrapes all stream options from the match page in exact site order."""
    logger.info(f"Resolving all streams (headless={headless}) for: {match_url}...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page()
        try:
            await page.goto(match_url, wait_until="domcontentloaded")
            streams = await extract_streams_from_page(page, post_click_wait_ms=post_click_wait_ms)
            return streams
        finally:
            await browser.close()


def get_default_stream_index(streams: List[Dict[str, str]]) -> int:
    """Returns the index of the best initial stream (PT stream if found, else 0)."""
    if not streams:
        return 0
    for idx, s in enumerate(streams):
        if s.get("name", "").strip().upper().endswith("PT"):
            return idx
    return 0


async def play_stream_in_browser(
    stream: Dict[str, str],
    match_url: str,
    browser_type: str = "chromium",
    use_adblock: bool = True,
):
    """Launches the visible browser directly on the stream URL with proper Referer, then clicks Play."""
    stream_url = stream["url"]
    stream_name = stream["name"]
    print(f"\n🚀 Launching Browser for Stream: \033[1;32m{stream_name}\033[0m")
    print(f"🔗 Stream URL: {stream_url}\n")

    async with async_playwright() as p:
        context, page = await launch_browser_context(
            p=p,
            browser_type=browser_type,
            use_adblock=use_adblock,
            headless=False,
        )

        print(f"🌐 Navigating to stream player with valid Referer ({match_url})...")
        await page.goto(stream_url, referer=match_url, wait_until="domcontentloaded")

        # If navigation opened a new tab, focus the latest tab
        target_page = page
        if len(context.pages) > 1:
            target_page = context.pages[-1]
            await target_page.bring_to_front()

        # Auto-click the center Play button in JW Player (with continuous retries & iframe support)
        logger.info("Auto-clicking stream Play button...")
        clicked = await auto_play_jwplayer(target_page, timeout_ms=15000)
        if clicked:
            print("\n▶️ Play button clicked successfully!")
        else:
            print("\n⚠️ Note: If video didn't start, please click the play button in the browser.")

        print("-" * 65)
        print(f" 🟢 Playing '{stream_name}' in browser with uBlock Origin.")
        print(" Press ENTER in this terminal when you want to close and exit.")
        print("-" * 65 + "\n")

        await asyncio.to_thread(input, "\nPress ENTER to close stream and exit... ")
        print("Closing stream session...")
        await context.close()
        print("Done.")


async def select_and_open_stream(
    match_url: str,
    use_adblock: bool = True,
    post_click_wait_ms: int = 2500,
    visible: bool = False,
    **kwargs,
):
    """Resolves a stream from the match page headlessly and streams it in MPV via local HLS relay."""
    is_headless = not visible
    selected = await resolve_stream(
        match_url=match_url,
        post_click_wait_ms=post_click_wait_ms,
        headless=is_headless,
    )
    if not selected:
        return

    await play_stream_with_mpv(
        stream_url=selected["url"],
        match_url=match_url,
        use_adblock=use_adblock,
        headless=is_headless,
        match_title=selected.get("name", "SoccerStreams Live"),
    )


def main():
    parser = create_parser(
        "Select and open the best stream for a football match in MPV via headless relay.",
        "match_url",
        "visible",
        "no_adblock",
        "wait_ms",
    )

    args = parser.parse_args()

    url = args.match_url
    if not url:
        if DEFAULT_DATA_FILE.exists():
            import json
            with open(DEFAULT_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            matches = data.get("matches_flat", [])
            if matches:
                options = [
                    f"{m['home_team']['name']} vs {m['away_team']['name']} ({m.get('time_or_score')})"
                    for m in matches
                ]
                print("\n⚽ Pick a match to find streams:")
                idx = interactive_select(options, title="Select Match")
                if idx is not None:
                    url = matches[idx]["full_url"]

    if not url:
        print(f"❌ No match URL provided. Usage: python src/select_stream.py <match_url>")
        sys.exit(1)

    try:
        asyncio.run(
            select_and_open_stream(
                match_url=url,
                use_adblock=not args.no_adblock,
                post_click_wait_ms=args.wait_ms,
                visible=args.visible,
            )
        )
    except KeyboardInterrupt:
        print("\nSession ended.")
        sys.exit(0)


if __name__ == "__main__":
    main()

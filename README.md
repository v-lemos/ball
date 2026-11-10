# SoccerStreams Scraper & Stream Viewer

An automated Python scraper and match stream viewer for [rdaily.live/sports/football](https://rdaily.live/sports/football) using Playwright with built-in adblocking (**uBlock Origin**).

---

## 🛡️ Browser Engines & Adblocking

- **Chromium (Default)**: Runs with **uBlock Origin Lite** (MV3 extension).
- **Firefox**: Runs with official signed **uBlock Origin** (`uBlock0@raymondhill.net`).
- *(WebKit has been removed).*

Both engines run with a persistent profile so uBlock Origin blocks all ads, popups, and malicious redirects on `rdaily.live` and streaming hosts like `hls.st`.

---

## 🚀 Quick Start

### 1. Set Up the Virtual Environment

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser binaries (Chromium and Firefox)
playwright install chromium firefox
```

---

## 🚀 Unified Pipeline (Single Command Execution)

To run the entire workflow:

```bash
source .venv/bin/activate

# Default: Headless scraping and stream resolution, opens browser directly at stream with uBlock Origin and auto-plays
python main.py

# Keep the browser window visible from the beginning across all steps (overriding default headless mode)
python main.py --visible

# Run with Firefox instead of Chromium
python main.py --browser firefox

# Custom tab wait time (in ms)
python main.py --wait-ms 3000
```

### What happens in this pipeline:
1. **Step 1 (Scraper - Headless by default)**: Navigates to `rdaily.live/sports/football`, parses all tournament fixtures, and saves them to `output/football_games.json`.
2. **Step 2 (Game Selection - Terminal Menu)**: Displays an interactive arrow-key terminal menu bounded strictly to games that are **LIVE** or starting within **10 minutes** (sorted first).
3. **Step 3 (Stream Selection - Headless by default)**: Headlessly opens the match page, clicks the **Broadcast** tab, auto-selects if only 1 stream is available, auto-picks the `PT` stream (or prompts via terminal if no PT stream exists), and resolves the stream URL.
4. **Step 4 (Browser Playback with uBlock Origin)**: Launches the browser window directly with legitimate Referer headers, enables uBlock Origin to eliminate ads and popups, auto-clicks the center **Play** button, and keeps the stream active.

---

## 🛠️ Standalone Scripts

You can also run any step independently:

* **Scraper only**:
  ```bash
  python src/scraper.py
  python src/scraper.py --browser firefox
  ```
* **Game Selector only**:
  ```bash
  python src/select_game.py
  python src/select_game.py --browser firefox
  ```
* **Stream Selector only** (for a specific match URL):
  ```bash
  python src/select_stream.py https://rdaily.live/match/football/WOq36LXO
  python src/select_stream.py https://rdaily.live/match/football/WOq36LXO --browser firefox
  ```
* **Direct Stream Player & M3U8 Extractor**:
  ```bash
  # Opens stream, auto-clicks play, and intercepts the .m3u8 link:
  python src/play_stream.py https://hls.st/beta/b566614ce7dbab50ec5f5b4a142f6989
  ```
* **Interactive Browser Playground**:
  ```bash
  python src/playground.py
  python src/playground.py --browser firefox
  ```

---

## 📂 Project Structure

```
soccerStreams/
├── .venv/                      # Python virtual environment
├── extensions/
│   ├── ubolite/                # uBlock Origin Lite for Chromium
│   └── firefox/                # uBlock Origin signed XPI for Firefox
├── requirements.txt            # Dependencies (playwright, beautifulsoup4)
├── main.py                     # Unified single-browser pipeline (Root entrypoint)
├── src/                        # Modular component scripts
│   ├── __init__.py             # Package marker
│   ├── scraper.py              # Step 1: Scraper & DOM parser
│   ├── select_game.py          # Step 2: Interactive game selector
│   ├── select_stream.py        # Step 3: Broadcast button click & PT stream picker
│   ├── playground.py           # Browser context launcher & interactive tester
│   └── terminal_menu.py        # Terminal arrow-key controls & interactive menus
├── README.md                   # Documentation
└── output/
    └── football_games.json     # Extracted match and competition data
```

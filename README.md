# SoccerStreams Scraper & Stream Viewer

An automated Python scraper and match stream viewer for [rdaily.live/sports/football](https://rdaily.live/sports/football) using Playwright (**Chromium** only) with built-in adblocking (**uBlock Origin Lite** MV3) and native **MPV** playback.

---

## 🛡️ Browser Engine & Adblocking

- **Chromium**: Runs with **uBlock Origin Lite** (MV3 extension) with persistent profile to block all ads, popups, and malicious redirects on `rdaily.live` and streaming hosts.

---

## 🚀 Quick Start

### 1. System Requirements & Setup

`mpv` is required to play streams with high performance and zero ads.

```bash
# Install mpv
# macOS:
brew install mpv

# Ubuntu / Debian:
sudo apt install mpv

# Arch Linux:
sudo pacman -S mpv

# Windows:
winget install mpv.mpv
```

### 2. Set Up the Virtual Environment

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright Chromium browser binary
playwright install chromium
```

---

## 🚀 Unified Pipeline (Single Command Execution)

To run the entire workflow:

```bash
source .venv/bin/activate

# Default: 100% headless scraping & resolution, launches directly into MPV (1080p 60fps)
python main.py

# Auto-select a match by team name (fuzzy matching, e.g. "bayern", "porto", "chelsea")
python main.py --team bayern
# Or shorthand:
python main.py -t porto

# Optional: Keep the browser window visible across all steps
python main.py --visible

# Optional: Custom tab wait time (in ms)
python main.py --wait-ms 3000
```

### What happens in this pipeline:
1. **Step 1 (Scraper - Headless)**: Navigates to `rdaily.live/sports/football`, parses all tournament fixtures, and saves them to `output/football_games.json`.
2. **Step 2 (Game Selection - Terminal Menu)**: Displays an interactive arrow-key terminal menu bounded strictly to games that are **LIVE** or starting within **10 minutes** (sorted first).
3. **Step 3 (Stream Resolution - Headless)**: Opens the match page headlessly, clicks the **Broadcast** tab, and captures all available broadcast streams in exact site order (auto-defaults to Portuguese `PT` stream if available).
4. **Step 4 (Headless HLS Relay, MPV Playback & Interactive Switcher)**: Launches **MPV** pointing to an authenticated local HLS relay. While MPV streams, an interactive stream switcher is rendered in the terminal: use `↑`/`↓` and `ENTER` to switch between streams on command without restarting the pipeline. Closing MPV or pressing `q` cleanly exits.

---

## 🛠️ Standalone Scripts

You can also run any step independently:

* **Scraper only**:
  ```bash
  python src/scraper.py
  ```
* **Game Selector only**:
  ```bash
  python src/select_game.py
  # Or with team auto-selection:
  python src/select_game.py -t bayern
  ```
* **Stream Selector only** (for a specific match URL):
  ```bash
  python src/select_stream.py https://rdaily.live/match/football/WOq36LXO
  ```
* **Direct Stream Player & M3U8 Extractor**:
  ```bash
  # Opens stream in Chromium, auto-clicks play, and intercepts the .m3u8 link:
  python src/play_stream.py https://hls.st/beta/b566614ce7dbab50ec5f5b4a142f6989
  ```
* **Interactive Browser Playground**:
  ```bash
  python src/playground.py
  ```

---

## 📂 Project Structure

```
soccerStreams/
├── .venv/                      # Python virtual environment
├── extensions/
│   └── ubolite/                # uBlock Origin Lite for Chromium
├── requirements.txt            # Dependencies (playwright, beautifulsoup4)
├── main.py                     # Unified single-browser pipeline (Root entrypoint)
├── src/                        # Modular component scripts
│   ├── __init__.py             # Package marker
│   ├── scraper.py              # Step 1: Scraper & DOM parser
│   ├── select_game.py          # Step 2: Interactive game selector
│   ├── select_stream.py        # Step 3: Broadcast button click & PT stream picker
│   ├── player_launcher.py      # Step 4: Headless HLS relay server & MPV launcher
│   ├── playground.py           # Browser context launcher & interactive tester
│   └── terminal_menu.py        # Terminal arrow-key controls & interactive menus
├── README.md                   # Documentation
└── output/
    └── football_games.json     # Extracted match and competition data
```

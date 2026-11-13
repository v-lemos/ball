# Ball

A Python scraper and match stream viewer for [rdaily.live/sports/football](https://rdaily.live/sports/football) using Playwright (**Chromium** only) with built-in adblocking (**uBlock Origin Lite** MV3) and native **MPV** playback.

---

## Browser Engine & Adblocking

- **Chromium**: Runs with **uBlock Origin Lite** (MV3 extension) with persistent profile to block all ads, popups, and malicious redirects on `rdaily.live` and streaming hosts.

---

## Quick Start

### 1. System Requirements & Setup

`mpv` (for zero-latency video playback) and `ffmpeg` (for fast remuxing of clips & replays) are required system packages.

```bash
# macOS:
brew install mpv ffmpeg

# Ubuntu / Debian:
sudo apt update && sudo apt install mpv ffmpeg

# Arch Linux:
sudo pacman -S mpv ffmpeg

# Windows:
winget install mpv.mpv
winget install Gyan.FFmpeg
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

## Unified Pipeline (Single Command Execution)

To run the entire workflow:

```bash
source .venv/bin/activate

# DEFAULT RUN:
# 100% headless scraping & resolution, launches directly into MPV (1080p 60fps)
python main.py

# OPTIONAL FLAGS:
# Auto-select a match by team name (fuzzy matching, e.g. "porto", "portugal"...)
python main.py -t porto

# Keep the browser window visible across all steps
python main.py -v

# Show detailed pipeline steps and progress info (default: clean "Loading..." between major steps)
python main.py -i

# Open the stream muted in mpv
python main.py -m

# Always show mpv player controls (OSC bar)
python main.py -c

# Real-time AI neural upscaling in mpv (FSRCNNX super-resolution, deband, ewa_lanczossharp)
python main.py -u

# Custom output JSON path
python main.py -o output/my_games.json

# ADVANCED FLAGS:
# Custom tab wait time (in ms)
python main.py --wait-ms 3000

# Sport route for testing, check the actual website for available sports, write them exactly as they are in the URL
python main.py --sport basketball

# Disable adblocker (no idea why you would ever want to do that)
python main.py --no-adblock
```

### What happens in this pipeline:
1. **Step 1 (Scraper - Headless)**: Navigates to `rdaily.live/sports/football`, parses all tournament fixtures, and saves them to `output/football_games.json`.
2. **Step 2 (Game Selection - Terminal Menu)**: Displays an interactive arrow-key terminal menu bounded strictly to games that are **LIVE** or starting within **10 minutes** (sorted first).
3. **Step 3 (Stream Resolution - Headless)**: Opens the match page headlessly, clicks the **Broadcast** tab, and captures all available broadcast streams in exact site order (auto-defaults to Portuguese `PT` stream if available).
4. **Step 4 (Headless HLS Relay, MPV Playback & Interactive Switcher)**: Launches **MPV** pointing to an authenticated local HLS relay. While MPV streams, an interactive stream switcher is rendered in the terminal: use `↑`/`↓` and `ENTER` to switch between streams on command without restarting the pipeline. Closing MPV or pressing `q` cleanly exits.
5. **🎬 Quick Clipper & Instant Goal Replay Buffer**:
   - **`[a]`**: Start recording a live clip anytime.
   - **`[c]`**: Stop recording and instantly save the clip as a standalone `.mp4` into `output/clips/`.
   - **`[g]`**: Download everything available in the rewind replay buffer (from the earliest cached moment up to live) into `output/clips/`.
   - *Controls work whether focused in the MPV player window or in the terminal switcher!*
   - Real-time on-screen display (OSD) feedback directly in MPV and terminal live elapsed counter (`🔴 RECORDING LIVE CLIP [00:15]`).
   - Lightning-fast remuxing via `ffmpeg -c copy` without slow video re-encoding.

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
│   ├── cli_args.py             # CLI parser configuration
│   ├── scraper.py              # Step 1: Scraper & DOM parser
│   ├── select_game.py          # Step 2: Interactive game selector
│   ├── select_stream.py        # Step 3: Broadcast button click & PT stream picker
│   ├── player_launcher.py      # Step 4: Headless HLS relay server & MPV launcher
│   ├── stream_recorder.py      # Quick Clipper & instant goal replay manager
│   ├── mpv_recorder.lua        # MPV player window keybindings for (a, c, g)
│   ├── playground.py           # Browser context launcher & interactive tester
│   └── terminal_menu.py        # Terminal arrow-key controls & interactive menus
├── README.md                   # Documentation
└── output/
    ├── football_games.json     # Extracted match and competition data
    └── clips/                  # Saved MP4 clips and goal replays
```

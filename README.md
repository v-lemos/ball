# SoccerStreams Scraper

A Python scraper using Playwright optimized for Apple Silicon (macOS arm64) to fetch and structure football matches and competitions from [rdaily.live/sports/football](https://rdaily.live/sports/football).

---

## ⚡ Browser Engine on Apple Silicon

By default, the scraper uses **WebKit** (`p.webkit`):
- **Why WebKit?** On macOS Apple Silicon (M1/M2/M3/M4), WebKit directly leverages Apple's native system frameworks (Metal, CoreGraphics, CoreText), using significantly less RAM and CPU/battery compared to full Chromium instances while maintaining fast rendering.
- **Chromium alternative**: Playwright also provides native ARM64 Chromium (`--browser chromium`), which you can toggle anytime.

---

## 🚀 Quick Start

### 1. Set Up the Virtual Environment

```bash
# Create virtual environment (already set up in this directory)
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser binaries (WebKit and Chromium)
playwright install webkit chromium
```

### 2. Run the Scraper

```bash
# Run in default optimized headless WebKit mode
source .venv/bin/activate
python main.py

# Run with visible browser window (headed mode)
python main.py --headed

# Run with Chromium instead of WebKit
python main.py --browser chromium

# Custom output destination
python main.py --output output/my_games.json
```

---

## 📂 Project Structure

```
soccerStreams/
├── .venv/                      # Python virtual environment
├── requirements.txt            # Dependencies (playwright, beautifulsoup4)
├── scraper.py                  # Core scraper & DOM parsing logic
├── main.py                     # CLI entrypoint with formatted summary
├── README.md                   # Documentation
└── output/
    └── football_games.json     # Extracted match and competition data
```

---

## 📊 Output Format (`output/football_games.json`)

The output is formatted as JSON containing both a competition-grouped structure and a flat match list:

```json
{
  "scraped_at": "2026-11-10T14:24:33Z",
  "source_url": "https://rdaily.live/sports/football",
  "browser_engine": "webkit",
  "summary": {
    "total_competitions": 2,
    "total_matches": 5
  },
  "competitions": [
    {
      "tournament_id": "tph5Yujo_AuYKNfEa",
      "region": "Europe",
      "competition": "UEFA Champions League Women - League phase",
      "total_matches": 2,
      "matches": [
        {
          "event_id": "WOq36LXO",
          "match_link": "/match/football/WOq36LXO",
          "full_url": "https://rdaily.live/match/football/WOq36LXO",
          "status": "notstarted",
          "status_label": "NS",
          "time_or_score": "17:45",
          "home_team": {
            "name": "Hacken W",
            "logo": "https://www.flashscore.com/res/image/data/AkyUYRle-SU3IbkKJ.png"
          },
          "away_team": {
            "name": "Chelsea W",
            "logo": "https://www.flashscore.com/res/image/data/ltBbMPU0-dQ3CHKf1.png"
          },
          "competition": "UEFA Champions League Women - League phase",
          "region": "Europe",
          "tournament_id": "tph5Yujo_AuYKNfEa"
        }
      ]
    }
  ]
}
```

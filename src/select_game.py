import argparse
import asyncio
from datetime import datetime
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple
import unicodedata

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

DEFAULT_DATA_FILE = PROJECT_ROOT / "output" / "football_games.json"

from scraper import FootballScraper
from select_stream import get_match_streams, get_default_stream_index, resolve_stream_headless
from player_launcher import play_interactive_stream_session, play_stream_with_mpv
from terminal_menu import interactive_game_menu, interactive_select


def parse_match_time(time_str: str, now: datetime) -> Optional[Tuple[datetime, float]]:
    """Parses a 'HH:MM' string and returns (match_datetime, minutes_until_start)."""
    if not time_str or ":" not in time_str:
        return None
    try:
        parts = time_str.strip().split(":")
        hours = int(parts[0])
        minutes = int(parts[1])
        match_time = now.replace(
            hour=hours, minute=minutes, second=0, microsecond=0
        )
        diff_minutes = (match_time - now).total_seconds() / 60
        return match_time, diff_minutes
    except Exception:
        return None


def categorize_matches(matches: List[Dict[str, Any]]):
    """Categorizes matches into:

    1. Live: Currently in-progress matches
    2. Right Up: Starting in <= 10 minutes (sorted soonest first)
    3. Upcoming: Starting in > 10 minutes (sorted soonest first)
    4. Finished: Completed matches
    """
    now = datetime.now()
    live = []
    rightUp = []
    upcoming = []
    finished = []

    for m in matches:
        status = (m.get("status") or "").lower()
        is_live = m.get("is_live", False) or status == "inprogress"
        time_or_score = m.get("time_or_score", "")

        if status == "finished":
            finished.append(m)
            continue

        if is_live:
            live.append((m, 0.0, "LIVE"))
            continue

        time_data = parse_match_time(time_or_score, now)
        if time_data:
            _, diff_minutes = time_data
            if diff_minutes <= 10:
                rightUp.append((m, diff_minutes, time_or_score))
            else:
                upcoming.append((m, diff_minutes))
        else:
            upcoming.append((m, 9999.0))

    rightUp.sort(key=lambda item: item[1])
    upcoming.sort(key=lambda item: item[1])

    return live, rightUp, upcoming, finished


def normalize_name(text: str) -> str:
    """Normalizes team or search strings: strips accents, lowercases, removes punctuation."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def score_team_match(query: str, team_name: str) -> float:
    """Calculates a match confidence score between 0.0 and 1.0.

    Supports exact matches, token overlap, and fuzzy string matching.
    """
    q = normalize_name(query)
    t = normalize_name(team_name)
    if not q or not t:
        return 0.0

    if q == t:
        return 1.0

    t_tokens = t.split()
    q_tokens = q.split()

    # Short query (<= 2 chars): require exact match or exact word token
    if len(q) <= 2:
        return 0.95 if (q == t or q in t_tokens) else 0.0

    meaningful_q = [tok for tok in q_tokens if len(tok) >= 3]
    meaningful_t = [tok for tok in t_tokens if len(tok) >= 3]

    # Full substring match
    if q in t:
        if q in t_tokens or (len(q_tokens) > 1 and all(token in t_tokens for token in q_tokens)):
            return 0.95
        len_ratio = len(q) / len(t)
        return 0.85 + (0.10 * len_ratio)

    if t in q:
        if t in q_tokens or (len(t_tokens) > 1 and all(token in q_tokens for token in t_tokens)):
            return 0.95
        len_ratio = len(t) / len(q)
        return 0.85 + (0.10 * len_ratio)

    # Token overlap based on meaningful query tokens
    if meaningful_q:
        matched_q_count = sum(1 for q_tok in meaningful_q if q_tok in t_tokens)
        if matched_q_count == len(meaningful_q):
            return 0.95
        if meaningful_t and all(t_tok in q_tokens for t_tok in meaningful_t):
            return 0.95

    # Fuzzy string matching with SequenceMatcher
    full_ratio = SequenceMatcher(None, q, t).ratio()
    best_token_ratio = 0.0

    for i in range(len(t_tokens) - len(q_tokens) + 1):
        window = " ".join(t_tokens[i : i + len(q_tokens)])
        r = SequenceMatcher(None, q, window).ratio()
        if r > best_token_ratio:
            best_token_ratio = r

    if len(meaningful_q) == 1:
        for t_tok in meaningful_t:
            r = SequenceMatcher(None, meaningful_q[0], t_tok).ratio()
            if r > best_token_ratio:
                best_token_ratio = r

    return max(full_ratio, best_token_ratio)


def find_matching_matches(
    matches: List[Dict[str, Any]], query: str, threshold: float = 0.65
) -> List[Tuple[Dict[str, Any], float, str]]:
    """Returns matching games sorted by score and status priority (LIVE > upcoming > finished)."""
    results = []
    for m in matches:
        home_name = m.get("home_team", {}).get("name") or ""
        away_name = m.get("away_team", {}).get("name") or ""
        home_score = score_team_match(query, home_name)
        away_score = score_team_match(query, away_name)

        if home_score >= away_score:
            best_score = home_score
            matched_team = home_name
        else:
            best_score = away_score
            matched_team = away_name

        if best_score >= threshold:
            results.append((m, best_score, matched_team))

    def sort_key(item):
        m, score, _ = item
        status = (m.get("status") or "").lower()
        is_live = m.get("is_live", False) or status == "inprogress"
        status_rank = 2 if is_live else (1 if status != "finished" else 0)
        return (round(score, 2), status_rank, score)

    results.sort(key=sort_key, reverse=True)
    return results


def select_game_by_team(
    matches: List[Dict[str, Any]], query: str
) -> Optional[Dict[str, Any]]:
    """Attempts to auto-select a game by matching home or away team with the given query.

    Returns the selected match dict, or None if no match found / user cancelled.
    """
    candidates = find_matching_matches(matches, query)
    if not candidates:
        print(f"⚠️  No matches found for team query '{query}'.")
        print("Falling back to full match selection menu...\n")
        return None

    top_match, top_score, top_team = candidates[0]
    second_score = candidates[1][1] if len(candidates) > 1 else 0.0

    is_single_choice = len(candidates) == 1 or (top_score >= 0.85 and (top_score - second_score) >= 0.15)

    if is_single_choice:
        home = top_match.get("home_team", {}).get("name", "TBD")
        away = top_match.get("away_team", {}).get("name", "TBD")
        status = (top_match.get("status") or "").lower()
        is_live = top_match.get("is_live", False) or status == "inprogress"
        time_str = top_match.get("time_or_score", "")

        if status == "finished":
            print(f"\n⚠️  Found match for '{query}': {home} vs {away}, but it is already FINISHED ({time_str or 'FT'}).")
            print("Falling back to full match selection menu...\n")
            return None

        tag = "LIVE" if is_live else f"Starts at {time_str}"
        print(f"\n🎯 Auto-selected match for team '{query}': {home} vs {away} [{tag}] (matched '{top_team}', confidence {top_score:.0%})")
        return top_match

    # Multiple candidates: present menu limited to matching games
    print(f"\n🤔 Multiple games matched '{query}':")
    options = []
    for m, score, matched_team in candidates:
        home = m.get("home_team", {}).get("name", "TBD")
        away = m.get("away_team", {}).get("name", "TBD")
        status = (m.get("status") or "").lower()
        is_live = m.get("is_live", False) or status == "inprogress"
        time_str = m.get("time_or_score", "")
        tag = "LIVE" if is_live else (f"FT {time_str}" if status == "finished" else time_str)
        options.append(f"{home} vs {away} ({tag}) [matched '{matched_team}']")

    selected_idx = interactive_select(
        options=options,
        title=f"Matches matching '{query}'",
    )
    if selected_idx is not None:
        chosen = candidates[selected_idx][0]
        chosen_home = chosen.get("home_team", {}).get("name", "TBD")
        chosen_away = chosen.get("away_team", {}).get("name", "TBD")
        print(f"\n🎯 Selected: {chosen_home} vs {chosen_away}")
        return chosen

    return None


def _format_match_label(match: Dict[str, Any], tag: str) -> str:
    """Formats a match entry as 'Home vs Away (tag)'."""
    home = match.get("home_team", {}).get("name") or "TBD"
    away = match.get("away_team", {}).get("name") or "TBD"
    return f"{home} vs {away} ({tag})"


def prompt_game_selection(
    matches: List[Dict[str, Any]],
    team: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Takes a list of matches, optionally auto-selects by team name, displays the terminal menu, and returns the chosen match dict."""
    if not matches:
        print("No football matches available for selection.")
        return None

    if team and team.strip():
        chosen = select_game_by_team(matches, team.strip())
        if chosen is not None:
            return chosen

    live, rightUp, upcoming, finished = categorize_matches(matches)

    live_items = [_format_match_label(m, time_tag) for m, _, time_tag in live]
    right_up_items = [_format_match_label(m, time_tag) for m, _, time_tag in rightUp]
    upcoming_items = [
        _format_match_label(m, m.get("time_or_score", "")) for m, _ in upcoming
    ]
    finished_items = [
        _format_match_label(m, m.get("time_or_score", "FT")) for m in finished
    ]

    selected_idx = interactive_game_menu(
        live_items=live_items,
        right_up_items=right_up_items,
        upcoming_items=upcoming_items,
        finished_items=finished_items,
    )

    if selected_idx is None:
        return None

    selectable = live + rightUp
    return selectable[selected_idx][0]


async def run_game_selection(
    use_adblock: bool = True,
    data_file: Path = DEFAULT_DATA_FILE,
    force_fetch: bool = False,
    visible: bool = False,
    team: Optional[str] = None,
):
    is_headless = not visible
    if not force_fetch and data_file.exists():
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        scraper = FootballScraper(headless=is_headless)
        data = await scraper.run(output_file=data_file)

    matches = data.get("matches_flat", [])
    if not matches:
        print("No football matches found.")
        return

    selected_match = prompt_game_selection(matches, team=team)
    if not selected_match:
        print("\nExited.")
        return

    home = selected_match["home_team"]["name"]
    away = selected_match["away_team"]["name"]
    target_url = selected_match["full_url"]

    print(f"\n🚀 Selected Match: {home} vs {away}")
    print(f"🔗 URL: {target_url}\n")

    streams = await get_match_streams(
        match_url=target_url,
        headless=is_headless,
    )

    if streams:
        default_idx = get_default_stream_index(streams)
        await play_interactive_stream_session(
            streams=streams,
            match_url=target_url,
            match_title=f"{home} vs {away}",
            initial_index=default_idx,
            use_adblock=use_adblock,
            headless=is_headless,
        )


def main():
    parser = argparse.ArgumentParser(
        description="Select and watch live or upcoming football games in MPV from rdaily.live."
    )
    parser.add_argument(
        "--file",
        type=str,
        default=str(DEFAULT_DATA_FILE),
        help="Path to JSON file with scraped games (default: output/football_games.json).",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Force a fresh scrape from rdaily.live before showing selection.",
    )
    parser.add_argument(
        "--visible",
        "--headed",
        action="store_true",
        help="Keep browser window visible (default: headless=True).",
    )
    parser.add_argument(
        "--no-adblock",
        action="store_true",
        help="Disable uBlock Origin Lite when resolving the stream.",
    )
    parser.add_argument(
        "-t",
        "--team",
        type=str,
        default=None,
        help="Auto-select match containing this team name (fuzzy match).",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            run_game_selection(
                use_adblock=not args.no_adblock,
                data_file=Path(args.file),
                force_fetch=args.fetch,
                visible=args.visible,
                team=args.team,
            )
        )
    except KeyboardInterrupt:
        print("\nSession ended.")
        sys.exit(0)


if __name__ == "__main__":
    main()

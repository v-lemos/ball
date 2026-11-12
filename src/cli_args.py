"""Centralized CLI argument definitions shared across pipeline entrypoints."""

import argparse
from typing import Any, Dict, Tuple

DEFAULT_SPORT = "football"
DEFAULT_WAIT_MS = 2500
DEFAULT_TEST_STREAM_URL = "https://hls.st/beta/b566614ce7dbab50ec5f5b4a142f6989"

ArgumentSpec = Tuple[Tuple[str, ...], Dict[str, Any]]

ARGUMENT_SPECS: Dict[str, ArgumentSpec] = {
    "visible": (
        ("-v",),
        {
            "dest": "visible",
            "action": "store_true",
            "help": "Keep browser window visible (default: headless=True).",
        },
    ),
    "no_adblock": (
        ("--no-adblock",),
        {
            "action": "store_true",
            "help": "Disable uBlock Origin Lite.",
        },
    ),
    "wait_ms": (
        ("-w",),
        {
            "dest": "wait_ms",
            "type": int,
            "default": DEFAULT_WAIT_MS,
            "help": f"Milliseconds to wait after clicking Broadcast tab (default: {DEFAULT_WAIT_MS}).",
        },
    ),
    "output": (
        ("-o",),
        {
            "dest": "output",
            "type": str,
            "default": None,
            "help": "Path to save scraped games (default: output/<sport>_games.json).",
        },
    ),
    "file": (
        ("--file",),
        {
            "type": str,
            "default": None,
            "help": "Path to JSON file with scraped games (default: output/<sport>_games.json).",
        },
    ),
    "fetch": (
        ("--fetch",),
        {
            "action": "store_true",
            "help": "Force a fresh scrape from rdaily.live before showing selection.",
        },
    ),
    "live": (
        ("--live",),
        {
            "action": "store_true",
            "help": "Only scrape live matches.",
        },
    ),
    "team": (
        ("-t",),
        {
            "dest": "team",
            "type": str,
            "default": None,
            "help": "Auto-select match containing this team name (fuzzy match).",
        },
    ),
    "sport": (
        ("-s",),
        {
            "dest": "sport",
            "type": str,
            "default": DEFAULT_SPORT,
            "help": f"Sport route on rdaily.live/sports/<sport> for testing (default: {DEFAULT_SPORT}).",
        },
    ),
    "match_url": (
        ("match_url",),
        {
            "nargs": "?",
            "default": None,
            "help": "URL of the match page (e.g., https://rdaily.live/match/football/WOq36LXO).",
        },
    ),
    "stream_url": (
        ("stream_url",),
        {
            "nargs": "?",
            "default": DEFAULT_TEST_STREAM_URL,
            "help": "The direct stream URL to open (e.g. https://hls.st/beta/...).",
        },
    ),
    "url": (
        ("url",),
        {
            "nargs": "?",
            "default": None,
            "help": "Target URL to open (default: https://rdaily.live/sports/<sport>).",
        },
    ),
}


def add_arguments(parser: argparse.ArgumentParser, *arg_names: str) -> argparse.ArgumentParser:
    """Registers the named argument specs from ARGUMENT_SPECS onto an ArgumentParser."""
    for name in arg_names:
        if name not in ARGUMENT_SPECS:
            raise ValueError(f"Unknown CLI argument spec: {name!r}")
        flags, kwargs = ARGUMENT_SPECS[name]
        parser.add_argument(*flags, **kwargs)
    return parser


def create_parser(description: str, *arg_names: str) -> argparse.ArgumentParser:
    """Builds an ArgumentParser with the given description and named argument specs."""
    parser = argparse.ArgumentParser(description=description, add_help=False)
    parser.add_argument(
        "-h",
        action="help",
        default=argparse.SUPPRESS,
        help="Show this help message and exit.",
    )
    return add_arguments(parser, *arg_names)

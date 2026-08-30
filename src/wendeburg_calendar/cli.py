"""Command-line interface.

Subcommands:
  harvest  - fetch configured sources and reconcile them into the database
  export   - write the current database state out to an RFC 5545 .ics feed
  run      - harvest then export (the default when no subcommand is given)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wendeburg_calendar.config import AppConfig, load_config
from wendeburg_calendar.db.repository import Repository
from wendeburg_calendar.export.ics_export import export_calendar
from wendeburg_calendar.harvest.pipeline import harvest_all
from wendeburg_calendar.http.fetcher import FixtureFetcher, HttpxFetcher
from wendeburg_calendar.llm.client import create_llm_client_from_env

# Importing this registers all bundled source adapters (e.g. "wendeburg").
import wendeburg_calendar.sources  # noqa: F401


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wendeburg-calendar",
        description=(
            "Harvest public regional event listings and publish them as a "
            "subscribable RFC 5545 calendar feed (calendar.ics)."
        ),
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to a TOML config file (default: %(default)s in the current directory).",
    )
    parser.add_argument(
        "--database",
        default=None,
        help="Override the SQLite database path configured in [general].database.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Override the .ics output path configured in [general].output.",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=None,
        metavar="SOURCE_ID",
        help="Limit harvesting to this source id. May be repeated for multiple sources.",
    )
    parser.add_argument(
        "--offline-fixture",
        default=None,
        metavar="DIR",
        help=(
            "Serve all HTTP requests (including robots.txt) from a local fixture "
            "directory containing a manifest.json, instead of making live network "
            "requests. Intended for offline end-to-end testing/demos."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Print more detail.")

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("harvest", help="Fetch sources and reconcile the database.")
    subparsers.add_parser("export", help="Export the database to the .ics feed.")
    subparsers.add_parser("run", help="Harvest then export (default).")
    return parser


def _build_fetcher(args: argparse.Namespace, cfg: AppConfig):
    if args.offline_fixture:
        return FixtureFetcher(args.offline_fixture)
    return HttpxFetcher(
        user_agent=cfg.general.user_agent,
        timeout_seconds=cfg.harvest.request_timeout_seconds,
        max_content_bytes=cfg.harvest.max_content_bytes,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"

    try:
        cfg = load_config(args.config)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    db_path = Path(args.database) if args.database else cfg.database_path
    output_path = Path(args.output) if args.output else cfg.output_path

    repo = Repository.connect(db_path)
    fetcher = _build_fetcher(args, cfg)
    exit_code = 0
    try:
        if command in ("harvest", "run"):
            llm_client = create_llm_client_from_env(cfg.llm.default_model) if cfg.llm.enabled else None
            if cfg.llm.enabled and llm_client is None and args.verbose:
                print(
                    "[harvest] note: OPENAI_API_KEY not set - LLM fallback extraction is disabled "
                    "for this run.",
                    file=sys.stderr,
                )
            results = harvest_all(cfg, fetcher, repo, llm_client, source_ids=args.source)
            for result in results:
                error_suffix = f" error={result.error}" if result.error else ""
                print(
                    f"[harvest] source={result.source_id} coverage={result.coverage.value} "
                    f"events_seen={result.events_seen} {result.reconcile_summary}{error_suffix}"
                )
                if result.error:
                    exit_code = 1

        if command in ("export", "run"):
            events = repo.list_all_events()
            written_path = export_calendar(events, cfg.general.domain, output_path)
            print(f"[export] wrote {len(events)} event(s) to {written_path}")
    finally:
        close = getattr(fetcher, "close", None)
        if callable(close):
            close()
        repo.close()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

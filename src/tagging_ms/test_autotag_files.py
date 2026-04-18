from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .service import StandaloneTaggingService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run StandaloneTaggingService.autotag_files() without the FastAPI layer."
    )
    parser.add_argument(
        "file_paths", nargs="+", help="Audio files to match and optionally retag"
    )
    parser.add_argument(
        "--output-dir",
        help="Optional directory for copied tagged files. If omitted, tags are written in place.",
    )
    parser.add_argument(
        "--track-match-threshold",
        type=float,
        default=0.4,
        help="Minimum similarity score for assigning a matched track.",
    )
    parser.add_argument(
        "--search-limit",
        type=int,
        default=10,
        help="Maximum number of MusicBrainz search results to inspect per file.",
    )
    parser.add_argument(
        "--no-write-tags",
        action="store_true",
        help="Do not write tags; only print match results.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    file_paths = [str(Path(path).expanduser().resolve()) for path in args.file_paths]
    output_dir = (
        str(Path(args.output_dir).expanduser().resolve()) if args.output_dir else None
    )

    service = StandaloneTaggingService()
    results = service.autotag_files(
        file_paths=file_paths,
        output_dir=output_dir,
        track_match_threshold=args.track_match_threshold,
        write_tags=not args.no_write_tags,
        search_limit=args.search_limit,
    )
    print(json.dumps([asdict(result) for result in results], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

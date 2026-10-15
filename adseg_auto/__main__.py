"""python -m adseg_auto --manifest INPUT --output NEW_DIR --provider replay --replay FILE"""

import argparse
from pathlib import Path
import sys

from .pipeline import Config, run
from .providers import HTTPConfig, HTTPProvider, OfflineProvider, ReplayProvider
from .util import encoded, load


def main(argv=None):
    parser = argparse.ArgumentParser(description="Auditable Chinese broadcast unit segmentation")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True, help="must not exist, including an empty directory")
    parser.add_argument("--provider", choices=("offline", "replay", "http"), default="offline")
    parser.add_argument("--replay", help="explicit adseg-replay-v1 JSON file")
    parser.add_argument("--config", help="algorithm Config fields as JSON")
    parser.add_argument("--http-config", help="HTTPConfig fields as JSON; never place credentials in this file")
    args = parser.parse_args(argv)
    try:
        if Path(args.output).exists():
            parser.error("output already exists; choose a new run directory")
        config = Config(**load(args.config)) if args.config else Config()
        if args.provider == "replay":
            if not args.replay:
                parser.error("--provider replay requires --replay FILE")
            provider = ReplayProvider(args.replay)
        elif args.provider == "http":
            if not args.http_config:
                parser.error("--provider http requires --http-config FILE")
            provider = HTTPProvider(HTTPConfig(**load(args.http_config)))
        else:
            provider = OfflineProvider()
        summary = run(args.manifest, args.output, provider, config)
    except (ValueError, TypeError, OSError):
        # Avoid echoing paths, server strings, malformed file content or secrets.
        print("adseg_auto: invalid configuration/input or unavailable output; check documented schemas. No prior run is overwritten.", file=sys.stderr)
        return 2
    print(encoded(summary).decode("utf-8"))
    return 1 if summary["technical_failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

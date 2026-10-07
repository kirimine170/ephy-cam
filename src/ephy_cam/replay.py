"""Offline import and deterministic evidence replay CLI (no device imports)."""

import argparse
import json
from pathlib import Path
import sys

from .inspection_bundle import BundleError, create_bundle, replay_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path)
    parser.add_argument("--capture-dir", type=Path)
    parser.add_argument("--output", type=Path, help="new directory in an existing trusted parent")
    parser.add_argument("--bundle", type=Path, help="verify an existing bundle without writing")
    parser.add_argument("--manifest-sha256", help="independent manifest digest for replay")
    for field in ("session", "task", "sample"):
        parser.add_argument("--expect-" + field + "-id", required=True)
    args = parser.parse_args(argv)
    importing = args.session is not None and args.capture_dir is not None and args.output is not None
    if args.bundle:
        if any((args.session, args.capture_dir, args.output)):
            parser.error("--bundle cannot be combined with import arguments")
    elif not importing or args.manifest_sha256:
        parser.error("import requires --session --capture-dir --output; digest applies to --bundle")
    identity = {field + "_id": getattr(args, "expect_" + field + "_id") for field in ("session", "task", "sample")}
    try:
        if args.bundle:
            report = replay_bundle(args.bundle, expected_identity=identity, expected_manifest_sha256=args.manifest_sha256)
        else:
            report = create_bundle(args.session, args.capture_dir, args.output, expected_identity=identity)
    except BundleError as error:
        print(json.dumps({"execution_status": "error", "reason": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

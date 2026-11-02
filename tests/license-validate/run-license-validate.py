#!/usr/bin/python3
# /// script
# dependencies = [ ]
# ///

import argparse
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(Path(__file__).name)


def main(args: argparse.Namespace) -> None:
    logger.info(f"Running: license-validate -v --spec {args.spec_file}")
    subprocess.run(
        ["license-validate", "-v", "--spec", args.spec_file],
        check=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Simple wrapper for license-validate. Can also pass variables via environment variables."
    )
    parser.add_argument(
        "--spec-file",
        help="Spec file to check.",
        default=os.environ.get("SPEC_FILE"),
    )

    args = parser.parse_args()
    try:
        main(args)
    except (subprocess.CalledProcessError, SystemExit):
        logger.error("license-validate failed!")
        raise SystemExit(1)
    except Exception as exc:
        logger.error("Unexpected failure", exc_info=exc)
        raise SystemExit(2)

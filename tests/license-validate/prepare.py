#!/usr/bin/python3
# /// script
# dependencies = []
# ///

import logging
import subprocess
from pathlib import Path

from utils import TestEnv

logger = logging.getLogger(Path(__file__).name)

if __name__ == "__main__":
    try:
        env = TestEnv.from_env_variables()
        # Try to get the spec file
        env.spec_file
    except SystemExit:
        raise
    except subprocess.CalledProcessError:
        logger.error("Preparation failed!")
        raise SystemExit(1)
    except Exception as exc:
        logger.error("Unexpected failure", exc_info=exc)
        raise SystemExit(2)

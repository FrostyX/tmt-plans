#!/usr/bin/python3
# /// script
# dependencies = [ ]
# ///

import argparse
import os
import re
import shutil
import sys
import subprocess
from pathlib import Path


def main(args: argparse.Namespace) -> None:
    """
    Prepare for rpmlint from a dist-git
    """
    args.env_file: Path

    # Get the basic build information from koji
    result = subprocess.run(
        [
            "koji",
            "taskinfo",
            "-v",
            args.koji_task_id,
        ],
        capture_output=True,
        text=True,
    )
    task_info = result.stdout
    task_error = result.stderr
    print(f"Task info output:\n{task_info}\nTask error:\n{task_error}")
    source_match_obj = re.search(r"Source:\s*(.*)", task_info)
    if source_match_obj is None:
        print(
            "Error: Could not find 'Source:' in koji taskinfo output. Maybe a 500 error? Please retry."
        )
        sys.exit(1)
    source = source_match_obj.group(1)
    source_match = re.search(r"git\+(?P<url>.*)#(?P<ref>.*)", source)
    repo_url = source_match.group("url")
    repo_ref = source_match.group("ref")

    # Clone the dist-git used in the build
    dist_git_path: Path = args.workdir / "dist-git"
    subprocess.run(["git", "clone", repo_url, dist_git_path])
    subprocess.run(["git", "checkout", "-d", repo_ref], cwd=dist_git_path)

    # Find any rplintrc files
    rc_files = list(dist_git_path.glob("*.rpmlintrc"))
    if len(rc_files) > 1:
        print("Warn: More than 1 rpmlintrc file found")
    if rc_files:
        print("Found rpmlintrc file")
        with args.env_file.open("a") as f:
            f.write(f"RPMLINT_RC_FILE={rc_files[0]}\n")
    toml_file = dist_git_path / "rpmlint.toml"
    if toml_file.exists():
        print("Found rpmlint.toml file")
        with args.env_file.open("a") as f:
            f.write(f"RPMLINT_TOML_FILE={toml_file}\n")

    # Copy packit config if present
    for name in ["packit.toml", "packit.yaml", ".packit.yaml"]:
        packit_file = dist_git_path / name
        if packit_file.exists():
            shutil.copy(packit_file, args.workdir)
            break

    # Find the files to lint
    spec_files = list(dist_git_path.glob("*.spec"))
    if len(spec_files) > 1:
        print("Warn: More than 1 spec file found")
    if spec_files:
        with args.env_file.open("a") as f:
            f.write(f"SPEC_FILE={spec_files[0]}\n")
    else:
        print("Warn: No spec file found?")
    subprocess.run(
        ["koji", "download-task", args.koji_task_id],
        cwd=args.workdir,
    )
    with args.env_file.open("a") as f:
        f.write(f"RPM_FILES={args.workdir}/*.rpm\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--koji-task-id", default=os.environ.get("KOJI_TASK_ID"))
    parser.add_argument(
        "--workdir",
        type=Path,
        default=os.environ.get("TMT_PLAN_DATA", "."),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=os.environ.get("TMT_PLAN_ENVIRONMENT_FILE", ".env"),
    )

    args = parser.parse_args()
    main(args)

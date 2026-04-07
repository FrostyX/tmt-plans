#!/usr/bin/python3

import sys
import argparse
import os
import subprocess
import shutil
from pathlib import Path
from enum import Enum
import json
import yaml
import utils
import tomli_w

CI_CONFIG_SECTION = "fedora-review"

# Expose these to the users
FEDORA_REVIEW_RESULTS = [
    "fedora-review.log.gz",
    "files.dir",
    "licensecheck.txt",
    "review.json",
    "review.txt",
    "rpmlint.txt",
]


class Result(Enum):
    INFO = "info"
    FAIL = "fail"
    PASS = "pass"


def dump_results_yaml(issues: int, skipped: int):
    """
    https://tmt.readthedocs.io/en/stable/spec/results.html
    """
    result = Result.FAIL if issues else Result.PASS
    data = [
        {
            "name": "/",
            "result": result.value,
            "note": [
                f"{skipped} skipped",
                f"{issues} issues",
            ],
            "log": ["viewer.html", "fedora-review.toml"] + FEDORA_REVIEW_RESULTS,
        }
    ]
    path = os.path.join(os.environ.get("TMT_TEST_DATA"), "results.yaml")
    print(f"Creating: {path}")
    with open(path, "w+") as fp:
        yaml.dump(data, fp)


def copy_fedora_review_results(spec_file, workdir):
    """
    Copy fedora-review logs and results to the result directory
    """
    package_name = Path(spec_file).stem
    fedora_review_resultdir = workdir / f"review-{package_name}"
    test_resultdir = Path(os.environ["TMT_TEST_DATA"])
    print(os.listdir(fedora_review_resultdir))
    for name in FEDORA_REVIEW_RESULTS:
        src = fedora_review_resultdir / name
        dst = test_resultdir / name
        print(src)
        if src.exists():
            print(f"Copying {name} to the test results")
            shutil.copy(src, dst)


def copy_viewer_html():
    """
    Copy viewer.html from plan data to the result directory
    """
    viewer = "viewer.html"
    print(f"Copying {viewer} to the test results")
    shutil.copy(viewer, Path(os.environ["TMT_TEST_DATA"]) / viewer)


def copy_mock_fedora_ci_toml():
    """
    Copy a mock fedora-ci.toml to the plan data directory
    This is only for development purposes. In production a package either has
    a fedora-ci.toml configuration in its repository or it doesn't. Either way,
    we don't want to copy it from anywhere else.
    """
    filename = "fedora-ci.toml"
    print(f"Copying {filename} to the plan data")
    dst = Path(os.environ["TMT_PLAN_DATA"]) / "dist-git" / filename
    shutil.copy(filename, dst)


def copy_data_into_data():
    """
    There is a weird bug that we discovered with @LecrisUT. For some reason,
    when a plan has `result: custom`, the `viewer.html` stops rendering in
    Testing Farm. It is because for some reason, Oculus starts looking for it
    in `data/data/viewer.html` instead of just `data/viewer.html`.
    This is IMHO a bug but either way, until it gets resolved, we can copy the
    data there as well.
    See https://gitlab.com/testing-farm/general/-/work_items/111
    """
    shutil.copytree(
        Path(os.environ["TMT_TEST_DATA"]),
        Path(os.environ["TMT_TEST_DATA"]) / "data",
    )


def fedora_review(spec_file, workdir):
    """
    Run fedora-review
    """
    env = os.environ.copy()
    env["REVIEW_NO_MOCKGROUP_CHECK"] = "true"

    name = Path(spec_file).stem
    config = str(args.workdir / "fedora-review.toml")
    cmd = ["fedora-review", "--config", config, "--prebuilt", "-n", name]
    print(f"Running: {" ".join(cmd)}")
    proc = subprocess.run(
        cmd,
        cwd=workdir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    print(proc.stdout.decode("utf-8"))
    print(proc.stderr.decode("utf-8"))
    if proc.returncode:
        raise RuntimeError("The fedora-review command failed")

    path = os.path.join(workdir, "review-" + name, "review.json")
    if not os.path.exists(path):
        raise RuntimeError(f"Result JSON doesn't exist: {path}")
    print("Result: {0}".format(path))

    with open(path, "r") as fp:
        review = json.load(fp)
    return review


def skip_checks(config):
    skip_for_all = [
        # A package with this name obviously already exists in the Fedora
        # repositories and this is that package. Check for a name conflict only
        # makes sense during the initial Package Review Process, but it does't
        # make any sense for CI on existing packages.
        "CheckNoNameConflict",
        # The licensecheck implementation within the `fedora-review` tool is
        # not up to modern standards and produces far to many false-positives
        # which would be too annoying for our users. We discussed this with
        # @msuchy and agreed that it would be better to have a dedicate service
        # for checking licenses. It should be based around ScanCode Toolkit,
        # FOSSology, or anything that succeeds them.
        "CheckLicensInDoc",
        "CheckLicenseField",
    ]
    skip_for_package = []
    if exclude := config.get("exclude"):
        skip_for_package = [x.strip() for x in exclude.split(",")]
        skip_for_package = [x for x in skip_for_package if x]
    return skip_for_all + skip_for_package


def parse_fedora_review_toml(workdir: Path):
    """
    Parse the fedora-review.toml out of the fedora-ci.toml
    """
    dist_git_path = args.workdir / "dist-git"
    if config := utils.get_config(dist_git_path, CI_CONFIG_SECTION):
        return config["toml"]
    return {}


def dump_fedora_review_config(fedora_review_config):
    name = "fedora-review.toml"
    path: Path = args.workdir / name
    with path.open("wb") as fp:
        tomli_w.dump(fedora_review_config, fp)
    print(f"Copying {name} to the test results")
    shutil.copy(path, Path(os.environ["TMT_TEST_DATA"]) / name)


def main(args: argparse.Namespace) -> None:
    """
    Run fedora-review plan
    """
    if not args.spec_file:
        raise RuntimeError("No spec file provided")

    if not args.rpm_files:
        raise RuntimeError("No RPM files provided")

    # At this point, the RPM packages are already downloaded in `args.workdir`,
    # we just need to copy the .spec next to them
    shutil.copy(args.spec_file, args.workdir)

    # Uncomment if needed for development purposes
    # copy_mock_fedora_ci_toml()

    # Parse the `fedora-review config` aout of the `fedora-ci.toml`, update
    # the list of excluded checks and save it as `fedora-review.toml`.
    config = parse_fedora_review_toml(args.workdir)
    skip = skip_checks(config)
    config["exclude"] = ",".join(skip)
    dump_fedora_review_config(config)
    print(f"Skipping these checks: {skip}")

    review = fedora_review(args.spec_file, args.workdir)
    issues = review.get("issues", [])

    dump_results_yaml(len(issues), len(skip))
    copy_fedora_review_results(args.spec_file, args.workdir)
    copy_viewer_html()
    copy_data_into_data()

    print(f"Skipped {len(skip)} issues")
    print(f"Found {len(issues)} issues")
    if issues:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Simple wrapper for fedora-review. "
            "Can also pass variables via environment variables."
        )
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=os.environ.get("TMT_PLAN_DATA", "."),
    )
    parser.add_argument(
        "--spec-file",
        help="Spec file to check.",
        default=os.environ.get("SPEC_FILE"),
    )
    parser.add_argument(
        "--rpm-files",
        help="RPM files to check. Can be wildcard.",
        default=os.environ.get("RPM_FILES"),
    )

    args = parser.parse_args()
    try:
        main(args)
    except RuntimeError as ex:
        print(ex, file=sys.stderr)
        sys.exit(1)

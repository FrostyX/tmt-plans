#!/usr/bin/python3

import sys
import argparse
import os
import subprocess
import shutil
from pathlib import Path
from contextlib import suppress
from enum import Enum
import json
import yaml

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
            "log": ["viewer.html", "skipped-checks.json"] + FEDORA_REVIEW_RESULTS,
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


def copy_mock_packit_yaml():
    """
    Copy a mock .packit.yaml to the plan data directory
    """
    filename = ".packit.yaml"
    print(f"Copying {filename} to the plan data")
    shutil.copy(filename, Path(os.environ["TMT_PLAN_DATA"]) / filename)


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
    cmd = ["fedora-review", "--prebuilt", "-n", name]
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


def count_issues(review, skip):
    print(f"Skipping these checks: {skip}")
    issues = review.get("issues", [])
    issues = [x for x in issues if x["name"] not in skip]
    return len(issues)


def skip_checks(packit_yaml):
    skip_for_all = [
        # TODO why?
        "CheckNoNameConflict",

        # TODO why?
        "CheckLicensInDoc",

        # TODO why?
        "CheckLicenseField",
    ]
    skip_for_package = []
    if "fedora_review" in packit_yaml:
        skip_for_package = packit_yaml["fedora_review"].get("skip", [])
    return skip_for_all + skip_for_package


def dump_skipped_checks(checks: list[str]) -> None:
    path = "./skipped-checks.json"
    with open(path, "w+") as fp:
        json.dump(checks, fp)


def parse_packit_yaml(workdir: Path):
    accepted_filenames = [
        "packit.yaml",
        ".packit.yaml",
        "packit.yml",
        ".packit.yml",
    ]
    for filename in accepted_filenames:
        packit_yaml_path = os.path.join(workdir, filename)
        print(f"Looking for packit configuration: {packit_yaml_path}")
        with suppress(FileNotFoundError), open(packit_yaml_path, "r") as fp:
            print("Packit configuration found")
            return yaml.safe_load(fp)
    return {}


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

    review = fedora_review(args.spec_file, args.workdir)
    copy_mock_packit_yaml()

    packit_yaml = parse_packit_yaml(args.workdir)
    skip = skip_checks(packit_yaml)
    dump_skipped_checks(skip)

    issues = count_issues(review, skip)
    dump_results_yaml(issues, len(skip))
    copy_fedora_review_results(args.spec_file, args.workdir)
    copy_viewer_html()
    copy_data_into_data()

    print(f"Skipped {len(skip)} issues")
    print(f"Found {issues} issues")
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

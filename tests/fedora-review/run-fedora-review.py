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


def dump_results_yaml(issues: int):
    """
    https://tmt.readthedocs.io/en/stable/spec/results.html
    """
    result = Result.FAIL if issues else Result.PASS
    data = [
        {
            "name": "/",
            "result": result.value,
            "note": [f"{issues} issues"],
            "log": ["viewer.html"] + FEDORA_REVIEW_RESULTS,
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


def find_srpm(workdir: Path) -> Path:
    """
    Find a SRPM package among other data
    """
    srpms = list(workdir.glob("*.src.rpm"))
    if not srpms:
        raise RuntimeError(f"No SRPM found in {workdir}")
    if len(srpms) > 1:
        raise RuntimeError(f"More than one SRPM found in {workdir}: {srpms}")
    return srpms[0]


def rpm_disttag(path: Path) -> str | None:
    """
    Find out the disttag value for a RPM or SRPM package.
    """
    ts = rpm.TransactionSet()
    ts.setVSFlags(rpm._RPMVSF_NOSIGNATURES)
    with path.open("rb") as fp:
        header = ts.hdrFromFdno(fp)
    # For some reason, `rpm.RPMTAG_DISTTAG` is `None` for all the packages that
    # I tested, so we are parsing the value out of the filename instead.
    if release := header[rpm.RPMTAG_RELEASE]:
        return release.rsplit(".", 1)[-1]
    return None


def fedora_review(spec_file, workdir):
    """
    Run fedora-review
    """
    env = os.environ.copy()
    env["REVIEW_NO_MOCKGROUP_CHECK"] = "true"

    name = Path(spec_file).stem
    cmd = ["fedora-review", "--prebuilt", "-n", name]

    # There is a weird disttag parsing bug in the `fedora-review` tool. When
    # the results contain RPM packages with different release numbers, e.g.
    # `nss-3.127.0-1.fc44.x86_64.rpm` and `nspr-4.39.0-4.fc44.x86_64.rpm``,
    # it fails to parse the dist tag even though it is the same fc44 for both.
    # https://forge.fedoraproject.org/packaging/FedoraReview/src/commit/7aeb863ec28c48d22280f9d60312c2e990a04512/src/FedoraReview/mock.py#L62-L71
    if disttag := rpm_disttag(find_srpm(workdir)):
        cmd.extend(["--define", f"DISTTAG={disttag}"])

    print(f"Running: {" ".join(cmd)}")
    subprocess.run(cmd, cwd=workdir, env=env, check=True)

    path = os.path.join(workdir, "review-" + name, "review.json")
    if not os.path.exists(path):
        raise RuntimeError(f"Result JSON doesn't exist: {path}")
    print("Result: {0}".format(path))

    with open(path, "r") as fp:
        review = json.load(fp)
    return review


def count_issues(review):
    issues = review.get("issues", [])
    return len(issues)


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
    issues = count_issues(review)
    dump_results_yaml(issues)
    copy_fedora_review_results(args.spec_file, args.workdir)
    copy_viewer_html()
    copy_data_into_data()

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

#!/usr/bin/python3

import sys
import argparse
import os
import subprocess
import shutil
import glob
from pathlib import Path
from enum import Enum
import logging
import json
import yaml
import utils
import tomli_w

logging.basicConfig(level="INFO")
log = logging.getLogger(Path(__file__).name)

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
    log.info("Creating: %s", path)
    with open(path, "w+") as fp:
        yaml.dump(data, fp)


def copy_fedora_review_results(spec_file, workdir):
    """
    Copy fedora-review logs and results to the result directory
    """
    package_name = Path(spec_file).stem
    fedora_review_resultdir = workdir / f"review-{package_name}"
    test_resultdir = Path(os.environ["TMT_TEST_DATA"])
    log.info(os.listdir(fedora_review_resultdir))
    for name in FEDORA_REVIEW_RESULTS:
        src = fedora_review_resultdir / name
        dst = test_resultdir / name
        log.info(src)
        if src.exists():
            log.info("Copying %s to the test results", name)
            shutil.copy(src, dst)


def copy_viewer_html():
    """
    Copy viewer.html from plan data to the result directory
    """
    viewer = "viewer.html"
    log.info("Copying %s to the test results", viewer)
    shutil.copy(viewer, Path(os.environ["TMT_TEST_DATA"]) / viewer)


def copy_mock_fedora_ci_toml():
    """
    Copy a mock fedora-ci.toml to the plan data directory
    This is only for development purposes. In production a package either has
    a fedora-ci.toml configuration in its repository or it doesn't. Either way,
    we don't want to copy it from anywhere else.
    """
    filename = "fedora-ci.toml"
    log.info("Copying %s to the plan data", filename)
    dst = Path(os.environ["TMT_PLAN_DATA"]) / "dist-git" / filename
    shutil.copy(filename, dst)


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
    nvr = path.name.rsplit(".", 2)[0]
    release = nvr.rsplit("-", 2)[-1]
    return release.rsplit(".", 1)[-1]


def fedora_review(spec_file, workdir):
    """
    Run fedora-review
    """
    env = os.environ.copy()
    env["REVIEW_NO_MOCKGROUP_CHECK"] = "true"

    config = str(workdir / "fedora-review.toml")
    name = Path(spec_file).stem
    # cmd = ["fedora-review", "--config", config, "--prebuilt", "-n", name]

    cmd = ["fedora-review", "--config", config, "--prebuilt", "-n", name, "-r"]

    # cmd = ["fedora-review", "--config", config, "--copr-build", "11081204"]

    # There is a weird disttag parsing bug in the `fedora-review` tool. When
    # the results contain RPM packages with different release numbers, e.g.
    # `nss-3.127.0-1.fc44.x86_64.rpm` and `nspr-4.39.0-4.fc44.x86_64.rpm``,
    # it fails to parse the dist tag even though it is the same fc44 for both.
    # https://forge.fedoraproject.org/packaging/FedoraReview/src/commit/7aeb863ec28c48d22280f9d60312c2e990a04512/src/FedoraReview/mock.py#L62-L71
    disttag = rpm_disttag(find_srpm(workdir))
    cmd.extend(["--define", f"DISTTAG={disttag}"])

    log.info("Running: %s", " ".join(cmd))
    subprocess.run(
        cmd,
        cwd=workdir,
        env=env,
        check=True,
    )

    path = os.path.join(workdir, "review-" + name, "review.json")
    if not os.path.exists(path):
        raise RuntimeError(f"Result JSON doesn't exist: {path}")
    log.info("Result: %s", path)

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
    dist_git_path = workdir / "dist-git"
    if config := utils.get_config(dist_git_path, CI_CONFIG_SECTION):
        return config
    return {}


def dump_fedora_review_config(workdir: Path, fedora_review_config):
    name = "fedora-review.toml"
    path: Path = workdir / name
    with path.open("wb") as fp:
        tomli_w.dump(fedora_review_config, fp)
    log.info("Copying %s to the test results", name)
    shutil.copy(path, Path(os.environ["TMT_TEST_DATA"]) / name)


def main(args: argparse.Namespace) -> None:
    """
    Run fedora-review plan
    """
    # if not args.spec_file:
    #     raise RuntimeError("No spec file provided")

    # if not args.rpm_files:
    #     raise RuntimeError("No RPM files provided")

    rpm_files = glob.glob(args.rpm_files)
    # if not rpm_files:
    #     raise RuntimeError(f"No RPM files match {args.rpm_files}")

    # Copy the downloaded RPM packages and spec file into the review workdir.
    workdir = utils.get_workdir()
    for rpm_file in rpm_files:
        src = Path(rpm_file)
        dst = workdir / src.name
        if dst.exists() and src.samefile(dst):
            continue
        log.info("Copying %s to %s", src, dst)
        shutil.copy(src, dst)
    shutil.copy(args.spec_file, workdir)

    # Uncomment if needed for development purposes
    # copy_mock_fedora_ci_toml()

    # Parse the `fedora-review config` aout of the `fedora-ci.toml`, update
    # the list of excluded checks and save it as `fedora-review.toml`.
    config = parse_fedora_review_toml(workdir)
    skip = skip_checks(config)
    config["exclude"] = ",".join(skip)
    dump_fedora_review_config(workdir, config)
    log.info("Skipping these checks: %s", skip)

    review = fedora_review(args.spec_file, workdir)
    issues = review.get("issues", [])

    dump_results_yaml(len(issues), len(skip))
    copy_fedora_review_results(args.spec_file, workdir)
    copy_viewer_html()

    log.info("Skipped %s issues", len(skip))
    log.error("Found %s issues", len(issues))
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
    except subprocess.CalledProcessError:
        log.error("Fedora-review failed!")
        sys.exit(1)
    except RuntimeError as ex:
        log.error(str(ex))
        log.error("Fedora-review failed!")
        sys.exit(1)
    except Exception as ex:
        log.error("Unexpected error!", exc_info=ex)
        sys.exit(2)

#!/usr/bin/python3

import sys
import os
import subprocess
import shutil
from pathlib import Path
import logging
import json
import tomli_w
from typing import Any

from utils import Result, TestEnv

DIR = Path(__file__).parent
log = logging.getLogger(Path(__file__).name)

CI_CONFIG_SECTION = "fedora-review"
FEDORA_REVIEW_TOML = "fedora-review.toml"

# Expose these to the users
FEDORA_REVIEW_RESULTS = [
    "fedora-review.log.gz",
    "files.dir",
    "licensecheck.txt",
    "review.json",
    "review.txt",
    "rpmlint.txt",
]


def dump_results_yaml(test_env: TestEnv, issues: int, skipped: int):
    """
    https://tmt.readthedocs.io/en/stable/spec/results.html
    """
    test_env.main_result.result = Result.FAIL if issues else Result.PASS
    test_env.main_result.note = [
        f"{skipped} skipped",
        f"{issues} issues",
    ]
    test_env.main_result.add_viewer_html(DIR / "viewer.html")
    test_env.main_result.add_file(test_env.workdir / FEDORA_REVIEW_TOML)
    copy_fedora_review_results(test_env)
    test_env.results.save()


def copy_fedora_review_results(test_env: TestEnv) -> None:
    """
    Copy fedora-review logs and results to the result directory
    """
    fedora_review_resultdir = test_env.workdir / f"review-{test_env.srpm_name}"
    log.info(os.listdir(fedora_review_resultdir))
    for name in FEDORA_REVIEW_RESULTS:
        test_env.main_result.add_file(fedora_review_resultdir / name, missing_ok=True)


def copy_mock_fedora_ci_toml(test_env: TestEnv):
    """
    Copy a mock fedora-ci.toml to the plan data directory
    This is only for development purposes. In production a package either has
    a fedora-ci.toml configuration in its repository or it doesn't. Either way,
    we don't want to copy it from anywhere else.
    """
    filename = "fedora-ci.toml"
    log.info("Copying %s to the plan data", filename)
    dst = test_env.git_dir / filename
    shutil.copy(filename, dst)


def rpm_disttag(path: Path) -> str | None:
    """
    Find out the disttag value for a RPM or SRPM package.
    """
    nvr = path.name.rsplit(".", 2)[0]
    release = nvr.rsplit("-", 2)[-1]
    return release.rsplit(".", 1)[-1]


def fedora_review(test_env: TestEnv) -> dict[str, Any]:
    """
    Run fedora-review
    """
    env = os.environ.copy()
    env["REVIEW_NO_MOCKGROUP_CHECK"] = "true"

    config = str(test_env.workdir / FEDORA_REVIEW_TOML)
    name = test_env.srpm_name
    cmd = ["fedora-review", "--config", config, "--prebuilt", "-n", name]

    # There is a weird disttag parsing bug in the `fedora-review` tool. When
    # the results contain RPM packages with different release numbers, e.g.
    # `nss-3.127.0-1.fc44.x86_64.rpm` and `nspr-4.39.0-4.fc44.x86_64.rpm``,
    # it fails to parse the dist tag even though it is the same fc44 for both.
    # https://forge.fedoraproject.org/packaging/FedoraReview/src/commit/7aeb863ec28c48d22280f9d60312c2e990a04512/src/FedoraReview/mock.py#L62-L71
    disttag = rpm_disttag(test_env.srpm)
    cmd.extend(["--define", f"DISTTAG={disttag}"])

    log.info("Running: %s", " ".join(cmd))
    subprocess.run(
        cmd,
        cwd=test_env.workdir,
        env=env,
        check=True,
    )

    path = os.path.join(test_env.workdir, "review-" + name, "review.json")
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


def dump_fedora_review_config(test_env: TestEnv, fedora_review_config):
    with (test_env.workdir / FEDORA_REVIEW_TOML).open("wb") as fp:
        tomli_w.dump(fedora_review_config, fp)


def main(test_env: TestEnv) -> None:
    """
    Run fedora-review plan
    """
    if not test_env.spec_file:
        raise RuntimeError("No spec file provided")

    if not test_env.rpms:
        raise RuntimeError("No RPM files provided")

    # At this point, the RPM packages are already downloaded in `args.workdir`,
    # we just need to copy the .spec next to them
    shutil.copy(test_env.spec_file, test_env.workdir)

    # Uncomment if needed for development purposes
    # copy_mock_fedora_ci_toml()

    # Parse the `fedora-review config` aout of the `fedora-ci.toml`, update
    # the list of excluded checks and save it as `fedora-review.toml`.
    config = test_env.get_config(CI_CONFIG_SECTION) or {}
    skip = skip_checks(config)
    config["exclude"] = ",".join(skip)
    dump_fedora_review_config(test_env, config)
    log.info("Skipping these checks: %s", skip)

    review = fedora_review(test_env)
    issues = review.get("issues", [])

    dump_results_yaml(test_env, len(issues), len(skip))

    log.info("Skipped %s issues", len(skip))
    log.error("Found %s issues", len(issues))
    if issues:
        sys.exit(1)


if __name__ == "__main__":
    env = TestEnv.from_env_variables()

    try:
        main(env)
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

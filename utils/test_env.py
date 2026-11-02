from __future__ import annotations

import abc
import functools
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TYPE_CHECKING = False
if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Any

    from .results import Results, TmtResult

# TODO: Make this more configurable
logging.basicConfig(level="INFO")
logger = logging.getLogger("tmt_plans.utils.test_env")

CI_CONFIG_FILES = [
    "fedora-ci.yaml",
    "fedora-ci.yml",
    "fedora-ci.toml",
]

__all__ = [
    "TestEnv",
]


class TestEnv(abc.ABC):
    """
    Container for various test environment information.
    """

    @functools.cached_property
    def results(self) -> Results:
        """
        Main interface to interact with custom tmt test results.
        """
        from .results import Results

        val = Results(self)
        _ = val["/"]
        val.save()
        return val

    @property
    def main_result(self) -> TmtResult:
        """
        Shortcut to main test's results.
        """
        return self.results["/"]

    @functools.cached_property
    def env_file(self) -> Path | None:
        """
        Environment file persistent across tests.
        """
        env_file = os.environ.get("TMT_PLAN_ENVIRONMENT_FILE")
        return Path(env_file) if env_file else None

    def save_env(self, name: str, value: Any) -> None:
        """
        Save an environment variable across tests.
        """
        if not self.env_file:
            return
        with self.env_file.open("a") as f:
            f.write(f"{name}={value!s}\n")

    @functools.cached_property
    def workdir(self) -> Path:
        """
        Get or generate a temporary workdir used across tests.


        Avoid using paths like ``TMT_PLAN_DATA`` because we do not want these to be
        synced back to testing-farm artifact storage.

        We default to ``/var/tmp`` since the default ``/tmp`` may not have enough
        storage. The user would be responsible for sanitizing the workdir in this case.
        """
        workdir = os.environ.get("WORKDIR")
        if not workdir:
            workdir = tempfile.mkdtemp(
                prefix="tmt-test-workdir-",
                dir=os.environ.get("TMPDIR", "/var/tmp"),
            )
            self.save_env("WORKDIR", workdir)
        logger.info(f"Temporary workdir: {workdir}")
        return Path(workdir)

    @functools.cached_property
    def test_data(self) -> Path:
        """
        The ``TMT_TEST_DATA`` path.
        """
        return Path(os.environ.get("TMT_TEST_DATA", "."))

    @functools.cached_property
    def plan_data(self) -> Path:
        """
        The ``TMT_PLAN_DATA`` path.
        """
        return Path(os.environ.get("TMT_PLAN_DATA", "."))

    @property
    @abc.abstractmethod
    def git_dir(self) -> Path:
        """
        Package's git workdir.

        The original spec file and other auxiliary files should be here.
        """

    # TODO: This would not be needed with the tmt artifacts
    @abc.abstractmethod
    def get_rpms(self) -> None:
        """
        Download the rpm artifacts.

        The rpms are expected to be downloaded in the ``workdir`` for now.
        """

    @functools.cached_property
    def spec_file(self) -> Path:
        """
        Original spec file associated with the artifacts.
        """

        if spec_file_env := os.environ.get("SPEC_FILE"):
            return Path(spec_file_env)

        discovered_spec_files = list(self.git_dir.glob("*.spec"))
        if len(discovered_spec_files) > 1:
            logger.warning("More than 1 spec file found, using only the first one.")
        if not discovered_spec_files:
            logger.error("No spec file found?")
            raise SystemExit(1)
        spec_file = discovered_spec_files[0]
        logger.info(f"Spec file: {spec_file}")
        self.save_env("SPEC_FILE", spec_file)
        return spec_file

    @property
    def srpm_name(self) -> str:
        """
        Base name of the pacakge/srpm.
        """
        # Getting the name from the spec file should be less costly route
        return self.spec_file.stem

    @functools.cached_property
    def rpms(self) -> list[Path]:
        """
        Current test's rpm files.
        """
        if rpm_file_env := os.environ.get("RPM_FILES"):
            # In case the `RPM_FILES` are relative, then use workdir as anchor
            # Otherwise it should not matter
            return list(self.workdir.glob(rpm_file_env))

        # TODO: Get the rpms from the artifacts instead
        self.get_rpms()
        self.save_env("RPM_FILES", f"{self.workdir}/*.rpm")
        rpms = list(self.workdir.glob("*.rpm"))
        if not rpms:
            logger.error("No rpms were downloaded!")
            raise SystemExit(1)
        return rpms

    @property
    def srpm(self) -> Path:
        """
        The source rpm file.
        """
        try:
            return next(rpm for rpm in self.rpms if rpm.name.endswith(".src.rpm"))
        except StopIteration:
            logger.error("SRPM file not found!")
            raise SystemExit(1)

    @property
    def binary_rpms(self) -> Iterable[Path]:
        """
        Just the binary rpms.
        """
        for rpm in self.rpms:
            if rpm.name.endswith(".src.rpm"):
                continue
            yield rpm

    @functools.cached_property
    def config_file(self) -> Path | None:
        """
        The fedora-ci config file.
        """
        for ci_file_name in CI_CONFIG_FILES:
            if (ci_file := self.git_dir / ci_file_name).exists():
                logger.info(f"Found config file {ci_file_name}")
                return ci_file
        logger.info("No fedora-ci config files.")
        return None

    @functools.cached_property
    def config(self) -> dict[str, Any] | None:
        """
        Contents of the ``config_file``.
        """
        from ruamel.yaml import YAML

        if sys.version_info < (3, 11):
            import tomli as tomllib
        else:
            import tomllib

        if not self.config_file:
            return None

        with self.config_file.open("rb") as f:
            if self.config_file.suffix == ".toml":
                config = tomllib.load(f)
            elif self.config_file.suffix in [".yaml", ".yml"]:
                config = YAML().load(f)
            else:
                raise AssertionError(
                    "Trying to load a file not listed in CI_CONFIG_FILES"
                )
        if config is None:
            return None
        if not isinstance(config, dict):
            logger.warning(f"Config file is in an unexpected format: {type(config)}")
            return None

        logger.info(f"Config file content:\n{self.config_file.read_text()}")
        return config

    @functools.cache
    def get_config(self, section: str) -> dict[str, Any] | None:
        """
        Get the ``tools.{section}`` section of the ``config``.
        """
        if not self.config:
            return None
        if not (tools := self.config.get("tools")):
            logger.info("No `tools` section found")
            return None
        if not (config := tools.get(section)):
            logger.info(f"No `tools.{section}` section found")
            return None
        return config

    @classmethod
    def from_env_variables(cls) -> TestEnv:
        if copr_build_id := os.environ.get("PACKIT_COPR_BUILD_ID"):
            return CoprTestEnv(int(copr_build_id))
        if koji_task_id := os.environ.get("KOJI_TASK_ID"):
            return KojiTestEnv(koji_task_id)
        logger.warning(
            "Could not determine the environment, assuming environment variables are set."
        )
        return CachedEnv()


class CachedEnv(TestEnv):
    """
    Test environment that has been previously prepared.
    """

    @property
    def git_dir(self) -> Path:
        # TODO: This should have a better path name
        return self.workdir / "dist-git"

    def get_rpms(self) -> None:
        # Nothing to do the rpms should already be there
        pass


class KojiTestEnv(TestEnv):
    """
    Test environment from a koji (scratch) build.
    """

    def __init__(self, koji_task_id: str) -> None:
        self.task_id = koji_task_id

    @functools.cache
    def get_task_info(self) -> str:
        """
        Get the ``koji taskinfo``
        """
        # TODO: Maybe we can parse this better
        result = subprocess.run(
            [
                "koji",
                "taskinfo",
                "-v",
                self.task_id,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        task_info = result.stdout
        task_error = result.stderr
        logger.info(f"Task info output:\n{task_info}\nTask error:\n{task_error}")
        return task_info

    @functools.cached_property
    def dist_git_path(self) -> Path:
        """
        Dist-git path from src.fedoraproject.org.
        """
        if dist_git_path_env := os.environ.get("DIST_GIT_PATH"):
            return Path(dist_git_path_env)

        source_match_obj = re.search(r"Source:\s*(.*)", self.get_task_info())
        if source_match_obj is None:
            logger.error(
                "Could not find 'Source:' in koji taskinfo output. Maybe a 500 error? Please retry."
            )
            raise SystemExit(1)
        source = source_match_obj.group(1)
        source_match = re.search(r"git\+(?P<url>.*)#(?P<ref>.*)", source)
        repo_url = source_match.group("url")
        repo_ref = source_match.group("ref")

        # Clone the dist-git used in the build
        dist_git_path = self.workdir / "dist-git"
        subprocess.run(
            ["git", "clone", repo_url, dist_git_path],
            check=True,
        )
        subprocess.run(
            ["git", "checkout", "-d", repo_ref],
            cwd=dist_git_path,
            check=True,
        )
        self.save_env("DIST_GIT_PATH", dist_git_path)
        return dist_git_path

    def get_rpms(self):
        subprocess.run(
            ["koji", "download-task", self.task_id],
            cwd=self.workdir,
            check=True,
        )

    @property
    def git_dir(self) -> Path:
        return self.dist_git_path


class CoprTestEnv(TestEnv):
    """
    Test environment from a copr build.
    """

    # TODO: Currently a packit job is assumed

    def __init__(self, copr_build_id: int) -> None:
        self.build_id = copr_build_id

    @property
    def git_dir(self) -> Path:
        return self.packit_git_dir

    @functools.cached_property
    def packit_git_dir(self) -> Path:
        """
        Download the git from the packit folder.
        """
        # TODO: This would not be a dist-git, what name to give?
        if dist_git_path_env := os.environ.get("DIST_GIT_PATH"):
            return Path(dist_git_path_env)

        try:
            url = os.environ["PACKIT_TARGET_URL"]
            ref = os.environ["PACKIT_TARGET_SHA"]
        except KeyError:
            logger.error("Did not find expected packit environment variables!")
            raise SystemExit(2)
        dist_git_path = self.workdir / "dist-git"
        subprocess.run(
            ["git", "clone", url, dist_git_path],
            check=True,
        )
        subprocess.run(
            ["git", "checkout", "-d", ref],
            cwd=dist_git_path,
            check=True,
        )
        # TODO: navigate to the actual `specfile_path` of the requested project
        self.save_env("DIST_GIT_PATH", dist_git_path)
        return dist_git_path

    def get_rpms(self) -> None:
        # TODO: This assumes testing-farm environment without multipipeline
        for rpm in Path("/var/share/test-artifacts").glob("*.rpm"):
            shutil.copy(rpm, self.workdir)

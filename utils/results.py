"""
Interface to manipulate tmt custom results
"""

from __future__ import annotations

import datetime
import enum
import functools
import logging
import shutil
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

TYPE_CHECKING = False
if TYPE_CHECKING:
    from .test_env import TestEnv

__all__ = [
    "Result",
    "Results",
]

logger = logging.getLogger("tmt_plans.utils.results")


class Result(enum.IntEnum):
    """
    Result states supported by tmt.

    See https://tmt.readthedocs.io/en/stable/spec/results.html
    """

    PENDING = enum.auto()
    SKIP = enum.auto()
    PASS = enum.auto()
    INFO = enum.auto()
    WARN = enum.auto()
    FAIL = enum.auto()
    ERROR = enum.auto()

    # We want IntEnum to be able to easily compare, but would also be nice to
    # initialize via `ResultState("pass")`, so we need this `_missing_` for that.
    @classmethod
    def _missing_(cls, value: Any) -> Result | None:
        if isinstance(value, str):
            try:
                return cls[value.upper()]
            except KeyError:
                pass
        return None

    def to_raw(self) -> str:
        """
        Convert to basic python types to be saved.
        """
        return str(self).lower()


class Results(Mapping[str, "TmtResult"]):
    """
    Main entry point for managing custom tmt results.

    The recommended way to interact with this is through :py:attr:`utils.TestEnv.results`.

    .. code-block:: python

        test_env = TestEnv.from_env_variables()
        test_env.main_result.result = "pass"
        test_env.main_result.log.append("log.txt")
        test_env.results.save()

    You can also create subresults or other sibling test results

    .. code-block:: python

        test_env.main_result.subresult["sub-test1"] = "pass"
        sub_test2 = test_env.main_result.subresult["sub-test2"]
        sub_test2.result = "fail"
        test_env.results["/sibling"] = "warn"
        test_env.results.save()

    Things to consider:
    * Any non-existing ``TmtResult`` is created as soon as it is requested
    * Saving the results has to be done manually by invoking ``save`` from here or the ``TmtResult``
    * If a test has ``subresult``, the main test result is derived automatically as the highest
      ``ResultState`` in the ``subresult``

    By default, tests do not have durations, and in order to add one, you have to measure it manually as

    .. code-block:: python

        sub_test = test_env.results["/sub-test"]
        sub_test.start()
        do_test()
        sub_test.finish()

    or you can use the :py:attr:`auto_stopwatch`

    .. code-block:: python

        test_env.results.auto_stopwatch = True
        sub_test = test_env.results["/sub-test"]
        do_test()
        sub_test.result = "pass"
    """

    _results: dict[str, TmtResult]
    _parent: TmtResult | TestEnv

    auto_stopwatch: bool = False
    """Trigger a test's stopwatch as soon as a test is created and when the """

    def __init__(self, parent: TmtResult | TestEnv) -> None:
        self._results = {}
        self._parent = parent
        if isinstance(parent, TmtResult):
            self.auto_stopwatch = parent._parent.auto_stopwatch

    def __getitem__(self, key: str, /) -> TmtResult:
        if key not in self._results:
            self._results[key] = TmtResult(name=key, _parent=self)
            if self.auto_stopwatch:
                self._results[key].start()
        return self._results[key]

    def __setitem__(self, key: str, value: TmtResult | str | Result) -> None:
        if isinstance(value, TmtResult):
            if key != value.name:
                raise ValueError(
                    f"The key and result name do not match: {key} != {value.name}"
                )
            self._results[key] = value
        elif isinstance(value, str):
            result = Result(value)
            self._results[key].result = result
        elif isinstance(value, Result):
            self._results[key].result = value
        else:
            raise TypeError(f"Unsupported value type: {value} [{type(value)}]")

    def __len__(self) -> int:
        return len(self._results)

    def __iter__(self) -> Iterator[str]:
        yield from self._results

    @functools.cached_property
    def test_data(self) -> Path:
        """
        The ``TMT_TEST_DATA`` path.
        """
        parent = self._parent
        while isinstance(parent, TmtResult):
            # Results -> TmtResult -> Results
            parent = parent._parent._parent
        return parent.test_data

    def save(self) -> None:
        """
        Save the current state of the results to the ``results.yaml``.
        """
        from ruamel.yaml import YAML

        with (self.test_data / "results.yaml").open("w") as f:
            YAML(typ="safe").dump(self.to_raw(), f)

    def to_raw(self) -> list[Any]:
        """
        Convert to basic python types to be saved.
        """
        # Note: doing this manually to keep the dependencies minimal
        return [result.to_raw() for result in self._results.values()]


@dataclass
class StopWatch:
    start: datetime.datetime = field(default_factory=datetime.datetime.now)
    end: datetime.datetime | None = None

    def format(self) -> str | None:
        """
        See helper duration format from ``tmt.utils``
        """
        if not self.end:
            return None
        duration = self.end - self.start
        counter = int(duration.total_seconds())

        hours, counter = divmod(counter, 3600)
        minutes, seconds = divmod(counter, 60)

        return f"{hours:02}:{minutes:02}:{seconds:02}"


@dataclass(kw_only=True)
class TmtResult:
    """
    Subset of tmt result that we will use.

    See https://tmt.readthedocs.io/en/stable/spec/results.html
    """

    name: str
    _result: Result = Result.PENDING
    log: list[str] = field(default_factory=list)
    note: list[str] = field(default_factory=list)
    subresult: Results = field(init=False)
    duration: str | None = None

    _parent: Results
    _stopwatch: StopWatch | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.subresult = Results(self)

    @property
    def result(self) -> Result:
        return self._result

    @result.setter
    def result(self, value: str | Result) -> None:
        if isinstance(value, str):
            self._result = Result(value)
        elif isinstance(value, Result):
            self._result = value
        else:
            raise ValueError(f"Unsupported value {value} [{type(value)}]")
        if self._parent.auto_stopwatch:
            self.finish()

    def to_raw(self) -> dict[str, Any]:
        """
        Convert to basic python types to be saved.
        """
        # Note: doing this manually to keep the dependencies minimal
        # All fields are in the dataclass, we just need to clean it up
        raw_value = asdict(self)
        for key, value in raw_value.items():
            if key.startswith("_"):
                # Remove private fields
                del raw_value[key]
                # But try to keep fields like `_result`
                field_key = key.removeprefix("_")
                if maybe_field := getattr(self, field_key, None):
                    raw_value[field_key] = maybe_field
            elif hasattr(value, "to_raw"):
                # Handle custom classes that need to be converted
                raw_value[key] = value.to_raw()
            elif value is None:
                # Remove any missing fields
                # TODO: Use a proper sentinel instead of `None` (python3.15)
                del raw_value[key]
        return raw_value

    def start(self) -> None:
        """
        Start measuring the test duration.
        """
        self._stopwatch = StopWatch()

    def finish(self) -> None:
        """
        Finish measuring the test duration
        """
        self._stopwatch.end = datetime.datetime.now()
        self.duration = self._stopwatch.format()

    def _copy_log_file(self, file: Path, missing_ok: bool = False) -> str:
        """
        Prepare a file to be added to the log entry.

        :param file: path to the log file. If it is an absolute path the file is first copied
          to ``TMT_TEST_DATA``
        :param missing_ok: do not add the log entry if the file is missing
        :return: log entry in the results
        :raise FileNotFoundError: if ``missing_ok=False`` and ``file`` does not exist
        """
        do_copy = file.is_absolute()
        log_entry = file.name if do_copy else str(file)
        file = self._parent.test_data / file
        if not file.exists():
            if not missing_ok:
                raise FileNotFoundError(f"Log file not found: {file}")
            return log_entry
        if do_copy:
            shutil.copy(file, self._parent.test_data / log_entry)
        return log_entry

    def add_file(self, file: Path, missing_ok: bool = False) -> None:
        """
        Add a file as a log entry to the results.

        :param file: path to the log file. If it is an absolute path the file is first copied
          to ``TMT_TEST_DATA``
        :param missing_ok: add the log entry even if ``file`` is missing, otherwise the log
          entry is skipped if ``file`` is missing
        """
        try:
            self.log.append(self._copy_log_file(file, missing_ok=missing_ok))
        except FileNotFoundError:
            # File is missing, but we are fine to do nothing
            pass

    def add_main_log(self, file: Path) -> None:
        """
        Add a primary log file.
        """
        try:
            self.log.insert(0, self._copy_log_file(file))
        except FileNotFoundError:
            logger.warning(f"Main log file not found: {file}")

    def add_viewer_html(self, file: Path) -> None:
        """
        Add a ``viewer.html`` file.
        """
        # It is just the main log file
        self.add_main_log(file)

    def save(self) -> None:
        """
        Shortcut to :py:meth:`Results.save`.
        """
        self._parent.save()

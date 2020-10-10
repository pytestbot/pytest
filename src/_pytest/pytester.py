"""(Disabled by default) support for testing pytest and pytest plugins."""
import collections.abc
import contextlib
import gc
import importlib
import os
import platform
import re
import shutil
import subprocess
import sys
import traceback
from fnmatch import fnmatch
from io import StringIO
from pathlib import Path
from typing import Callable
from typing import Dict
from typing import Generator
from typing import Iterable
from typing import List
from typing import Optional
from typing import overload
from typing import Sequence
from typing import TextIO
from typing import Tuple
from typing import Type
from typing import TYPE_CHECKING
from typing import Union
from weakref import WeakKeyDictionary

import attr
import py
from iniconfig import IniConfig

import pytest
from _pytest import timing
from _pytest._code import Source
from _pytest.capture import _get_multicapture
from _pytest.compat import final
from _pytest.config import _PluggyPlugin
from _pytest.config import Config
from _pytest.config import ExitCode
from _pytest.config import PytestPluginManager
from _pytest.config.argparsing import Parser
from _pytest.fixtures import FixtureRequest
from _pytest.main import Session
from _pytest.monkeypatch import MonkeyPatch
from _pytest.nodes import Collector
from _pytest.nodes import Item
from _pytest.pathlib import make_numbered_dir
from _pytest.python import Module
from _pytest.reports import CollectReport
from _pytest.reports import TestReport
from _pytest.tmpdir import TempPathFactory

if TYPE_CHECKING:
    from typing_extensions import Literal

    import pexpect

    PathLike = os.PathLike[str]
else:
    PathLike = os.PathLike


IGNORE_PAM = [  # filenames added when obtaining details about the current user
    "/var/lib/sss/mc/passwd"
]


def pytest_addoption(parser: Parser) -> None:
    parser.addoption(
        "--lsof",
        action="store_true",
        dest="lsof",
        default=False,
        help="run FD checks if lsof is available",
    )

    parser.addoption(
        "--runpytest",
        default="inprocess",
        dest="runpytest",
        choices=("inprocess", "subprocess"),
        help=(
            "run pytest sub runs in tests using an 'inprocess' "
            "or 'subprocess' (python -m main) method"
        ),
    )

    parser.addini(
        "pytester_example_dir", help="directory to take the pytester example files from"
    )


def pytest_configure(config: Config) -> None:
    if config.getvalue("lsof"):
        checker = LsofFdLeakChecker()
        if checker.matching_platform():
            config.pluginmanager.register(checker)

    config.addinivalue_line(
        "markers",
        "pytester_example_path(*path_segments): join the given path "
        "segments to `pytester_example_dir` for this test.",
    )


class LsofFdLeakChecker:
    def get_open_files(self) -> List[Tuple[str, str]]:
        out = subprocess.run(
            ("lsof", "-Ffn0", "-p", str(os.getpid())),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            universal_newlines=True,
        ).stdout

        def isopen(line: str) -> bool:
            return line.startswith("f") and (
                "deleted" not in line
                and "mem" not in line
                and "txt" not in line
                and "cwd" not in line
            )

        open_files = []

        for line in out.split("\n"):
            if isopen(line):
                fields = line.split("\0")
                fd = fields[0][1:]
                filename = fields[1][1:]
                if filename in IGNORE_PAM:
                    continue
                if filename.startswith("/"):
                    open_files.append((fd, filename))

        return open_files

    def matching_platform(self) -> bool:
        try:
            subprocess.run(("lsof", "-v"), check=True)
        except (OSError, subprocess.CalledProcessError):
            return False
        else:
            return True

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item: Item) -> Generator[None, None, None]:
        lines1 = self.get_open_files()
        yield
        if hasattr(sys, "pypy_version_info"):
            gc.collect()
        lines2 = self.get_open_files()

        new_fds = {t[0] for t in lines2} - {t[0] for t in lines1}
        leaked_files = [t for t in lines2 if t[0] in new_fds]
        if leaked_files:
            error = [
                "***** %s FD leakage detected" % len(leaked_files),
                *(str(f) for f in leaked_files),
                "*** Before:",
                *(str(f) for f in lines1),
                "*** After:",
                *(str(f) for f in lines2),
                "***** %s FD leakage detected" % len(leaked_files),
                "*** function %s:%s: %s " % item.location,
                "See issue #2366",
            ]
            item.warn(pytest.PytestWarning("\n".join(error)))


# used at least by pytest-xdist plugin


@pytest.fixture
def _pytest(request: FixtureRequest) -> "PytestArg":
    """Return a helper which offers a gethookrecorder(hook) method which
    returns a HookRecorder instance which helps to make assertions about called
    hooks."""
    return PytestArg(request)


class PytestArg:
    def __init__(self, request: FixtureRequest) -> None:
        self._request = request

    def gethookrecorder(self, hook) -> "HookRecorder":
        hookrecorder = HookRecorder(hook._pm)
        self._request.addfinalizer(hookrecorder.finish_recording)
        return hookrecorder


def get_public_names(values: Iterable[str]) -> List[str]:
    """Only return names from iterator values without a leading underscore."""
    return [x for x in values if x[0] != "_"]


class ParsedCall:
    def __init__(self, name: str, kwargs) -> None:
        self.__dict__.update(kwargs)
        self._name = name

    def __repr__(self) -> str:
        d = self.__dict__.copy()
        del d["_name"]
        return f"<ParsedCall {self._name!r}(**{d!r})>"

    if TYPE_CHECKING:
        # The class has undetermined attributes, this tells mypy about it.
        def __getattr__(self, key: str):
            ...


class HookRecorder:
    """Record all hooks called in a plugin manager.

    This wraps all the hook calls in the plugin manager, recording each call
    before propagating the normal calls.
    """

    def __init__(self, pluginmanager: PytestPluginManager) -> None:
        self._pluginmanager = pluginmanager
        self.calls: List[ParsedCall] = []
        self.ret: Optional[Union[int, ExitCode]] = None

        def before(hook_name: str, hook_impls, kwargs) -> None:
            self.calls.append(ParsedCall(hook_name, kwargs))

        def after(outcome, hook_name: str, hook_impls, kwargs) -> None:
            pass

        self._undo_wrapping = pluginmanager.add_hookcall_monitoring(before, after)

    def finish_recording(self) -> None:
        self._undo_wrapping()

    def getcalls(self, names: Union[str, Iterable[str]]) -> List[ParsedCall]:
        if isinstance(names, str):
            names = names.split()
        return [call for call in self.calls if call._name in names]

    def assert_contains(self, entries: Sequence[Tuple[str, str]]) -> None:
        __tracebackhide__ = True
        i = 0
        entries = list(entries)
        backlocals = sys._getframe(1).f_locals
        while entries:
            name, check = entries.pop(0)
            for ind, call in enumerate(self.calls[i:]):
                if call._name == name:
                    print("NAMEMATCH", name, call)
                    if eval(check, backlocals, call.__dict__):
                        print("CHECKERMATCH", repr(check), "->", call)
                    else:
                        print("NOCHECKERMATCH", repr(check), "-", call)
                        continue
                    i += ind + 1
                    break
                print("NONAMEMATCH", name, "with", call)
            else:
                pytest.fail(f"could not find {name!r} check {check!r}")

    def popcall(self, name: str) -> ParsedCall:
        __tracebackhide__ = True
        for i, call in enumerate(self.calls):
            if call._name == name:
                del self.calls[i]
                return call
        lines = [f"could not find call {name!r}, in:"]
        lines.extend(["  %s" % x for x in self.calls])
        pytest.fail("\n".join(lines))

    def getcall(self, name: str) -> ParsedCall:
        values = self.getcalls(name)
        assert len(values) == 1, (name, values)
        return values[0]

    # functionality for test reports

    @overload
    def getreports(
        self, names: "Literal['pytest_collectreport']",
    ) -> Sequence[CollectReport]:
        ...

    @overload
    def getreports(
        self, names: "Literal['pytest_runtest_logreport']",
    ) -> Sequence[TestReport]:
        ...

    @overload
    def getreports(
        self,
        names: Union[str, Iterable[str]] = (
            "pytest_collectreport",
            "pytest_runtest_logreport",
        ),
    ) -> Sequence[Union[CollectReport, TestReport]]:
        ...

    def getreports(
        self,
        names: Union[str, Iterable[str]] = (
            "pytest_collectreport",
            "pytest_runtest_logreport",
        ),
    ) -> Sequence[Union[CollectReport, TestReport]]:
        return [x.report for x in self.getcalls(names)]

    def matchreport(
        self,
        inamepart: str = "",
        names: Union[str, Iterable[str]] = (
            "pytest_runtest_logreport",
            "pytest_collectreport",
        ),
        when: Optional[str] = None,
    ) -> Union[CollectReport, TestReport]:
        """Return a testreport whose dotted import path matches."""
        values = []
        for rep in self.getreports(names=names):
            if not when and rep.when != "call" and rep.passed:
                # setup/teardown passing reports - let's ignore those
                continue
            if when and rep.when != when:
                continue
            if not inamepart or inamepart in rep.nodeid.split("::"):
                values.append(rep)
        if not values:
            raise ValueError(
                "could not find test report matching %r: "
                "no test reports at all!" % (inamepart,)
            )
        if len(values) > 1:
            raise ValueError(
                "found 2 or more testreports matching {!r}: {}".format(
                    inamepart, values
                )
            )
        return values[0]

    @overload
    def getfailures(
        self, names: "Literal['pytest_collectreport']",
    ) -> Sequence[CollectReport]:
        ...

    @overload
    def getfailures(
        self, names: "Literal['pytest_runtest_logreport']",
    ) -> Sequence[TestReport]:
        ...

    @overload
    def getfailures(
        self,
        names: Union[str, Iterable[str]] = (
            "pytest_collectreport",
            "pytest_runtest_logreport",
        ),
    ) -> Sequence[Union[CollectReport, TestReport]]:
        ...

    def getfailures(
        self,
        names: Union[str, Iterable[str]] = (
            "pytest_collectreport",
            "pytest_runtest_logreport",
        ),
    ) -> Sequence[Union[CollectReport, TestReport]]:
        return [rep for rep in self.getreports(names) if rep.failed]

    def getfailedcollections(self) -> Sequence[CollectReport]:
        return self.getfailures("pytest_collectreport")

    def listoutcomes(
        self,
    ) -> Tuple[
        Sequence[TestReport],
        Sequence[Union[CollectReport, TestReport]],
        Sequence[Union[CollectReport, TestReport]],
    ]:
        passed = []
        skipped = []
        failed = []
        for rep in self.getreports(
            ("pytest_collectreport", "pytest_runtest_logreport")
        ):
            if rep.passed:
                if rep.when == "call":
                    assert isinstance(rep, TestReport)
                    passed.append(rep)
            elif rep.skipped:
                skipped.append(rep)
            else:
                assert rep.failed, f"Unexpected outcome: {rep!r}"
                failed.append(rep)
        return passed, skipped, failed

    def countoutcomes(self) -> List[int]:
        return [len(x) for x in self.listoutcomes()]

    def assertoutcome(self, passed: int = 0, skipped: int = 0, failed: int = 0) -> None:
        __tracebackhide__ = True

        outcomes = self.listoutcomes()
        realpassed, realskipped, realfailed = outcomes
        obtained = {
            "passed": len(realpassed),
            "skipped": len(realskipped),
            "failed": len(realfailed),
        }
        expected = {"passed": passed, "skipped": skipped, "failed": failed}
        assert obtained == expected, outcomes

    def clear(self) -> None:
        self.calls[:] = []


@pytest.fixture
def linecomp() -> "LineComp":
    """A :class: `LineComp` instance for checking that an input linearly
    contains a sequence of strings."""
    return LineComp()


@pytest.fixture(name="LineMatcher")
def LineMatcher_fixture(request: FixtureRequest) -> Type["LineMatcher"]:
    """A reference to the :class: `LineMatcher`.

    This is instantiable with a list of lines (without their trailing newlines).
    This is useful for testing large texts, such as the output of commands.
    """
    return LineMatcher


@pytest.fixture
def pytester(request: FixtureRequest, tmp_path_factory: TempPathFactory) -> "Pytester":
    """
    Facilities to write tests/configuration files, execute pytest in isolation, and match
    against expected output, perfect for black-box testing of pytest plugins.

    It attempts to isolate the test run from external factors as much as possible, modifying
    the current working directory to ``path`` and environment variables during initialization.

    It is particularly useful for testing plugins. It is similar to the :fixture:`tmp_path`
    fixture but provides methods which aid in testing pytest itself.
    """
    return Pytester(request, tmp_path_factory)


@pytest.fixture
def testdir(pytester: "Pytester") -> "Testdir":
    """
    Identical to :fixture:`pytester`, and provides an instance whose methods return
    legacy ``py.path.local`` objects instead when applicable.

    New code should avoid using :fixture:`testdir` in favor of :fixture:`pytester`.
    """
    return Testdir(pytester)


@pytest.fixture
def _sys_snapshot() -> Generator[None, None, None]:
    snappaths = SysPathsSnapshot()
    snapmods = SysModulesSnapshot()
    yield
    snapmods.restore()
    snappaths.restore()


@pytest.fixture
def _config_for_test() -> Generator[Config, None, None]:
    from _pytest.config import get_config

    config = get_config()
    yield config
    config._ensure_unconfigure()  # cleanup, e.g. capman closing tmpfiles.


# Regex to match the session duration string in the summary: "74.34s".
rex_session_duration = re.compile(r"\d+\.\d\ds")
# Regex to match all the counts and phrases in the summary line: "34 passed, 111 skipped".
rex_outcome = re.compile(r"(\d+) (\w+)")


class RunResult:
    """The result of running a command."""

    def __init__(
        self,
        ret: Union[int, ExitCode],
        outlines: List[str],
        errlines: List[str],
        duration: float,
    ) -> None:
        try:
            self.ret: Union[int, ExitCode] = pytest.ExitCode(ret)
            """The return value."""
        except ValueError:
            self.ret = ret
        self.outlines = outlines
        """List of lines captured from stdout."""
        self.errlines = errlines
        """List of lines captured from stderr."""
        self.stdout = LineMatcher(outlines)
        """:class:`LineMatcher` of stdout.

        Use e.g. :func:`stdout.str() <LineMatcher.str()>` to reconstruct stdout, or the commonly used
        :func:`stdout.fnmatch_lines() <LineMatcher.fnmatch_lines()>` method.
        """
        self.stderr = LineMatcher(errlines)
        """:class:`LineMatcher` of stderr."""
        self.duration = duration
        """Duration in seconds."""

    def __repr__(self) -> str:
        return (
            "<RunResult ret=%s len(stdout.lines)=%d len(stderr.lines)=%d duration=%.2fs>"
            % (self.ret, len(self.stdout.lines), len(self.stderr.lines), self.duration)
        )

    def parseoutcomes(self) -> Dict[str, int]:
        """Return a dictionary of outcome noun -> count from parsing the terminal
        output that the test process produced.

        The returned nouns will always be in plural form::

            ======= 1 failed, 1 passed, 1 warning, 1 error in 0.13s ====

        Will return ``{"failed": 1, "passed": 1, "warnings": 1, "errors": 1}``.
        """
        return self.parse_summary_nouns(self.outlines)

    @classmethod
    def parse_summary_nouns(cls, lines) -> Dict[str, int]:
        """Extract the nouns from a pytest terminal summary line.

        It always returns the plural noun for consistency::

            ======= 1 failed, 1 passed, 1 warning, 1 error in 0.13s ====

        Will return ``{"failed": 1, "passed": 1, "warnings": 1, "errors": 1}``.
        """
        for line in reversed(lines):
            if rex_session_duration.search(line):
                outcomes = rex_outcome.findall(line)
                ret = {noun: int(count) for (count, noun) in outcomes}
                break
        else:
            raise ValueError("Pytest terminal summary report not found")

        to_plural = {
            "warning": "warnings",
            "error": "errors",
        }
        return {to_plural.get(k, k): v for k, v in ret.items()}

    def assert_outcomes(
        self,
        passed: int = 0,
        skipped: int = 0,
        failed: int = 0,
        errors: int = 0,
        xpassed: int = 0,
        xfailed: int = 0,
    ) -> None:
        """Assert that the specified outcomes appear with the respective
        numbers (0 means it didn't occur) in the text output from a test run."""
        __tracebackhide__ = True

        d = self.parseoutcomes()
        obtained = {
            "passed": d.get("passed", 0),
            "skipped": d.get("skipped", 0),
            "failed": d.get("failed", 0),
            "errors": d.get("errors", 0),
            "xpassed": d.get("xpassed", 0),
            "xfailed": d.get("xfailed", 0),
        }
        expected = {
            "passed": passed,
            "skipped": skipped,
            "failed": failed,
            "errors": errors,
            "xpassed": xpassed,
            "xfailed": xfailed,
        }
        assert obtained == expected


class CwdSnapshot:
    def __init__(self) -> None:
        self.__saved = os.getcwd()

    def restore(self) -> None:
        os.chdir(self.__saved)


class SysModulesSnapshot:
    def __init__(self, preserve: Optional[Callable[[str], bool]] = None) -> None:
        self.__preserve = preserve
        self.__saved = dict(sys.modules)

    def restore(self) -> None:
        if self.__preserve:
            self.__saved.update(
                (k, m) for k, m in sys.modules.items() if self.__preserve(k)
            )
        sys.modules.clear()
        sys.modules.update(self.__saved)


class SysPathsSnapshot:
    def __init__(self) -> None:
        self.__saved = list(sys.path), list(sys.meta_path)

    def restore(self) -> None:
        sys.path[:], sys.meta_path[:] = self.__saved


@final
class Pytester:
    """
    Facilities to write tests/configuration files, execute pytest in isolation, and match
    against expected output, perfect for black-box testing of pytest plugins.

    It attempts to isolate the test run from external factors as much as possible, modifying
    the current  working directory to ``path`` and environment variables during initialization.

    Attributes:

    :ivar Path path: temporary directory path used to create files/run tests from, etc.

    :ivar plugins:
       A list of plugins to use with :py:meth:`parseconfig` and
       :py:meth:`runpytest`.  Initially this is an empty list but plugins can
       be added to the list.  The type of items to add to the list depends on
       the method using them so refer to them for details.
    """

    __test__ = False

    CLOSE_STDIN = object

    class TimeoutExpired(Exception):
        pass

    def __init__(
        self, request: FixtureRequest, tmp_path_factory: TempPathFactory
    ) -> None:
        self._request = request
        self._mod_collections: WeakKeyDictionary[
            Module, List[Union[Item, Collector]]
        ] = (WeakKeyDictionary())
        if request.function:
            name: str = request.function.__name__
        else:
            name = request.node.name
        self._name = name
        self._tmp_path_factory = tmp_path_factory
        self._path: Path = tmp_path_factory.mktemp(name, numbered=True)
        self.plugins: List[Union[str, _PluggyPlugin]] = []
        self._cwd_snapshot = CwdSnapshot()
        self._sys_path_snapshot = SysPathsSnapshot()
        self._sys_modules_snapshot = self.__take_sys_modules_snapshot()
        self.chdir()
        self._request.addfinalizer(self._finalize)
        self._method = self._request.config.getoption("--runpytest")

        self._monkeypatch = mp = MonkeyPatch()
        mp.setenv("PYTEST_DEBUG_TEMPROOT", str(tmp_path_factory.mktemp("tmproot")))
        # Ensure no unexpected caching via tox.
        mp.delenv("TOX_ENV_DIR", raising=False)
        # Discard outer pytest options.
        mp.delenv("PYTEST_ADDOPTS", raising=False)
        # Ensure no user config is used.
        tmphome = str(self.path)
        mp.setenv("HOME", tmphome)
        mp.setenv("USERPROFILE", tmphome)
        # Do not use colors for inner runs by default.
        mp.setenv("PY_COLORS", "0")

    @property
    def path(self) -> Path:
        """Temporary directory where files are created and pytest is executed."""
        return self._path

    def __repr__(self) -> str:
        return f"<Pytester {self.path!r}>"

    def _finalize(self) -> None:
        """
        Clean up global state artifacts.

        Some methods modify the global interpreter state and this tries to
        clean this up. It does not remove the temporary directory however so
        it can be looked at after the test run has finished.
        """
        self._sys_modules_snapshot.restore()
        self._sys_path_snapshot.restore()
        self._cwd_snapshot.restore()
        self._monkeypatch.undo()

    def __take_sys_modules_snapshot(self) -> SysModulesSnapshot:
        # Some zope modules used by twisted-related tests keep internal state
        # and can't be deleted; we had some trouble in the past with
        # `zope.interface` for example.
        def preserve_module(name):
            return name.startswith("zope")

        return SysModulesSnapshot(preserve=preserve_module)

    def make_hook_recorder(self, pluginmanager: PytestPluginManager) -> HookRecorder:
        """Create a new :py:class:`HookRecorder` for a PluginManager."""
        pluginmanager.reprec = reprec = HookRecorder(pluginmanager)
        self._request.addfinalizer(reprec.finish_recording)
        return reprec

    def chdir(self) -> None:
        """Cd into the temporary directory.

        This is done automatically upon instantiation.
        """
        os.chdir(self.path)

    def _makefile(self, ext: str, lines, files, encoding: str = "utf-8") -> Path:
        items = list(files.items())

        def to_text(s):
            return s.decode(encoding) if isinstance(s, bytes) else str(s)

        if lines:
            source = "\n".join(to_text(x) for x in lines)
            basename = self._name
            items.insert(0, (basename, source))

        ret = None
        for basename, value in items:
            p = self.path.joinpath(basename).with_suffix(ext)
            p.parent.mkdir(parents=True, exist_ok=True)
            source_ = Source(value)
            source = "\n".join(to_text(line) for line in source_.lines)
            p.write_text(source.strip(), encoding=encoding)
            if ret is None:
                ret = p
        assert ret is not None
        return ret

    def makefile(self, ext: str, *args: str, **kwargs) -> Path:
        r"""Create new file(s) in the test directory.

        :param str ext:
            The extension the file(s) should use, including the dot, e.g. `.py`.
        :param args:
            All args are treated as strings and joined using newlines.
            The result is written as contents to the file.  The name of the
            file is based on the test function requesting this fixture.
        :param kwargs:
            Each keyword is the name of a file, while the value of it will
            be written as contents of the file.

        Examples:

        .. code-block:: python

            testdir.makefile(".txt", "line1", "line2")

            testdir.makefile(".ini", pytest="[pytest]\naddopts=-rs\n")

        """
        return self._makefile(ext, args, kwargs)

    def makeconftest(self, source) -> Path:
        """Write a contest.py file with 'source' as contents."""
        return self.makepyfile(conftest=source)

    def makeini(self, source) -> Path:
        """Write a tox.ini file with 'source' as contents."""
        return self.makefile(".ini", tox=source)

    def getinicfg(self, source) -> IniConfig:
        """Return the pytest section from the tox.ini config file."""
        p = self.makeini(source)
        return IniConfig(p)["pytest"]

    def makepyprojecttoml(self, source) -> Path:
        """Write a pyproject.toml file with 'source' as contents.

        .. versionadded:: 6.0
        """
        return self.makefile(".toml", pyproject=source)

    def makepyfile(self, *args, **kwargs) -> Path:
        r"""Shortcut for .makefile() with a .py extension.

        Defaults to the test name with a '.py' extension, e.g test_foobar.py, overwriting
        existing files.

        Examples:

        .. code-block:: python

            def test_something(testdir):
                # Initial file is created test_something.py.
                testdir.makepyfile("foobar")
                # To create multiple files, pass kwargs accordingly.
                testdir.makepyfile(custom="foobar")
                # At this point, both 'test_something.py' & 'custom.py' exist in the test directory.

        """
        return self._makefile(".py", args, kwargs)

    def maketxtfile(self, *args, **kwargs) -> Path:
        r"""Shortcut for .makefile() with a .txt extension.

        Defaults to the test name with a '.txt' extension, e.g test_foobar.txt, overwriting
        existing files.

        Examples:

        .. code-block:: python

            def test_something(testdir):
                # Initial file is created test_something.txt.
                testdir.maketxtfile("foobar")
                # To create multiple files, pass kwargs accordingly.
                testdir.maketxtfile(custom="foobar")
                # At this point, both 'test_something.txt' & 'custom.txt' exist in the test directory.

        """
        return self._makefile(".txt", args, kwargs)

    def syspathinsert(self, path=None) -> None:
        """Prepend a directory to sys.path, defaults to :py:attr:`tmpdir`.

        This is undone automatically when this object dies at the end of each
        test.
        """
        if path is None:
            path = self.path

        self._monkeypatch.syspath_prepend(str(path))

    def mkdir(self, name: str) -> Path:
        """Create a new (sub)directory."""
        p = self.path / name
        p.mkdir()
        return p

    def mkpydir(self, name: str) -> Path:
        """Create a new python package.

        This creates a (sub)directory with an empty ``__init__.py`` file so it
        gets recognised as a Python package.
        """
        p = self.path / name
        p.mkdir()
        p.joinpath("__init__.py").touch()
        return p

    def copy_example(self, name=None) -> Path:
        """Copy file from project's directory into the testdir.

        :param str name: The name of the file to copy.
        :return: path to the copied directory (inside ``self.path``).

        """
        example_dir = self._request.config.getini("pytester_example_dir")
        if example_dir is None:
            raise ValueError("pytester_example_dir is unset, can't copy examples")
        example_dir = Path(str(self._request.config.rootdir)) / example_dir

        for extra_element in self._request.node.iter_markers("pytester_example_path"):
            assert extra_element.args
            example_dir = example_dir.joinpath(*extra_element.args)

        if name is None:
            func_name = self._name
            maybe_dir = example_dir / func_name
            maybe_file = example_dir / (func_name + ".py")

            if maybe_dir.is_dir():
                example_path = maybe_dir
            elif maybe_file.is_file():
                example_path = maybe_file
            else:
                raise LookupError(
                    f"{func_name} cant be found as module or package in {example_dir}"
                )
        else:
            example_path = example_dir.joinpath(name)

        if example_path.is_dir() and not example_path.joinpath("__init__.py").is_file():
            # TODO: py.path.local.copy can copy files to existing directories,
            # while with shutil.copytree the destination directory cannot exist,
            # we will need to roll our own in order to drop py.path.local completely
            py.path.local(example_path).copy(py.path.local(self.path))
            return self.path
        elif example_path.is_file():
            result = self.path.joinpath(example_path.name)
            shutil.copy(example_path, result)
            return result
        else:
            raise LookupError(
                f'example "{example_path}" is not found as a file or directory'
            )

    Session = Session

    def getnode(
        self, config: Config, arg: Union[PathLike, str]
    ) -> Optional[Union[Collector, Item]]:
        """Return the collection node of a file.

        :param _pytest.config.Config config:
           A pytest config.
           See :py:meth:`parseconfig` and :py:meth:`parseconfigure` for creating it.
        :param py.path.local arg:
            Path to the file.
        """
        session = Session.from_config(config)
        assert "::" not in str(arg)
        p = py.path.local(arg)
        config.hook.pytest_sessionstart(session=session)
        res = session.perform_collect([str(p)], genitems=False)[0]
        config.hook.pytest_sessionfinish(session=session, exitstatus=ExitCode.OK)
        return res

    def getpathnode(self, path: Union[PathLike, str]):
        """Return the collection node of a file.

        This is like :py:meth:`getnode` but uses :py:meth:`parseconfigure` to
        create the (configured) pytest Config instance.

        :param py.path.local path: Path to the file.
        """
        path = py.path.local(path)
        config = self.parseconfigure(path)
        session = Session.from_config(config)
        x = session.fspath.bestrelpath(path)
        config.hook.pytest_sessionstart(session=session)
        res = session.perform_collect([x], genitems=False)[0]
        config.hook.pytest_sessionfinish(session=session, exitstatus=ExitCode.OK)
        return res

    def genitems(self, colitems: Sequence[Union[Item, Collector]]) -> List[Item]:
        """Generate all test items from a collection node.

        This recurses into the collection node and returns a list of all the
        test items contained within.
        """
        session = colitems[0].session
        result: List[Item] = []
        for colitem in colitems:
            result.extend(session.genitems(colitem))
        return result

    def runitem(self, source):
        """Run the "test_func" Item.

        The calling test instance (class containing the test method) must
        provide a ``.getrunner()`` method which should return a runner which
        can run the test protocol for a single item, e.g.
        :py:func:`_pytest.runner.runtestprotocol`.
        """
        # used from runner functional tests
        item = self.getitem(source)
        # the test class where we are called from wants to provide the runner
        testclassinstance = self._request.instance
        runner = testclassinstance.getrunner()
        return runner(item)

    def inline_runsource(self, source, *cmdlineargs) -> HookRecorder:
        """Run a test module in process using ``pytest.main()``.

        This run writes "source" into a temporary file and runs
        ``pytest.main()`` on it, returning a :py:class:`HookRecorder` instance
        for the result.

        :param source: The source code of the test module.

        :param cmdlineargs: Any extra command line arguments to use.

        :returns: :py:class:`HookRecorder` instance of the result.
        """
        p = self.makepyfile(source)
        values = list(cmdlineargs) + [p]
        return self.inline_run(*values)

    def inline_genitems(self, *args) -> Tuple[List[Item], HookRecorder]:
        """Run ``pytest.main(['--collectonly'])`` in-process.

        Runs the :py:func:`pytest.main` function to run all of pytest inside
        the test process itself like :py:meth:`inline_run`, but returns a
        tuple of the collected items and a :py:class:`HookRecorder` instance.
        """
        rec = self.inline_run("--collect-only", *args)
        items = [x.item for x in rec.getcalls("pytest_itemcollected")]
        return items, rec

    def inline_run(
        self, *args, plugins=(), no_reraise_ctrlc: bool = False
    ) -> HookRecorder:
        """Run ``pytest.main()`` in-process, returning a HookRecorder.

        Runs the :py:func:`pytest.main` function to run all of pytest inside
        the test process itself.  This means it can return a
        :py:class:`HookRecorder` instance which gives more detailed results
        from that run than can be done by matching stdout/stderr from
        :py:meth:`runpytest`.

        :param args:
            Command line arguments to pass to :py:func:`pytest.main`.
        :param plugins:
            Extra plugin instances the ``pytest.main()`` instance should use.
        :param no_reraise_ctrlc:
            Typically we reraise keyboard interrupts from the child run. If
            True, the KeyboardInterrupt exception is captured.

        :returns: A :py:class:`HookRecorder` instance.
        """
        # (maybe a cpython bug?) the importlib cache sometimes isn't updated
        # properly between file creation and inline_run (especially if imports
        # are interspersed with file creation)
        importlib.invalidate_caches()

        plugins = list(plugins)
        finalizers = []
        try:
            # Any sys.module or sys.path changes done while running pytest
            # inline should be reverted after the test run completes to avoid
            # clashing with later inline tests run within the same pytest test,
            # e.g. just because they use matching test module names.
            finalizers.append(self.__take_sys_modules_snapshot().restore)
            finalizers.append(SysPathsSnapshot().restore)

            # Important note:
            # - our tests should not leave any other references/registrations
            #   laying around other than possibly loaded test modules
            #   referenced from sys.modules, as nothing will clean those up
            #   automatically

            rec = []

            class Collect:
                def pytest_configure(x, config: Config) -> None:
                    rec.append(self.make_hook_recorder(config.pluginmanager))

            plugins.append(Collect())
            ret = pytest.main([str(x) for x in args], plugins=plugins)
            if len(rec) == 1:
                reprec = rec.pop()
            else:

                class reprec:  # type: ignore
                    pass

            reprec.ret = ret  # type: ignore

            # Typically we reraise keyboard interrupts from the child run
            # because it's our user requesting interruption of the testing.
            if ret == ExitCode.INTERRUPTED and not no_reraise_ctrlc:
                calls = reprec.getcalls("pytest_keyboard_interrupt")
                if calls and calls[-1].excinfo.type == KeyboardInterrupt:
                    raise KeyboardInterrupt()
            return reprec
        finally:
            for finalizer in finalizers:
                finalizer()

    def runpytest_inprocess(self, *args, **kwargs) -> RunResult:
        """Return result of running pytest in-process, providing a similar
        interface to what self.runpytest() provides."""
        syspathinsert = kwargs.pop("syspathinsert", False)

        if syspathinsert:
            self.syspathinsert()
        now = timing.time()
        capture = _get_multicapture("sys")
        capture.start_capturing()
        try:
            try:
                reprec = self.inline_run(*args, **kwargs)
            except SystemExit as e:
                ret = e.args[0]
                try:
                    ret = ExitCode(e.args[0])
                except ValueError:
                    pass

                class reprec:  # type: ignore
                    ret = ret

            except Exception:
                traceback.print_exc()

                class reprec:  # type: ignore
                    ret = ExitCode(3)

        finally:
            out, err = capture.readouterr()
            capture.stop_capturing()
            sys.stdout.write(out)
            sys.stderr.write(err)

        assert reprec.ret is not None
        res = RunResult(
            reprec.ret, out.splitlines(), err.splitlines(), timing.time() - now
        )
        res.reprec = reprec  # type: ignore
        return res

    def runpytest(self, *args, **kwargs) -> RunResult:
        """Run pytest inline or in a subprocess, depending on the command line
        option "--runpytest" and return a :py:class:`RunResult`."""
        args = self._ensure_basetemp(args)
        if self._method == "inprocess":
            return self.runpytest_inprocess(*args, **kwargs)
        elif self._method == "subprocess":
            return self.runpytest_subprocess(*args, **kwargs)
        raise RuntimeError(f"Unrecognized runpytest option: {self._method}")

    def _ensure_basetemp(self, args):
        args = list(args)
        for x in args:
            if str(x).startswith("--basetemp"):
                break
        else:
            args.append("--basetemp=%s" % self.path.parent.joinpath("basetemp"))
        return args

    def parseconfig(self, *args) -> Config:
        """Return a new pytest Config instance from given commandline args.

        This invokes the pytest bootstrapping code in _pytest.config to create
        a new :py:class:`_pytest.core.PluginManager` and call the
        pytest_cmdline_parse hook to create a new
        :py:class:`_pytest.config.Config` instance.

        If :py:attr:`plugins` has been populated they should be plugin modules
        to be registered with the PluginManager.
        """
        import _pytest.config

        new_args = self._ensure_basetemp(args)
        new_args = [str(x) for x in new_args]

        config = _pytest.config._prepareconfig(new_args, self.plugins)  # type: ignore[arg-type]
        # we don't know what the test will do with this half-setup config
        # object and thus we make sure it gets unconfigured properly in any
        # case (otherwise capturing could still be active, for example)
        self._request.addfinalizer(config._ensure_unconfigure)
        return config

    def parseconfigure(self, *args) -> Config:
        """Return a new pytest configured Config instance.

        Returns a new :py:class:`_pytest.config.Config` instance like
        :py:meth:`parseconfig`, but also calls the pytest_configure hook.
        """
        config = self.parseconfig(*args)
        config._do_configure()
        return config

    def getitem(self, source, funcname: str = "test_func") -> Item:
        """Return the test item for a test function.

        Writes the source to a python file and runs pytest's collection on
        the resulting module, returning the test item for the requested
        function name.

        :param source:
            The module source.
        :param funcname:
            The name of the test function for which to return a test item.
        """
        items = self.getitems(source)
        for item in items:
            if item.name == funcname:
                return item
        assert 0, "{!r} item not found in module:\n{}\nitems: {}".format(
            funcname, source, items
        )

    def getitems(self, source) -> List[Item]:
        """Return all test items collected from the module.

        Writes the source to a Python file and runs pytest's collection on
        the resulting module, returning all test items contained within.
        """
        modcol = self.getmodulecol(source)
        return self.genitems([modcol])

    def getmodulecol(self, source, configargs=(), withinit: bool = False):
        """Return the module collection node for ``source``.

        Writes ``source`` to a file using :py:meth:`makepyfile` and then
        runs the pytest collection on it, returning the collection node for the
        test module.

        :param source:
            The source code of the module to collect.

        :param configargs:
            Any extra arguments to pass to :py:meth:`parseconfigure`.

        :param withinit:
            Whether to also write an ``__init__.py`` file to the same
            directory to ensure it is a package.
        """
        if isinstance(source, Path):
            path = self.path.joinpath(source)
            assert not withinit, "not supported for paths"
        else:
            kw = {self._name: str(source)}
            path = self.makepyfile(**kw)
        if withinit:
            self.makepyfile(__init__="#")
        self.config = config = self.parseconfigure(path, *configargs)
        return self.getnode(config, path)

    def collect_by_name(
        self, modcol: Module, name: str
    ) -> Optional[Union[Item, Collector]]:
        """Return the collection node for name from the module collection.

        Searchs a module collection node for a collection node matching the
        given name.

        :param modcol: A module collection node; see :py:meth:`getmodulecol`.
        :param name: The name of the node to return.
        """
        if modcol not in self._mod_collections:
            self._mod_collections[modcol] = list(modcol.collect())
        for colitem in self._mod_collections[modcol]:
            if colitem.name == name:
                return colitem
        return None

    def popen(
        self,
        cmdargs,
        stdout: Union[int, TextIO] = subprocess.PIPE,
        stderr: Union[int, TextIO] = subprocess.PIPE,
        stdin=CLOSE_STDIN,
        **kw,
    ):
        """Invoke subprocess.Popen.

        Calls subprocess.Popen making sure the current working directory is
        in the PYTHONPATH.

        You probably want to use :py:meth:`run` instead.
        """
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [os.getcwd(), env.get("PYTHONPATH", "")])
        )
        kw["env"] = env

        if stdin is Testdir.CLOSE_STDIN:
            kw["stdin"] = subprocess.PIPE
        elif isinstance(stdin, bytes):
            kw["stdin"] = subprocess.PIPE
        else:
            kw["stdin"] = stdin

        popen = subprocess.Popen(cmdargs, stdout=stdout, stderr=stderr, **kw)
        if stdin is Testdir.CLOSE_STDIN:
            assert popen.stdin is not None
            popen.stdin.close()
        elif isinstance(stdin, bytes):
            assert popen.stdin is not None
            popen.stdin.write(stdin)

        return popen

    def run(
        self,
        *cmdargs: Union[str, PathLike],
        timeout: Optional[float] = None,
        stdin=CLOSE_STDIN,
    ) -> RunResult:
        """Run a command with arguments.

        Run a process using subprocess.Popen saving the stdout and stderr.

        :param cmdargs:
            The sequence of arguments to pass to `subprocess.Popen()`, with ``Path``
            and ``py.path.local`` objects being converted to ``str`` automatically.
        :param timeout:
            The period in seconds after which to timeout and raise
            :py:class:`Testdir.TimeoutExpired`
        :param stdin:
            Optional standard input.  Bytes are being send, closing
            the pipe, otherwise it is passed through to ``popen``.
            Defaults to ``CLOSE_STDIN``, which translates to using a pipe
            (``subprocess.PIPE``) that gets closed.

        :rtype: RunResult
        """
        __tracebackhide__ = True

        cmdargs = tuple(
            str(arg) if isinstance(arg, (py.path.local, Path)) else arg
            for arg in cmdargs
        )
        p1 = self.path.joinpath("stdout")
        p2 = self.path.joinpath("stderr")
        print("running:", *cmdargs)
        print("     in:", Path.cwd())

        with p1.open("w", encoding="utf8") as f1, p2.open("w", encoding="utf8") as f2:
            now = timing.time()
            popen = self.popen(
                cmdargs,
                stdin=stdin,
                stdout=f1,
                stderr=f2,
                close_fds=(sys.platform != "win32"),
            )
            if isinstance(stdin, bytes):
                popen.stdin.close()

            def handle_timeout() -> None:
                __tracebackhide__ = True

                timeout_message = (
                    "{seconds} second timeout expired running:"
                    " {command}".format(seconds=timeout, command=cmdargs)
                )

                popen.kill()
                popen.wait()
                raise self.TimeoutExpired(timeout_message)

            if timeout is None:
                ret = popen.wait()
            else:
                try:
                    ret = popen.wait(timeout)
                except subprocess.TimeoutExpired:
                    handle_timeout()

        with p1.open(encoding="utf8") as f1, p2.open(encoding="utf8") as f2:
            out = f1.read().splitlines()
            err = f2.read().splitlines()

        self._dump_lines(out, sys.stdout)
        self._dump_lines(err, sys.stderr)

        with contextlib.suppress(ValueError):
            ret = ExitCode(ret)
        return RunResult(ret, out, err, timing.time() - now)

    def _dump_lines(self, lines, fp):
        try:
            for line in lines:
                print(line, file=fp)
        except UnicodeEncodeError:
            print(f"couldn't print to {fp} because of encoding")

    def _getpytestargs(self) -> Tuple[str, ...]:
        return sys.executable, "-mpytest"

    def runpython(self, script) -> RunResult:
        """Run a python script using sys.executable as interpreter.

        :rtype: RunResult
        """
        return self.run(sys.executable, script)

    def runpython_c(self, command):
        """Run python -c "command".

        :rtype: RunResult
        """
        return self.run(sys.executable, "-c", command)

    def runpytest_subprocess(self, *args, timeout: Optional[float] = None) -> RunResult:
        """Run pytest as a subprocess with given arguments.

        Any plugins added to the :py:attr:`plugins` list will be added using the
        ``-p`` command line option.  Additionally ``--basetemp`` is used to put
        any temporary files and directories in a numbered directory prefixed
        with "runpytest-" to not conflict with the normal numbered pytest
        location for temporary files and directories.

        :param args:
            The sequence of arguments to pass to the pytest subprocess.
        :param timeout:
            The period in seconds after which to timeout and raise
            :py:class:`Testdir.TimeoutExpired`.

        :rtype: RunResult
        """
        __tracebackhide__ = True
        p = make_numbered_dir(root=self.path, prefix="runpytest-")
        args = ("--basetemp=%s" % p,) + args
        plugins = [x for x in self.plugins if isinstance(x, str)]
        if plugins:
            args = ("-p", plugins[0]) + args
        args = self._getpytestargs() + args
        return self.run(*args, timeout=timeout)

    def spawn_pytest(
        self, string: str, expect_timeout: float = 10.0
    ) -> "pexpect.spawn":
        """Run pytest using pexpect.

        This makes sure to use the right pytest and sets up the temporary
        directory locations.

        The pexpect child is returned.
        """
        basetemp = self.path / "temp-pexpect"
        basetemp.mkdir()
        invoke = " ".join(map(str, self._getpytestargs()))
        cmd = f"{invoke} --basetemp={basetemp} {string}"
        return self.spawn(cmd, expect_timeout=expect_timeout)

    def spawn(self, cmd: str, expect_timeout: float = 10.0) -> "pexpect.spawn":
        """Run a command using pexpect.

        The pexpect child is returned.
        """
        pexpect = pytest.importorskip("pexpect", "3.0")
        if hasattr(sys, "pypy_version_info") and "64" in platform.machine():
            pytest.skip("pypy-64 bit not supported")
        if not hasattr(pexpect, "spawn"):
            pytest.skip("pexpect.spawn not available")
        logfile = self.path.joinpath("spawn.out").open("wb")

        child = pexpect.spawn(cmd, logfile=logfile)
        self._request.addfinalizer(logfile.close)
        child.timeout = expect_timeout
        return child


class LineComp:
    def __init__(self) -> None:
        self.stringio = StringIO()
        """:class:`python:io.StringIO()` instance used for input."""

    def assert_contains_lines(self, lines2: Sequence[str]) -> None:
        """Assert that ``lines2`` are contained (linearly) in :attr:`stringio`'s value.

        Lines are matched using :func:`LineMatcher.fnmatch_lines`.
        """
        __tracebackhide__ = True
        val = self.stringio.getvalue()
        self.stringio.truncate(0)
        self.stringio.seek(0)
        lines1 = val.split("\n")
        LineMatcher(lines1).fnmatch_lines(lines2)


@final
@attr.s(repr=False, str=False)
class Testdir:
    """
    Similar to :class:`Pytester`, but this class works with legacy py.path.local objects instead.

    All methods just forward to an internal :class:`Pytester` instance, converting results
    to `py.path.local` objects as necessary.
    """

    __test__ = False

    CLOSE_STDIN = Pytester.CLOSE_STDIN
    TimeoutExpired = Pytester.TimeoutExpired
    Session = Pytester.Session

    _pytester = attr.ib(type=Pytester)

    def __attrs_post_init__(self):
        self.test_tmproot = py.path.local(
            self._pytester._tmp_path_factory.mktemp(
                "tmp-" + self._pytester._name, numbered=True
            )
        )

    @property
    def tmpdir(self) -> py.path.local:
        return py.path.local(self._pytester.path)

    @property
    def request(self):
        return self._pytester._request

    @property
    def plugins(self):
        return self._pytester.plugins

    @plugins.setter
    def plugins(self, plugins):
        self._pytester.plugins = plugins

    @property
    def monkeypatch(self) -> MonkeyPatch:
        return self._pytester._monkeypatch

    def make_hook_recorder(self, pluginmanager) -> HookRecorder:
        return self._pytester.make_hook_recorder(pluginmanager)

    def chdir(self) -> None:
        return self._pytester.chdir()

    def finalize(self) -> None:
        return self._pytester._finalize()

    def makefile(self, ext, *args, **kwargs) -> py.path.local:
        return py.path.local(str(self._pytester.makefile(ext, *args, **kwargs)))

    def makeconftest(self, source) -> py.path.local:
        return py.path.local(str(self._pytester.makeconftest(source)))

    def makeini(self, source) -> py.path.local:
        return py.path.local(str(self._pytester.makeini(source)))

    def getinicfg(self, source) -> py.path.local:
        return py.path.local(str(self._pytester.getinicfg(source)))

    def makepyprojecttoml(self, source) -> py.path.local:
        return py.path.local(str(self._pytester.makepyprojecttoml(source)))

    def makepyfile(self, *args, **kwargs) -> py.path.local:
        return py.path.local(str(self._pytester.makepyfile(*args, **kwargs)))

    def maketxtfile(self, *args, **kwargs) -> py.path.local:
        return py.path.local(str(self._pytester.maketxtfile(*args, **kwargs)))

    def syspathinsert(self, path=None) -> None:
        return self._pytester.syspathinsert(path)

    def mkdir(self, name) -> py.path.local:
        return py.path.local(str(self._pytester.mkdir(name)))

    def mkpydir(self, name) -> py.path.local:
        return py.path.local(str(self._pytester.mkpydir(name)))

    def copy_example(self, name=None) -> py.path.local:
        return py.path.local(str(self._pytester.copy_example(name)))

    def getnode(self, config: Config, arg) -> Optional[Union[Item, Collector]]:
        return self._pytester.getnode(config, arg)

    def getpathnode(self, path):
        return self._pytester.getpathnode(path)

    def genitems(self, colitems: List[Union[Item, Collector]]) -> List[Item]:
        return self._pytester.genitems(colitems)

    def runitem(self, source):
        return self._pytester.runitem(source)

    def inline_runsource(self, source, *cmdlineargs):
        return self._pytester.inline_runsource(source, *cmdlineargs)

    def inline_genitems(self, *args):
        return self._pytester.inline_genitems(*args)

    def inline_run(self, *args, plugins=(), no_reraise_ctrlc: bool = False):
        return self._pytester.inline_run(
            *args, plugins=plugins, no_reraise_ctrlc=no_reraise_ctrlc
        )

    def runpytest_inprocess(self, *args, **kwargs) -> RunResult:
        return self._pytester.runpytest_inprocess(*args, **kwargs)

    def runpytest(self, *args, **kwargs) -> RunResult:
        return self._pytester.runpytest(*args, **kwargs)

    def parseconfig(self, *args) -> Config:
        return self._pytester.parseconfig(*args)

    def parseconfigure(self, *args) -> Config:
        return self._pytester.parseconfigure(*args)

    def getitem(self, source, funcname="test_func"):
        return self._pytester.getitem(source, funcname)

    def getitems(self, source):
        return self._pytester.getitems(source)

    def getmodulecol(self, source, configargs=(), withinit=False):
        return self._pytester.getmodulecol(
            source, configargs=configargs, withinit=withinit
        )

    def collect_by_name(
        self, modcol: Module, name: str
    ) -> Optional[Union[Item, Collector]]:
        return self._pytester.collect_by_name(modcol, name)

    def popen(
        self,
        cmdargs,
        stdout: Union[int, TextIO] = subprocess.PIPE,
        stderr: Union[int, TextIO] = subprocess.PIPE,
        stdin=CLOSE_STDIN,
        **kw,
    ):
        return self._pytester.popen(cmdargs, stdout, stderr, stdin, **kw)

    def run(self, *cmdargs, timeout=None, stdin=CLOSE_STDIN) -> RunResult:
        return self._pytester.run(*cmdargs, timeout=timeout, stdin=stdin)

    def runpython(self, script) -> RunResult:
        return self._pytester.runpython(script)

    def runpython_c(self, command):
        return self._pytester.runpython_c(command)

    def runpytest_subprocess(self, *args, timeout=None) -> RunResult:
        return self._pytester.runpytest_subprocess(*args, timeout=timeout)

    def spawn_pytest(
        self, string: str, expect_timeout: float = 10.0
    ) -> "pexpect.spawn":
        return self._pytester.spawn_pytest(string, expect_timeout=expect_timeout)

    def spawn(self, cmd: str, expect_timeout: float = 10.0) -> "pexpect.spawn":
        return self._pytester.spawn(cmd, expect_timeout=expect_timeout)

    def __repr__(self) -> str:
        return f"<Testdir {self.tmpdir!r}>"

    def __str__(self) -> str:
        return str(self.tmpdir)


class LineMatcher:
    """Flexible matching of text.

    This is a convenience class to test large texts like the output of
    commands.

    The constructor takes a list of lines without their trailing newlines, i.e.
    ``text.splitlines()``.
    """

    def __init__(self, lines: List[str]) -> None:
        self.lines = lines
        self._log_output: List[str] = []

    def _getlines(self, lines2: Union[str, Sequence[str], Source]) -> Sequence[str]:
        if isinstance(lines2, str):
            lines2 = Source(lines2)
        if isinstance(lines2, Source):
            lines2 = lines2.strip().lines
        return lines2

    def fnmatch_lines_random(self, lines2: Sequence[str]) -> None:
        """Check lines exist in the output in any order (using :func:`python:fnmatch.fnmatch`)."""
        __tracebackhide__ = True
        self._match_lines_random(lines2, fnmatch)

    def re_match_lines_random(self, lines2: Sequence[str]) -> None:
        """Check lines exist in the output in any order (using :func:`python:re.match`)."""
        __tracebackhide__ = True
        self._match_lines_random(lines2, lambda name, pat: bool(re.match(pat, name)))

    def _match_lines_random(
        self, lines2: Sequence[str], match_func: Callable[[str, str], bool]
    ) -> None:
        __tracebackhide__ = True
        lines2 = self._getlines(lines2)
        for line in lines2:
            for x in self.lines:
                if line == x or match_func(x, line):
                    self._log("matched: ", repr(line))
                    break
            else:
                msg = "line %r not found in output" % line
                self._log(msg)
                self._fail(msg)

    def get_lines_after(self, fnline: str) -> Sequence[str]:
        """Return all lines following the given line in the text.

        The given line can contain glob wildcards.
        """
        for i, line in enumerate(self.lines):
            if fnline == line or fnmatch(line, fnline):
                return self.lines[i + 1 :]
        raise ValueError("line %r not found in output" % fnline)

    def _log(self, *args) -> None:
        self._log_output.append(" ".join(str(x) for x in args))

    @property
    def _log_text(self) -> str:
        return "\n".join(self._log_output)

    def fnmatch_lines(
        self, lines2: Sequence[str], *, consecutive: bool = False
    ) -> None:
        """Check lines exist in the output (using :func:`python:fnmatch.fnmatch`).

        The argument is a list of lines which have to match and can use glob
        wildcards.  If they do not match a pytest.fail() is called.  The
        matches and non-matches are also shown as part of the error message.

        :param lines2: String patterns to match.
        :param consecutive: Match lines consecutively?
        """
        __tracebackhide__ = True
        self._match_lines(lines2, fnmatch, "fnmatch", consecutive=consecutive)

    def re_match_lines(
        self, lines2: Sequence[str], *, consecutive: bool = False
    ) -> None:
        """Check lines exist in the output (using :func:`python:re.match`).

        The argument is a list of lines which have to match using ``re.match``.
        If they do not match a pytest.fail() is called.

        The matches and non-matches are also shown as part of the error message.

        :param lines2: string patterns to match.
        :param consecutive: match lines consecutively?
        """
        __tracebackhide__ = True
        self._match_lines(
            lines2,
            lambda name, pat: bool(re.match(pat, name)),
            "re.match",
            consecutive=consecutive,
        )

    def _match_lines(
        self,
        lines2: Sequence[str],
        match_func: Callable[[str, str], bool],
        match_nickname: str,
        *,
        consecutive: bool = False,
    ) -> None:
        """Underlying implementation of ``fnmatch_lines`` and ``re_match_lines``.

        :param Sequence[str] lines2:
            List of string patterns to match. The actual format depends on
            ``match_func``.
        :param match_func:
            A callable ``match_func(line, pattern)`` where line is the
            captured line from stdout/stderr and pattern is the matching
            pattern.
        :param str match_nickname:
            The nickname for the match function that will be logged to stdout
            when a match occurs.
        :param consecutive:
            Match lines consecutively?
        """
        if not isinstance(lines2, collections.abc.Sequence):
            raise TypeError("invalid type for lines2: {}".format(type(lines2).__name__))
        lines2 = self._getlines(lines2)
        lines1 = self.lines[:]
        nextline = None
        extralines = []
        __tracebackhide__ = True
        wnick = len(match_nickname) + 1
        started = False
        for line in lines2:
            nomatchprinted = False
            while lines1:
                nextline = lines1.pop(0)
                if line == nextline:
                    self._log("exact match:", repr(line))
                    started = True
                    break
                elif match_func(nextline, line):
                    self._log("%s:" % match_nickname, repr(line))
                    self._log(
                        "{:>{width}}".format("with:", width=wnick), repr(nextline)
                    )
                    started = True
                    break
                else:
                    if consecutive and started:
                        msg = f"no consecutive match: {line!r}"
                        self._log(msg)
                        self._log(
                            "{:>{width}}".format("with:", width=wnick), repr(nextline)
                        )
                        self._fail(msg)
                    if not nomatchprinted:
                        self._log(
                            "{:>{width}}".format("nomatch:", width=wnick), repr(line)
                        )
                        nomatchprinted = True
                    self._log("{:>{width}}".format("and:", width=wnick), repr(nextline))
                extralines.append(nextline)
            else:
                msg = f"remains unmatched: {line!r}"
                self._log(msg)
                self._fail(msg)
        self._log_output = []

    def no_fnmatch_line(self, pat: str) -> None:
        """Ensure captured lines do not match the given pattern, using ``fnmatch.fnmatch``.

        :param str pat: The pattern to match lines.
        """
        __tracebackhide__ = True
        self._no_match_line(pat, fnmatch, "fnmatch")

    def no_re_match_line(self, pat: str) -> None:
        """Ensure captured lines do not match the given pattern, using ``re.match``.

        :param str pat: The regular expression to match lines.
        """
        __tracebackhide__ = True
        self._no_match_line(
            pat, lambda name, pat: bool(re.match(pat, name)), "re.match"
        )

    def _no_match_line(
        self, pat: str, match_func: Callable[[str, str], bool], match_nickname: str
    ) -> None:
        """Ensure captured lines does not have a the given pattern, using ``fnmatch.fnmatch``.

        :param str pat: The pattern to match lines.
        """
        __tracebackhide__ = True
        nomatch_printed = False
        wnick = len(match_nickname) + 1
        for line in self.lines:
            if match_func(line, pat):
                msg = f"{match_nickname}: {pat!r}"
                self._log(msg)
                self._log("{:>{width}}".format("with:", width=wnick), repr(line))
                self._fail(msg)
            else:
                if not nomatch_printed:
                    self._log("{:>{width}}".format("nomatch:", width=wnick), repr(pat))
                    nomatch_printed = True
                self._log("{:>{width}}".format("and:", width=wnick), repr(line))
        self._log_output = []

    def _fail(self, msg: str) -> None:
        __tracebackhide__ = True
        log_text = self._log_text
        self._log_output = []
        pytest.fail(log_text)

    def str(self) -> str:
        """Return the entire original text."""
        return "\n".join(self.lines)

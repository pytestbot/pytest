"""Command line options, ini-file and conftest.py processing."""
import argparse
import collections.abc
import contextlib
import copy
import enum
import inspect
import os
import re
import shlex
import sys
import types
import warnings
from functools import lru_cache
from pathlib import Path
from types import TracebackType
from typing import Any
from typing import Callable
from typing import Dict
from typing import Generator
from typing import IO
from typing import Iterable
from typing import Iterator
from typing import List
from typing import Optional
from typing import Sequence
from typing import Set
from typing import TextIO
from typing import Tuple
from typing import Type
from typing import TYPE_CHECKING
from typing import Union

import attr
import py
from pluggy import HookimplMarker
from pluggy import HookspecMarker
from pluggy import PluginManager

import _pytest._code
import _pytest.deprecated
import _pytest.hookspec
from .exceptions import PrintHelp as PrintHelp
from .exceptions import UsageError as UsageError
from .findpaths import determine_setup
from _pytest._code import ExceptionInfo
from _pytest._code import filter_traceback
from _pytest._io import TerminalWriter
from _pytest.compat import final
from _pytest.compat import importlib_metadata
from _pytest.outcomes import fail
from _pytest.outcomes import Skipped
from _pytest.pathlib import bestrelpath
from _pytest.pathlib import import_path
from _pytest.pathlib import ImportMode
from _pytest.store import Store
from _pytest.warning_types import PytestConfigWarning

if TYPE_CHECKING:

    from _pytest._code.code import _TracebackStyle
    from _pytest.terminal import TerminalReporter
    from .argparsing import Argument


_PluggyPlugin = object
"""A type to represent plugin objects.

Plugins can be any namespace, so we can't narrow it down much, but we use an
alias to make the intent clear.

Ideally this type would be provided by pluggy itself.
"""


hookimpl = HookimplMarker("pytest")
hookspec = HookspecMarker("pytest")


class WarningFilterException(Exception):
    pass


@final
class ExitCode(enum.IntEnum):
    """Encodes the valid exit codes by pytest.

    Currently users and plugins may supply other exit codes as well.

    .. versionadded:: 5.0
    """

    #: Tests passed.
    OK = 0
    #: Tests failed.
    TESTS_FAILED = 1
    #: pytest was interrupted.
    INTERRUPTED = 2
    #: An internal error got in the way.
    INTERNAL_ERROR = 3
    #: pytest was misused.
    USAGE_ERROR = 4
    #: pytest couldn't find tests.
    NO_TESTS_COLLECTED = 5


class ConftestImportFailure(Exception):
    def __init__(
        self,
        path: py.path.local,
        excinfo: Tuple[Type[Exception], Exception, TracebackType],
    ) -> None:
        super().__init__(path, excinfo)
        self.path = path
        self.excinfo = excinfo

    def __str__(self) -> str:
        return "{}: {} (from {})".format(
            self.excinfo[0].__name__, self.excinfo[1], self.path
        )


def filter_traceback_for_conftest_import_failure(
    entry: _pytest._code.TracebackEntry,
) -> bool:
    """Filter tracebacks entries which point to pytest internals or importlib.

    Make a special case for importlib because we use it to import test modules and conftest files
    in _pytest.pathlib.import_path.
    """
    return filter_traceback(entry) and "importlib" not in str(entry.path).split(os.sep)


def main(
    args: Optional[Union[List[str], py.path.local]] = None,
    plugins: Optional[Sequence[Union[str, _PluggyPlugin]]] = None,
) -> Union[int, ExitCode]:
    """Perform an in-process test run.

    :param args: List of command line arguments.
    :param plugins: List of plugin objects to be auto-registered during initialization.

    :returns: An exit code.
    """
    try:
        try:
            config = _prepareconfig(args, plugins)
        except ConftestImportFailure as e:
            exc_info = ExceptionInfo(e.excinfo)
            tw = TerminalWriter(sys.stderr)
            tw.line(f"ImportError while loading conftest '{e.path}'.", red=True)
            exc_info.traceback = exc_info.traceback.filter(
                filter_traceback_for_conftest_import_failure
            )
            exc_repr = (
                exc_info.getrepr(style="short", chain=False)
                if exc_info.traceback
                else exc_info.exconly()
            )
            formatted_tb = str(exc_repr)
            for line in formatted_tb.splitlines():
                tw.line(line.rstrip(), red=True)
            return ExitCode.USAGE_ERROR
        else:
            try:
                ret: Union[ExitCode, int] = config.hook.pytest_cmdline_main(
                    config=config
                )
                try:
                    return ExitCode(ret)
                except ValueError:
                    return ret
            finally:
                config._ensure_unconfigure()
    except UsageError as e:
        tw = TerminalWriter(sys.stderr)
        for msg in e.args:
            tw.line(f"ERROR: {msg}\n", red=True)
        return ExitCode.USAGE_ERROR


def console_main() -> int:
    """The CLI entry point of pytest.

    This function is not meant for programmable use; use `main()` instead.
    """
    # https://docs.python.org/3/library/signal.html#note-on-sigpipe
    try:
        code = main()
        sys.stdout.flush()
        return code
    except BrokenPipeError:
        # Python flushes standard streams on exit; redirect remaining output
        # to devnull to avoid another BrokenPipeError at shutdown
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 1  # Python exits with error code 1 on EPIPE


class cmdline:  # compatibility namespace
    main = staticmethod(main)


def filename_arg(path: str, optname: str) -> str:
    """Argparse type validator for filename arguments.

    :path: Path of filename.
    :optname: Name of the option.
    """
    if os.path.isdir(path):
        raise UsageError(f"{optname} must be a filename, given: {path}")
    return path


def directory_arg(path: str, optname: str) -> str:
    """Argparse type validator for directory arguments.

    :path: Path of directory.
    :optname: Name of the option.
    """
    if not os.path.isdir(path):
        raise UsageError(f"{optname} must be a directory, given: {path}")
    return path


# Plugins that cannot be disabled via "-p no:X" currently.
essential_plugins = (
    "mark",
    "main",
    "runner",
    "fixtures",
    "helpconfig",  # Provides -p.
)

default_plugins = essential_plugins + (
    "python",
    "terminal",
    "debugging",
    "unittest",
    "capture",
    "skipping",
    "tmpdir",
    "monkeypatch",
    "recwarn",
    "pastebin",
    "nose",
    "assertion",
    "junitxml",
    "doctest",
    "cacheprovider",
    "freeze_support",
    "setuponly",
    "setupplan",
    "stepwise",
    "warnings",
    "logging",
    "reports",
    "faulthandler",
)

builtin_plugins = set(default_plugins)
builtin_plugins.add("pytester")


def get_config(
    args: Optional[List[str]] = None,
    plugins: Optional[Sequence[Union[str, _PluggyPlugin]]] = None,
) -> "Config":
    # subsequent calls to main will create a fresh instance
    pluginmanager = PytestPluginManager()
    config = Config(
        pluginmanager,
        invocation_params=Config.InvocationParams(
            args=args or (), plugins=plugins, dir=Path.cwd(),
        ),
    )

    if args is not None:
        # Handle any "-p no:plugin" args.
        pluginmanager.consider_preparse(args, exclude_only=True)

    for spec in default_plugins:
        pluginmanager.import_plugin(spec)

    return config


def get_plugin_manager() -> "PytestPluginManager":
    """Obtain a new instance of the
    :py:class:`_pytest.config.PytestPluginManager`, with default plugins
    already loaded.

    This function can be used by integration with other tools, like hooking
    into pytest to run tests into an IDE.
    """
    return get_config().pluginmanager


def _prepareconfig(
    args: Optional[Union[py.path.local, List[str]]] = None,
    plugins: Optional[Sequence[Union[str, _PluggyPlugin]]] = None,
) -> "Config":
    if args is None:
        args = sys.argv[1:]
    elif isinstance(args, py.path.local):
        args = [str(args)]
    elif not isinstance(args, list):
        msg = "`args` parameter expected to be a list of strings, got: {!r} (type: {})"
        raise TypeError(msg.format(args, type(args)))

    config = get_config(args, plugins)
    pluginmanager = config.pluginmanager
    try:
        if plugins:
            for plugin in plugins:
                if isinstance(plugin, str):
                    pluginmanager.consider_pluginarg(plugin)
                else:
                    pluginmanager.register(plugin)
        config = pluginmanager.hook.pytest_cmdline_parse(
            pluginmanager=pluginmanager, args=args
        )
        return config
    except BaseException:
        config._ensure_unconfigure()
        raise


@final
class PytestPluginManager(PluginManager):
    """A :py:class:`pluggy.PluginManager <pluggy.PluginManager>` with
    additional pytest-specific functionality:

    * Loading plugins from the command line, ``PYTEST_PLUGINS`` env variable and
      ``pytest_plugins`` global variables found in plugins being loaded.
    * ``conftest.py`` loading during start-up.
    """

    def __init__(self) -> None:
        import _pytest.assertion

        super().__init__("pytest")
        # The objects are module objects, only used generically.
        self._conftest_plugins: Set[types.ModuleType] = set()

        # State related to local conftest plugins.
        self._dirpath2confmods: Dict[py.path.local, List[types.ModuleType]] = {}
        self._conftestpath2mod: Dict[Path, types.ModuleType] = {}
        self._confcutdir: Optional[py.path.local] = None
        self._noconftest = False
        self._duplicatepaths: Set[py.path.local] = set()

        # plugins that were explicitly skipped with pytest.skip
        # list of (module name, skip reason)
        # previously we would issue a warning when a plugin was skipped, but
        # since we refactored warnings as first citizens of Config, they are
        # just stored here to be used later.
        self.skipped_plugins: List[Tuple[str, str]] = []

        self.add_hookspecs(_pytest.hookspec)
        self.register(self)
        if os.environ.get("PYTEST_DEBUG"):
            err: IO[str] = sys.stderr
            encoding: str = getattr(err, "encoding", "utf8")
            try:
                err = open(
                    os.dup(err.fileno()), mode=err.mode, buffering=1, encoding=encoding,
                )
            except Exception:
                pass
            self.trace.root.setwriter(err.write)
            self.enable_tracing()

        # Config._consider_importhook will set a real object if required.
        self.rewrite_hook = _pytest.assertion.DummyRewriteHook()
        # Used to know when we are importing conftests after the pytest_configure stage.
        self._configured = False

    def parse_hookimpl_opts(self, plugin: _PluggyPlugin, name: str):
        # pytest hooks are always prefixed with "pytest_",
        # so we avoid accessing possibly non-readable attributes
        # (see issue #1073).
        if not name.startswith("pytest_"):
            return
        # Ignore names which can not be hooks.
        if name == "pytest_plugins":
            return

        method = getattr(plugin, name)
        opts = super().parse_hookimpl_opts(plugin, name)

        # Consider only actual functions for hooks (#3775).
        if not inspect.isroutine(method):
            return

        # Collect unmarked hooks as long as they have the `pytest_' prefix.
        if opts is None and name.startswith("pytest_"):
            opts = {}
        if opts is not None:
            # TODO: DeprecationWarning, people should use hookimpl
            # https://github.com/pytest-dev/pytest/issues/4562
            known_marks = {m.name for m in getattr(method, "pytestmark", [])}

            for name in ("tryfirst", "trylast", "optionalhook", "hookwrapper"):
                opts.setdefault(name, hasattr(method, name) or name in known_marks)
        return opts

    def parse_hookspec_opts(self, module_or_class, name: str):
        opts = super().parse_hookspec_opts(module_or_class, name)
        if opts is None:
            method = getattr(module_or_class, name)

            if name.startswith("pytest_"):
                # todo: deprecate hookspec hacks
                # https://github.com/pytest-dev/pytest/issues/4562
                known_marks = {m.name for m in getattr(method, "pytestmark", [])}
                opts = {
                    "firstresult": hasattr(method, "firstresult")
                    or "firstresult" in known_marks,
                    "historic": hasattr(method, "historic")
                    or "historic" in known_marks,
                }
        return opts

    def register(
        self, plugin: _PluggyPlugin, name: Optional[str] = None
    ) -> Optional[str]:
        if name in _pytest.deprecated.DEPRECATED_EXTERNAL_PLUGINS:
            warnings.warn(
                PytestConfigWarning(
                    "{} plugin has been merged into the core, "
                    "please remove it from your requirements.".format(
                        name.replace("_", "-")
                    )
                )
            )
            return None
        ret: Optional[str] = super().register(plugin, name)
        if ret:
            self.hook.pytest_plugin_registered.call_historic(
                kwargs=dict(plugin=plugin, manager=self)
            )

            if isinstance(plugin, types.ModuleType):
                self.consider_module(plugin)
        return ret

    def getplugin(self, name: str):
        # Support deprecated naming because plugins (xdist e.g.) use it.
        plugin: Optional[_PluggyPlugin] = self.get_plugin(name)
        return plugin

    def hasplugin(self, name: str) -> bool:
        """Return whether a plugin with the given name is registered."""
        return bool(self.get_plugin(name))

    def pytest_configure(self, config: "Config") -> None:
        """:meta private:"""
        # XXX now that the pluginmanager exposes hookimpl(tryfirst...)
        # we should remove tryfirst/trylast as markers.
        config.addinivalue_line(
            "markers",
            "tryfirst: mark a hook implementation function such that the "
            "plugin machinery will try to call it first/as early as possible.",
        )
        config.addinivalue_line(
            "markers",
            "trylast: mark a hook implementation function such that the "
            "plugin machinery will try to call it last/as late as possible.",
        )
        self._configured = True

    #
    # Internal API for local conftest plugin handling.
    #
    def _set_initial_conftests(self, namespace: argparse.Namespace) -> None:
        """Load initial conftest files given a preparsed "namespace".

        As conftest files may add their own command line options which have
        arguments ('--my-opt somepath') we might get some false positives.
        All builtin and 3rd party plugins will have been loaded, however, so
        common options will not confuse our logic here.
        """
        current = py.path.local()
        self._confcutdir = (
            current.join(namespace.confcutdir, abs=True)
            if namespace.confcutdir
            else None
        )
        self._noconftest = namespace.noconftest
        self._using_pyargs = namespace.pyargs
        testpaths = namespace.file_or_dir
        foundanchor = False
        for testpath in testpaths:
            path = str(testpath)
            # remove node-id syntax
            i = path.find("::")
            if i != -1:
                path = path[:i]
            anchor = current.join(path, abs=1)
            if anchor.exists():  # we found some file object
                self._try_load_conftest(anchor, namespace.importmode)
                foundanchor = True
        if not foundanchor:
            self._try_load_conftest(current, namespace.importmode)

    def _try_load_conftest(
        self, anchor: py.path.local, importmode: Union[str, ImportMode]
    ) -> None:
        self._getconftestmodules(anchor, importmode)
        # let's also consider test* subdirs
        if anchor.check(dir=1):
            for x in anchor.listdir("test*"):
                if x.check(dir=1):
                    self._getconftestmodules(x, importmode)

    @lru_cache(maxsize=128)
    def _getconftestmodules(
        self, path: py.path.local, importmode: Union[str, ImportMode],
    ) -> List[types.ModuleType]:
        if self._noconftest:
            return []

        if path.isfile():
            directory = path.dirpath()
        else:
            directory = path

        # XXX these days we may rather want to use config.rootpath
        # and allow users to opt into looking into the rootdir parent
        # directories instead of requiring to specify confcutdir.
        clist = []
        for parent in directory.parts():
            if self._confcutdir and self._confcutdir.relto(parent):
                continue
            conftestpath = parent.join("conftest.py")
            if conftestpath.isfile():
                mod = self._importconftest(conftestpath, importmode)
                clist.append(mod)
        self._dirpath2confmods[directory] = clist
        return clist

    def _rget_with_confmod(
        self, name: str, path: py.path.local, importmode: Union[str, ImportMode],
    ) -> Tuple[types.ModuleType, Any]:
        modules = self._getconftestmodules(path, importmode)
        for mod in reversed(modules):
            try:
                return mod, getattr(mod, name)
            except AttributeError:
                continue
        raise KeyError(name)

    def _importconftest(
        self, conftestpath: py.path.local, importmode: Union[str, ImportMode],
    ) -> types.ModuleType:
        # Use a resolved Path object as key to avoid loading the same conftest
        # twice with build systems that create build directories containing
        # symlinks to actual files.
        # Using Path().resolve() is better than py.path.realpath because
        # it resolves to the correct path/drive in case-insensitive file systems (#5792)
        key = Path(str(conftestpath)).resolve()

        with contextlib.suppress(KeyError):
            return self._conftestpath2mod[key]

        pkgpath = conftestpath.pypkgpath()
        if pkgpath is None:
            _ensure_removed_sysmodule(conftestpath.purebasename)

        try:
            mod = import_path(conftestpath, mode=importmode)
        except Exception as e:
            assert e.__traceback__ is not None
            exc_info = (type(e), e, e.__traceback__)
            raise ConftestImportFailure(conftestpath, exc_info) from e

        self._check_non_top_pytest_plugins(mod, conftestpath)

        self._conftest_plugins.add(mod)
        self._conftestpath2mod[key] = mod
        dirpath = conftestpath.dirpath()
        if dirpath in self._dirpath2confmods:
            for path, mods in self._dirpath2confmods.items():
                if path and path.relto(dirpath) or path == dirpath:
                    assert mod not in mods
                    mods.append(mod)
        self.trace(f"loading conftestmodule {mod!r}")
        self.consider_conftest(mod)
        return mod

    def _check_non_top_pytest_plugins(
        self, mod: types.ModuleType, conftestpath: py.path.local,
    ) -> None:
        if (
            hasattr(mod, "pytest_plugins")
            and self._configured
            and not self._using_pyargs
        ):
            msg = (
                "Defining 'pytest_plugins' in a non-top-level conftest is no longer supported:\n"
                "It affects the entire test suite instead of just below the conftest as expected.\n"
                "  {}\n"
                "Please move it to a top level conftest file at the rootdir:\n"
                "  {}\n"
                "For more information, visit:\n"
                "  https://docs.pytest.org/en/stable/deprecations.html#pytest-plugins-in-non-top-level-conftest-files"
            )
            fail(msg.format(conftestpath, self._confcutdir), pytrace=False)

    #
    # API for bootstrapping plugin loading
    #
    #

    def consider_preparse(
        self, args: Sequence[str], *, exclude_only: bool = False
    ) -> None:
        i = 0
        n = len(args)
        while i < n:
            opt = args[i]
            i += 1
            if isinstance(opt, str):
                if opt == "-p":
                    try:
                        parg = args[i]
                    except IndexError:
                        return
                    i += 1
                elif opt.startswith("-p"):
                    parg = opt[2:]
                else:
                    continue
                if exclude_only and not parg.startswith("no:"):
                    continue
                self.consider_pluginarg(parg)

    def consider_pluginarg(self, arg: str) -> None:
        if arg.startswith("no:"):
            name = arg[3:]
            if name in essential_plugins:
                raise UsageError("plugin %s cannot be disabled" % name)

            # PR #4304: remove stepwise if cacheprovider is blocked.
            if name == "cacheprovider":
                self.set_blocked("stepwise")
                self.set_blocked("pytest_stepwise")

            self.set_blocked(name)
            if not name.startswith("pytest_"):
                self.set_blocked("pytest_" + name)
        else:
            name = arg
            # Unblock the plugin.  None indicates that it has been blocked.
            # There is no interface with pluggy for this.
            if self._name2plugin.get(name, -1) is None:
                del self._name2plugin[name]
            if not name.startswith("pytest_"):
                if self._name2plugin.get("pytest_" + name, -1) is None:
                    del self._name2plugin["pytest_" + name]
            self.import_plugin(arg, consider_entry_points=True)

    def consider_conftest(self, conftestmodule: types.ModuleType) -> None:
        self.register(conftestmodule, name=conftestmodule.__file__)

    def consider_env(self) -> None:
        self._import_plugin_specs(os.environ.get("PYTEST_PLUGINS"))

    def consider_module(self, mod: types.ModuleType) -> None:
        self._import_plugin_specs(getattr(mod, "pytest_plugins", []))

    def _import_plugin_specs(
        self, spec: Union[None, types.ModuleType, str, Sequence[str]]
    ) -> None:
        plugins = _get_plugin_specs_as_list(spec)
        for import_spec in plugins:
            self.import_plugin(import_spec)

    def import_plugin(self, modname: str, consider_entry_points: bool = False) -> None:
        """Import a plugin with ``modname``.

        If ``consider_entry_points`` is True, entry point names are also
        considered to find a plugin.
        """
        # Most often modname refers to builtin modules, e.g. "pytester",
        # "terminal" or "capture".  Those plugins are registered under their
        # basename for historic purposes but must be imported with the
        # _pytest prefix.
        assert isinstance(modname, str), (
            "module name as text required, got %r" % modname
        )
        if self.is_blocked(modname) or self.get_plugin(modname) is not None:
            return

        importspec = "_pytest." + modname if modname in builtin_plugins else modname
        self.rewrite_hook.mark_rewrite(importspec)

        if consider_entry_points:
            loaded = self.load_setuptools_entrypoints("pytest11", name=modname)
            if loaded:
                return

        try:
            __import__(importspec)
        except ImportError as e:
            raise ImportError(
                'Error importing plugin "{}": {}'.format(modname, str(e.args[0]))
            ).with_traceback(e.__traceback__) from e

        except Skipped as e:
            self.skipped_plugins.append((modname, e.msg or ""))
        else:
            mod = sys.modules[importspec]
            self.register(mod, modname)


def _get_plugin_specs_as_list(
    specs: Union[None, types.ModuleType, str, Sequence[str]]
) -> List[str]:
    """Parse a plugins specification into a list of plugin names."""
    # None means empty.
    if specs is None:
        return []
    # Workaround for #3899 - a submodule which happens to be called "pytest_plugins".
    if isinstance(specs, types.ModuleType):
        return []
    # Comma-separated list.
    if isinstance(specs, str):
        return specs.split(",") if specs else []
    # Direct specification.
    if isinstance(specs, collections.abc.Sequence):
        return list(specs)
    raise UsageError(
        "Plugins may be specified as a sequence or a ','-separated string of plugin names. Got: %r"
        % specs
    )


def _ensure_removed_sysmodule(modname: str) -> None:
    try:
        del sys.modules[modname]
    except KeyError:
        pass


class Notset:
    def __repr__(self):
        return "<NOTSET>"


notset = Notset()


def _iter_rewritable_modules(package_files: Iterable[str]) -> Iterator[str]:
    """Given an iterable of file names in a source distribution, return the "names" that should
    be marked for assertion rewrite.

    For example the package "pytest_mock/__init__.py" should be added as "pytest_mock" in
    the assertion rewrite mechanism.

    This function has to deal with dist-info based distributions and egg based distributions
    (which are still very much in use for "editable" installs).

    Here are the file names as seen in a dist-info based distribution:

        pytest_mock/__init__.py
        pytest_mock/_version.py
        pytest_mock/plugin.py
        pytest_mock.egg-info/PKG-INFO

    Here are the file names as seen in an egg based distribution:

        src/pytest_mock/__init__.py
        src/pytest_mock/_version.py
        src/pytest_mock/plugin.py
        src/pytest_mock.egg-info/PKG-INFO
        LICENSE
        setup.py

    We have to take in account those two distribution flavors in order to determine which
    names should be considered for assertion rewriting.

    More information:
        https://github.com/pytest-dev/pytest-mock/issues/167
    """
    package_files = list(package_files)
    seen_some = False
    for fn in package_files:
        is_simple_module = "/" not in fn and fn.endswith(".py")
        is_package = fn.count("/") == 1 and fn.endswith("__init__.py")
        if is_simple_module:
            module_name, _ = os.path.splitext(fn)
            # we ignore "setup.py" at the root of the distribution
            if module_name != "setup":
                seen_some = True
                yield module_name
        elif is_package:
            package_name = os.path.dirname(fn)
            seen_some = True
            yield package_name

    if not seen_some:
        # At this point we did not find any packages or modules suitable for assertion
        # rewriting, so we try again by stripping the first path component (to account for
        # "src" based source trees for example).
        # This approach lets us have the common case continue to be fast, as egg-distributions
        # are rarer.
        new_package_files = []
        for fn in package_files:
            parts = fn.split("/")
            new_fn = "/".join(parts[1:])
            if new_fn:
                new_package_files.append(new_fn)
        if new_package_files:
            yield from _iter_rewritable_modules(new_package_files)


def _args_converter(args: Iterable[str]) -> Tuple[str, ...]:
    return tuple(args)


@final
class Config:
    """Access to configuration values, pluginmanager and plugin hooks.

    :param PytestPluginManager pluginmanager:

    :param InvocationParams invocation_params:
        Object containing parameters regarding the :func:`pytest.main`
        invocation.
    """

    @final
    @attr.s(frozen=True)
    class InvocationParams:
        """Holds parameters passed during :func:`pytest.main`.

        The object attributes are read-only.

        .. versionadded:: 5.1

        .. note::

            Note that the environment variable ``PYTEST_ADDOPTS`` and the ``addopts``
            ini option are handled by pytest, not being included in the ``args`` attribute.

            Plugins accessing ``InvocationParams`` must be aware of that.
        """

        args = attr.ib(type=Tuple[str, ...], converter=_args_converter)
        """The command-line arguments as passed to :func:`pytest.main`.

        :type: Tuple[str, ...]
        """
        plugins = attr.ib(type=Optional[Sequence[Union[str, _PluggyPlugin]]])
        """Extra plugins, might be `None`.

        :type: Optional[Sequence[Union[str, plugin]]]
        """
        dir = attr.ib(type=Path)
        """The directory from which :func:`pytest.main` was invoked.

        :type: pathlib.Path
        """

    def __init__(
        self,
        pluginmanager: PytestPluginManager,
        *,
        invocation_params: Optional[InvocationParams] = None,
    ) -> None:
        from .argparsing import Parser, FILE_OR_DIR

        if invocation_params is None:
            invocation_params = self.InvocationParams(
                args=(), plugins=None, dir=Path.cwd()
            )

        self.option = argparse.Namespace()
        """Access to command line option as attributes.

        :type: argparse.Namespace
        """

        self.invocation_params = invocation_params
        """The parameters with which pytest was invoked.

        :type: InvocationParams
        """

        _a = FILE_OR_DIR
        self._parser = Parser(
            usage=f"%(prog)s [options] [{_a}] [{_a}] [...]",
            processopt=self._processopt,
        )
        self.pluginmanager = pluginmanager
        """The plugin manager handles plugin registration and hook invocation.

        :type: PytestPluginManager
        """

        self.trace = self.pluginmanager.trace.root.get("config")
        self.hook = self.pluginmanager.hook
        self._inicache: Dict[str, Any] = {}
        self._override_ini: Sequence[str] = ()
        self._opt2dest: Dict[str, str] = {}
        self._cleanup: List[Callable[[], None]] = []
        # A place where plugins can store information on the config for their
        # own use. Currently only intended for internal plugins.
        self._store = Store()
        self.pluginmanager.register(self, "pytestconfig")
        self._configured = False
        self.hook.pytest_addoption.call_historic(
            kwargs=dict(parser=self._parser, pluginmanager=self.pluginmanager)
        )

        if TYPE_CHECKING:
            from _pytest.cacheprovider import Cache

            self.cache: Optional[Cache] = None

    @property
    def invocation_dir(self) -> py.path.local:
        """The directory from which pytest was invoked.

        Prefer to use :attr:`invocation_params.dir <InvocationParams.dir>`,
        which is a :class:`pathlib.Path`.

        :type: py.path.local
        """
        return py.path.local(str(self.invocation_params.dir))

    @property
    def rootpath(self) -> Path:
        """The path to the :ref:`rootdir <rootdir>`.

        :type: pathlib.Path

        .. versionadded:: 6.1
        """
        return self._rootpath

    @property
    def rootdir(self) -> py.path.local:
        """The path to the :ref:`rootdir <rootdir>`.

        Prefer to use :attr:`rootpath`, which is a :class:`pathlib.Path`.

        :type: py.path.local
        """
        return py.path.local(str(self.rootpath))

    @property
    def inipath(self) -> Optional[Path]:
        """The path to the :ref:`configfile <configfiles>`.

        :type: Optional[pathlib.Path]

        .. versionadded:: 6.1
        """
        return self._inipath

    @property
    def inifile(self) -> Optional[py.path.local]:
        """The path to the :ref:`configfile <configfiles>`.

        Prefer to use :attr:`inipath`, which is a :class:`pathlib.Path`.

        :type: Optional[py.path.local]
        """
        return py.path.local(str(self.inipath)) if self.inipath else None

    def add_cleanup(self, func: Callable[[], None]) -> None:
        """Add a function to be called when the config object gets out of
        use (usually coninciding with pytest_unconfigure)."""
        self._cleanup.append(func)

    def _do_configure(self) -> None:
        assert not self._configured
        self._configured = True
        with warnings.catch_warnings():
            warnings.simplefilter("default")
            self.hook.pytest_configure.call_historic(kwargs=dict(config=self))

    def _ensure_unconfigure(self) -> None:
        if self._configured:
            self._configured = False
            self.hook.pytest_unconfigure(config=self)
            self.hook.pytest_configure._call_history = []
        while self._cleanup:
            fin = self._cleanup.pop()
            fin()

    def get_terminal_writer(self) -> TerminalWriter:
        terminalreporter: TerminalReporter = self.pluginmanager.get_plugin(
            "terminalreporter"
        )
        return terminalreporter._tw

    def pytest_cmdline_parse(
        self, pluginmanager: PytestPluginManager, args: List[str]
    ) -> "Config":
        try:
            self.parse(args)
        except UsageError:

            # Handle --version and --help here in a minimal fashion.
            # This gets done via helpconfig normally, but its
            # pytest_cmdline_main is not called in case of errors.
            if getattr(self.option, "version", False) or "--version" in args:
                from _pytest.helpconfig import showversion

                showversion(self)
            elif (
                getattr(self.option, "help", False) or "--help" in args or "-h" in args
            ):
                self._parser._getparser().print_help()
                sys.stdout.write(
                    "\nNOTE: displaying only minimal help due to UsageError.\n\n"
                )

            raise

        return self

    def notify_exception(
        self,
        excinfo: ExceptionInfo[BaseException],
        option: Optional[argparse.Namespace] = None,
    ) -> None:
        if option and getattr(option, "fulltrace", False):
            style: _TracebackStyle = "long"
        else:
            style = "native"
        excrepr = excinfo.getrepr(
            funcargs=True, showlocals=getattr(option, "showlocals", False), style=style
        )
        res = self.hook.pytest_internalerror(excrepr=excrepr, excinfo=excinfo)
        if not any(res):
            for line in str(excrepr).split("\n"):
                sys.stderr.write("INTERNALERROR> %s\n" % line)
                sys.stderr.flush()

    def cwd_relative_nodeid(self, nodeid: str) -> str:
        # nodeid's are relative to the rootpath, compute relative to cwd.
        if self.invocation_params.dir != self.rootpath:
            fullpath = self.rootpath / nodeid
            nodeid = bestrelpath(self.invocation_params.dir, fullpath)
        return nodeid

    @classmethod
    def fromdictargs(cls, option_dict, args) -> "Config":
        """Constructor usable for subprocesses."""
        config = get_config(args)
        config.option.__dict__.update(option_dict)
        config.parse(args, addopts=False)
        for x in config.option.plugins:
            config.pluginmanager.consider_pluginarg(x)
        return config

    def _processopt(self, opt: "Argument") -> None:
        for name in opt._short_opts + opt._long_opts:
            self._opt2dest[name] = opt.dest

        if hasattr(opt, "default"):
            if not hasattr(self.option, opt.dest):
                setattr(self.option, opt.dest, opt.default)

    @hookimpl(trylast=True)
    def pytest_load_initial_conftests(self, early_config: "Config") -> None:
        self.pluginmanager._set_initial_conftests(early_config.known_args_namespace)

    def _initini(self, args: Sequence[str]) -> None:
        ns, unknown_args = self._parser.parse_known_and_unknown_args(
            args, namespace=copy.copy(self.option)
        )
        rootpath, inipath, inicfg = determine_setup(
            ns.inifilename,
            ns.file_or_dir + unknown_args,
            rootdir_cmd_arg=ns.rootdir or None,
            config=self,
        )
        self._rootpath = rootpath
        self._inipath = inipath
        self.inicfg = inicfg
        self._parser.extra_info["rootdir"] = str(self.rootpath)
        self._parser.extra_info["inifile"] = str(self.inipath)
        self._parser.addini("addopts", "extra command line options", "args")
        self._parser.addini("minversion", "minimally required pytest version")
        self._parser.addini(
            "required_plugins",
            "plugins that must be present for pytest to run",
            type="args",
            default=[],
        )
        self._override_ini = ns.override_ini or ()

    def _consider_importhook(self, args: Sequence[str]) -> None:
        """Install the PEP 302 import hook if using assertion rewriting.

        Needs to parse the --assert=<mode> option from the commandline
        and find all the installed plugins to mark them for rewriting
        by the importhook.
        """
        ns, unknown_args = self._parser.parse_known_and_unknown_args(args)
        mode = getattr(ns, "assertmode", "plain")
        if mode == "rewrite":
            import _pytest.assertion

            try:
                hook = _pytest.assertion.install_importhook(self)
            except SystemError:
                mode = "plain"
            else:
                self._mark_plugins_for_rewrite(hook)
        self._warn_about_missing_assertion(mode)

    def _mark_plugins_for_rewrite(self, hook) -> None:
        """Given an importhook, mark for rewrite any top-level
        modules or packages in the distribution package for
        all pytest plugins."""
        self.pluginmanager.rewrite_hook = hook

        if os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD"):
            # We don't autoload from setuptools entry points, no need to continue.
            return

        package_files = (
            str(file)
            for dist in importlib_metadata.distributions()
            if any(ep.group == "pytest11" for ep in dist.entry_points)
            for file in dist.files or []
        )

        for name in _iter_rewritable_modules(package_files):
            hook.mark_rewrite(name)

    def _validate_args(self, args: List[str], via: str) -> List[str]:
        """Validate known args."""
        self._parser._config_source_hint = via  # type: ignore
        try:
            self._parser.parse_known_and_unknown_args(
                args, namespace=copy.copy(self.option)
            )
        finally:
            del self._parser._config_source_hint  # type: ignore

        return args

    def _preparse(self, args: List[str], addopts: bool = True) -> None:
        if addopts:
            env_addopts = os.environ.get("PYTEST_ADDOPTS", "")
            if len(env_addopts):
                args[:] = (
                    self._validate_args(shlex.split(env_addopts), "via PYTEST_ADDOPTS")
                    + args
                )
        self._initini(args)
        if addopts:
            args[:] = (
                self._validate_args(self.getini("addopts"), "via addopts config") + args
            )

        self.known_args_namespace = self._parser.parse_known_args(
            args, namespace=copy.copy(self.option)
        )
        self._checkversion()
        self._consider_importhook(args)
        self.pluginmanager.consider_preparse(args, exclude_only=False)
        if not os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD"):
            # Don't autoload from setuptools entry point. Only explicitly specified
            # plugins are going to be loaded.
            self.pluginmanager.load_setuptools_entrypoints("pytest11")
        self.pluginmanager.consider_env()

        self.known_args_namespace = self._parser.parse_known_args(
            args, namespace=copy.copy(self.known_args_namespace)
        )

        self._validate_plugins()
        self._warn_about_skipped_plugins()

        if self.known_args_namespace.confcutdir is None and self.inipath is not None:
            confcutdir = str(self.inipath.parent)
            self.known_args_namespace.confcutdir = confcutdir
        try:
            self.hook.pytest_load_initial_conftests(
                early_config=self, args=args, parser=self._parser
            )
        except ConftestImportFailure as e:
            if self.known_args_namespace.help or self.known_args_namespace.version:
                # we don't want to prevent --help/--version to work
                # so just let is pass and print a warning at the end
                self.issue_config_time_warning(
                    PytestConfigWarning(f"could not load initial conftests: {e.path}"),
                    stacklevel=2,
                )
            else:
                raise

    @hookimpl(hookwrapper=True)
    def pytest_collection(self) -> Generator[None, None, None]:
        """Validate invalid ini keys after collection is done so we take in account
        options added by late-loading conftest files."""
        yield
        self._validate_config_options()

    def _checkversion(self) -> None:
        import pytest

        minver = self.inicfg.get("minversion", None)
        if minver:
            # Imported lazily to improve start-up time.
            from packaging.version import Version

            if not isinstance(minver, str):
                raise pytest.UsageError(
                    "%s: 'minversion' must be a single value" % self.inipath
                )

            if Version(minver) > Version(pytest.__version__):
                raise pytest.UsageError(
                    "%s: 'minversion' requires pytest-%s, actual pytest-%s'"
                    % (self.inipath, minver, pytest.__version__,)
                )

    def _validate_config_options(self) -> None:
        for key in sorted(self._get_unknown_ini_keys()):
            self._warn_or_fail_if_strict(f"Unknown config option: {key}\n")

    def _validate_plugins(self) -> None:
        required_plugins = sorted(self.getini("required_plugins"))
        if not required_plugins:
            return

        # Imported lazily to improve start-up time.
        from packaging.version import Version
        from packaging.requirements import InvalidRequirement, Requirement

        plugin_info = self.pluginmanager.list_plugin_distinfo()
        plugin_dist_info = {dist.project_name: dist.version for _, dist in plugin_info}

        missing_plugins = []
        for required_plugin in required_plugins:
            try:
                spec = Requirement(required_plugin)
            except InvalidRequirement:
                missing_plugins.append(required_plugin)
                continue

            if spec.name not in plugin_dist_info:
                missing_plugins.append(required_plugin)
            elif Version(plugin_dist_info[spec.name]) not in spec.specifier:
                missing_plugins.append(required_plugin)

        if missing_plugins:
            raise UsageError(
                "Missing required plugins: {}".format(", ".join(missing_plugins)),
            )

    def _warn_or_fail_if_strict(self, message: str) -> None:
        if self.known_args_namespace.strict_config:
            raise UsageError(message)

        self.issue_config_time_warning(PytestConfigWarning(message), stacklevel=3)

    def _get_unknown_ini_keys(self) -> List[str]:
        parser_inicfg = self._parser._inidict
        return [name for name in self.inicfg if name not in parser_inicfg]

    def parse(self, args: List[str], addopts: bool = True) -> None:
        # Parse given cmdline arguments into this config object.
        assert not hasattr(
            self, "args"
        ), "can only parse cmdline args at most once per Config object"
        self.hook.pytest_addhooks.call_historic(
            kwargs=dict(pluginmanager=self.pluginmanager)
        )
        self._preparse(args, addopts=addopts)
        # XXX deprecated hook:
        self.hook.pytest_cmdline_preparse(config=self, args=args)
        self._parser.after_preparse = True  # type: ignore
        try:
            args = self._parser.parse_setoption(
                args, self.option, namespace=self.option
            )
            if not args:
                if self.invocation_params.dir == self.rootpath:
                    args = self.getini("testpaths")
                if not args:
                    args = [str(self.invocation_params.dir)]
            self.args = args
        except PrintHelp:
            pass

    def issue_config_time_warning(self, warning: Warning, stacklevel: int) -> None:
        """Issue and handle a warning during the "configure" stage.

        During ``pytest_configure`` we can't capture warnings using the ``catch_warnings_for_item``
        function because it is not possible to have hookwrappers around ``pytest_configure``.

        This function is mainly intended for plugins that need to issue warnings during
        ``pytest_configure`` (or similar stages).

        :param warning: The warning instance.
        :param stacklevel: stacklevel forwarded to warnings.warn.
        """
        if self.pluginmanager.is_blocked("warnings"):
            return

        cmdline_filters = self.known_args_namespace.pythonwarnings or []
        config_filters = self.getini("filterwarnings")

        with warnings.catch_warnings(record=True) as records:
            warnings.simplefilter("always", type(warning))
            apply_warning_filters(config_filters, cmdline_filters)
            warnings.warn(warning, stacklevel=stacklevel)

        if records:
            frame = sys._getframe(stacklevel - 1)
            location = frame.f_code.co_filename, frame.f_lineno, frame.f_code.co_name
            self.hook.pytest_warning_captured.call_historic(
                kwargs=dict(
                    warning_message=records[0],
                    when="config",
                    item=None,
                    location=location,
                )
            )
            self.hook.pytest_warning_recorded.call_historic(
                kwargs=dict(
                    warning_message=records[0],
                    when="config",
                    nodeid="",
                    location=location,
                )
            )

    def addinivalue_line(self, name: str, line: str) -> None:
        """Add a line to an ini-file option. The option must have been
        declared but might not yet be set in which case the line becomes
        the first line in its value."""
        x = self.getini(name)
        assert isinstance(x, list)
        x.append(line)  # modifies the cached list inline

    def getini(self, name: str):
        """Return configuration value from an :ref:`ini file <configfiles>`.

        If the specified name hasn't been registered through a prior
        :py:func:`parser.addini <_pytest.config.argparsing.Parser.addini>`
        call (usually from a plugin), a ValueError is raised.
        """
        try:
            return self._inicache[name]
        except KeyError:
            self._inicache[name] = val = self._getini(name)
            return val

    def _getini(self, name: str):
        try:
            description, type, default = self._parser._inidict[name]
        except KeyError as e:
            raise ValueError(f"unknown configuration value: {name!r}") from e
        override_value = self._get_override_ini_value(name)
        if override_value is None:
            try:
                value = self.inicfg[name]
            except KeyError:
                if default is not None:
                    return default
                if type is None:
                    return ""
                return []
        else:
            value = override_value
        # Coerce the values based on types.
        #
        # Note: some coercions are only required if we are reading from .ini files, because
        # the file format doesn't contain type information, but when reading from toml we will
        # get either str or list of str values (see _parse_ini_config_from_pyproject_toml).
        # For example:
        #
        #   ini:
        #     a_line_list = "tests acceptance"
        #   in this case, we need to split the string to obtain a list of strings.
        #
        #   toml:
        #     a_line_list = ["tests", "acceptance"]
        #   in this case, we already have a list ready to use.
        #
        if type == "pathlist":
            # TODO: This assert is probably not valid in all cases.
            assert self.inipath is not None
            dp = self.inipath.parent
            input_values = shlex.split(value) if isinstance(value, str) else value
            return [py.path.local(str(dp / x)) for x in input_values]
        elif type == "args":
            return shlex.split(value) if isinstance(value, str) else value
        elif type == "linelist":
            if isinstance(value, str):
                return [t for t in map(lambda x: x.strip(), value.split("\n")) if t]
            else:
                return value
        elif type == "bool":
            return _strtobool(str(value).strip())
        else:
            assert type in [None, "string"]
            return value

    def _getconftest_pathlist(
        self, name: str, path: py.path.local
    ) -> Optional[List[py.path.local]]:
        try:
            mod, relroots = self.pluginmanager._rget_with_confmod(
                name, path, self.getoption("importmode")
            )
        except KeyError:
            return None
        modpath = py.path.local(mod.__file__).dirpath()
        values: List[py.path.local] = []
        for relroot in relroots:
            if not isinstance(relroot, py.path.local):
                relroot = relroot.replace("/", os.sep)
                relroot = modpath.join(relroot, abs=True)
            values.append(relroot)
        return values

    def _get_override_ini_value(self, name: str) -> Optional[str]:
        value = None
        # override_ini is a list of "ini=value" options.
        # Always use the last item if multiple values are set for same ini-name,
        # e.g. -o foo=bar1 -o foo=bar2 will set foo to bar2.
        for ini_config in self._override_ini:
            try:
                key, user_ini_value = ini_config.split("=", 1)
            except ValueError as e:
                raise UsageError(
                    "-o/--override-ini expects option=value style (got: {!r}).".format(
                        ini_config
                    )
                ) from e
            else:
                if key == name:
                    value = user_ini_value
        return value

    def getoption(self, name: str, default=notset, skip: bool = False):
        """Return command line option value.

        :param name: Name of the option.  You may also specify
            the literal ``--OPT`` option instead of the "dest" option name.
        :param default: Default value if no option of that name exists.
        :param skip: If True, raise pytest.skip if option does not exists
            or has a None value.
        """
        name = self._opt2dest.get(name, name)
        try:
            val = getattr(self.option, name)
            if val is None and skip:
                raise AttributeError(name)
            return val
        except AttributeError as e:
            if default is not notset:
                return default
            if skip:
                import pytest

                pytest.skip(f"no {name!r} option found")
            raise ValueError(f"no option named {name!r}") from e

    def getvalue(self, name: str, path=None):
        """Deprecated, use getoption() instead."""
        return self.getoption(name)

    def getvalueorskip(self, name: str, path=None):
        """Deprecated, use getoption(skip=True) instead."""
        return self.getoption(name, skip=True)

    def _warn_about_missing_assertion(self, mode: str) -> None:
        if not _assertion_supported():
            if mode == "plain":
                warning_text = (
                    "ASSERTIONS ARE NOT EXECUTED"
                    " and FAILING TESTS WILL PASS.  Are you"
                    " using python -O?"
                )
            else:
                warning_text = (
                    "assertions not in test modules or"
                    " plugins will be ignored"
                    " because assert statements are not executed "
                    "by the underlying Python interpreter "
                    "(are you using python -O?)\n"
                )
            self.issue_config_time_warning(
                PytestConfigWarning(warning_text), stacklevel=3,
            )

    def _warn_about_skipped_plugins(self) -> None:
        for module_name, msg in self.pluginmanager.skipped_plugins:
            self.issue_config_time_warning(
                PytestConfigWarning(f"skipped plugin {module_name!r}: {msg}"),
                stacklevel=2,
            )


def _assertion_supported() -> bool:
    try:
        assert False
    except AssertionError:
        return True
    else:
        return False  # type: ignore[unreachable]


def create_terminal_writer(
    config: Config, file: Optional[TextIO] = None
) -> TerminalWriter:
    """Create a TerminalWriter instance configured according to the options
    in the config object.

    Every code which requires a TerminalWriter object and has access to a
    config object should use this function.
    """
    tw = TerminalWriter(file=file)

    if config.option.color == "yes":
        tw.hasmarkup = True
    elif config.option.color == "no":
        tw.hasmarkup = False

    if config.option.code_highlight == "yes":
        tw.code_highlight = True
    elif config.option.code_highlight == "no":
        tw.code_highlight = False

    return tw


def _strtobool(val: str) -> bool:
    """Convert a string representation of truth to True or False.

    True values are 'y', 'yes', 't', 'true', 'on', and '1'; false values
    are 'n', 'no', 'f', 'false', 'off', and '0'.  Raises ValueError if
    'val' is anything else.

    .. note:: Copied from distutils.util.
    """
    val = val.lower()
    if val in ("y", "yes", "t", "true", "on", "1"):
        return True
    elif val in ("n", "no", "f", "false", "off", "0"):
        return False
    else:
        raise ValueError(f"invalid truth value {val!r}")


@lru_cache(maxsize=50)
def parse_warning_filter(
    arg: str, *, escape: bool
) -> Tuple[str, str, Type[Warning], str, int]:
    """Parse a warnings filter string.

    This is copied from warnings._setoption, but does not apply the filter,
    only parses it, and makes the escaping optional.
    """
    parts = arg.split(":")
    if len(parts) > 5:
        raise warnings._OptionError(f"too many fields (max 5): {arg!r}")
    while len(parts) < 5:
        parts.append("")
    action_, message, category_, module, lineno_ = [s.strip() for s in parts]
    action: str = warnings._getaction(action_)  # type: ignore[attr-defined]
    try:
        category: Type[Warning] = warnings._getcategory(category_)  # type: ignore[attr-defined] # noqa: E501
    except Exception as e:
        raise WarningFilterException(
            f"Error while processing warning rule '{category_}'. Please make "
            "sure importing the warning classes defined in filterwarning "
            "doesn't trigger an exception."
        ) from e
    if message and escape:
        message = re.escape(message)
    if module and escape:
        module = re.escape(module) + r"\Z"
    if lineno_:
        try:
            lineno = int(lineno_)
            if lineno < 0:
                raise ValueError
        except (ValueError, OverflowError) as e:
            raise warnings._OptionError(f"invalid lineno {lineno_!r}") from e
    else:
        lineno = 0
    return action, message, category, module, lineno


def apply_warning_filters(
    config_filters: Iterable[str], cmdline_filters: Iterable[str]
) -> None:
    """Applies pytest-configured filters to the warnings module"""
    # Filters should have this precedence: cmdline options, config.
    # Filters should be applied in the inverse order of precedence.
    for arg in config_filters:
        warnings.filterwarnings(*parse_warning_filter(arg, escape=False))

    for arg in cmdline_filters:
        warnings.filterwarnings(*parse_warning_filter(arg, escape=True))

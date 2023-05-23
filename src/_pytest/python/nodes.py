import fnmatch
import inspect
import os
import sys
import types
import warnings
from functools import partial
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Generator
from typing import Iterable
from typing import Iterator
from typing import List
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Set
from typing import Tuple
from typing import Union

import _pytest
from .metafunc import CallSpec2
from .metafunc import Metafunc
from _pytest import fixtures
from _pytest import nodes
from _pytest._code import ExceptionInfo
from _pytest._code import filter_traceback
from _pytest._code import getfslineno
from _pytest._code.code import TerminalRepr
from _pytest.compat import get_real_func
from _pytest.compat import getimfunc
from _pytest.compat import LEGACY_PATH
from _pytest.compat import NOTSET
from _pytest.compat import safe_getattr
from _pytest.config import Config
from _pytest.deprecated import FSCOLLECTOR_GETHOOKPROXY_ISINITPATH
from _pytest.deprecated import NOSE_SUPPORT_METHOD
from _pytest.fixtures import FuncFixtureInfo
from _pytest.main import Session
from _pytest.mark.structures import get_unpacked_marks
from _pytest.outcomes import skip
from _pytest.pathlib import fnmatch_ex
from _pytest.pathlib import import_path
from _pytest.pathlib import ImportPathMismatchError
from _pytest.pathlib import parts
from _pytest.pathlib import visit
from _pytest.warning_types import PytestCollectionWarning


def path_matches_patterns(path: Path, patterns: Iterable[str]) -> bool:
    """Return whether path matches any of the patterns in the list of globs given."""
    return any(fnmatch_ex(pattern, path) for pattern in patterns)


class PyobjMixin(nodes.Node):
    """this mix-in inherits from Node to carry over the typing information

    as its intended to always mix in before a node
    its position in the mro is unaffected"""

    _ALLOW_MARKERS = True

    @property
    def module(self):
        """Python module object this node was collected from (can be None)."""
        node = self.getparent(Module)
        return node.obj if node is not None else None

    @property
    def cls(self):
        """Python class object this node was collected from (can be None)."""
        node = self.getparent(Class)
        return node.obj if node is not None else None

    @property
    def instance(self):
        """Python instance object the function is bound to.

        Returns None if not a test method, e.g. for a standalone test function,
        a staticmethod, a class or a module.
        """
        node = self.getparent(Function)
        return getattr(node.obj, "__self__", None) if node is not None else None

    @property
    def obj(self):
        """Underlying Python object."""
        obj = getattr(self, "_obj", None)
        if obj is None:
            self._obj = obj = self._getobj()
            # XXX evil hack
            # used to avoid Function marker duplication
            if self._ALLOW_MARKERS:
                self.own_markers.extend(get_unpacked_marks(self.obj))
                # This assumes that `obj` is called before there is a chance
                # to add custom keys to `self.keywords`, so no fear of overriding.
                self.keywords.update((mark.name, mark) for mark in self.own_markers)
        return obj

    @obj.setter
    def obj(self, value):
        self._obj = value

    def _getobj(self):
        """Get the underlying Python object. May be overwritten by subclasses."""
        # TODO: Improve the type of `parent` such that assert/ignore aren't needed.
        assert self.parent is not None
        obj = self.parent.obj  # type: ignore[attr-defined]
        return getattr(obj, self.name)

    def getmodpath(self, stopatmodule: bool = True, includemodule: bool = False) -> str:
        """Return Python path relative to the containing module."""
        chain = self.listchain()
        chain.reverse()
        parts = []
        for node in chain:
            name = node.name
            if isinstance(node, Module):
                name = os.path.splitext(name)[0]
                if stopatmodule:
                    if includemodule:
                        parts.append(name)
                    break
            parts.append(name)
        parts.reverse()
        return ".".join(parts)

    def reportinfo(self) -> Tuple[Union["os.PathLike[str]", str], Optional[int], str]:
        # XXX caching?
        obj = self.obj
        compat_co_firstlineno = getattr(obj, "compat_co_firstlineno", None)
        if isinstance(compat_co_firstlineno, int):
            # nose compatibility
            file_path = sys.modules[obj.__module__].__file__
            assert file_path is not None
            if file_path.endswith(".pyc"):
                file_path = file_path[:-1]
            path: Union["os.PathLike[str]", str] = file_path
            lineno = compat_co_firstlineno
        else:
            path, lineno = getfslineno(obj)
        modpath = self.getmodpath()
        assert isinstance(lineno, int)
        return path, lineno, modpath


class _EmptyClass:
    pass  # noqa: E701


IGNORED_ATTRIBUTES = frozenset.union(  # noqa: E305
    frozenset(),
    # Module.
    dir(types.ModuleType("empty_module")),
    # Some extra module attributes the above doesn't catch.
    {"__builtins__", "__file__", "__cached__"},
    # Class.
    dir(_EmptyClass),
    # Instance.
    dir(_EmptyClass()),
)


class PyCollector(PyobjMixin, nodes.Collector):
    def funcnamefilter(self, name: str) -> bool:
        return self._matches_prefix_or_glob_option("python_functions", name)

    def isnosetest(self, obj: object) -> bool:
        """Look for the __test__ attribute, which is applied by the
        @nose.tools.istest decorator.
        """
        # We explicitly check for "is True" here to not mistakenly treat
        # classes with a custom __getattr__ returning something truthy (like a
        # function) as test classes.
        return safe_getattr(obj, "__test__", False) is True

    def classnamefilter(self, name: str) -> bool:
        return self._matches_prefix_or_glob_option("python_classes", name)

    def istestfunction(self, obj: object, name: str) -> bool:
        if self.funcnamefilter(name) or self.isnosetest(obj):
            if isinstance(obj, (staticmethod, classmethod)):
                # staticmethods and classmethods need to be unwrapped.
                obj = safe_getattr(obj, "__func__", False)
            return callable(obj) and fixtures.getfixturemarker(obj) is None
        else:
            return False

    def istestclass(self, obj: object, name: str) -> bool:
        return self.classnamefilter(name) or self.isnosetest(obj)

    def _matches_prefix_or_glob_option(self, option_name: str, name: str) -> bool:
        """Check if the given name matches the prefix or glob-pattern defined
        in ini configuration."""
        for option in self.config.getini(option_name):
            if name.startswith(option):
                return True
            # Check that name looks like a glob-string before calling fnmatch
            # because this is called for every name in each collected module,
            # and fnmatch is somewhat expensive to call.
            elif ("*" in option or "?" in option or "[" in option) and fnmatch.fnmatch(
                name, option
            ):
                return True
        return False

    def collect(self) -> Iterable[Union[nodes.Item, nodes.Collector]]:
        if not getattr(self.obj, "__test__", True):
            return []

        # Avoid random getattrs and peek in the __dict__ instead.
        dicts = [getattr(self.obj, "__dict__", {})]
        if isinstance(self.obj, type):
            for basecls in self.obj.__mro__:
                dicts.append(basecls.__dict__)

        # In each class, nodes should be definition ordered.
        # __dict__ is definition ordered.
        seen: Set[str] = set()
        dict_values: List[List[Union[nodes.Item, nodes.Collector]]] = []
        ihook = self.ihook
        for dic in dicts:
            values: List[Union[nodes.Item, nodes.Collector]] = []
            # Note: seems like the dict can change during iteration -
            # be careful not to remove the list() without consideration.
            for name, obj in list(dic.items()):
                if name in IGNORED_ATTRIBUTES:
                    continue
                if name in seen:
                    continue
                seen.add(name)
                res = ihook.pytest_pycollect_makeitem(
                    collector=self, name=name, obj=obj
                )
                if res is None:
                    continue
                elif isinstance(res, list):
                    values.extend(res)
                else:
                    values.append(res)
            dict_values.append(values)

        # Between classes in the class hierarchy, reverse-MRO order -- nodes
        # inherited from base classes should come before subclasses.
        result = []
        for values in reversed(dict_values):
            result.extend(values)
        return result

    def _genfunctions(self, name: str, funcobj) -> Iterator["Function"]:
        modulecol = self.getparent(Module)
        assert modulecol is not None
        module = modulecol.obj
        clscol = self.getparent(Class)
        cls = clscol and clscol.obj or None

        definition = FunctionDefinition.from_parent(self, name=name, callobj=funcobj)
        fixtureinfo = definition._fixtureinfo

        # pytest_generate_tests impls call metafunc.parametrize() which fills
        # metafunc._calls, the outcome of the hook.
        metafunc = Metafunc(
            definition=definition,
            fixtureinfo=fixtureinfo,
            config=self.config,
            cls=cls,
            module=module,
            _ispytest=True,
        )
        methods = []
        if hasattr(module, "pytest_generate_tests"):
            methods.append(module.pytest_generate_tests)
        if cls is not None and hasattr(cls, "pytest_generate_tests"):
            methods.append(cls().pytest_generate_tests)
        self.ihook.pytest_generate_tests.call_extra(methods, dict(metafunc=metafunc))

        if not metafunc._calls:
            yield Function.from_parent(self, name=name, fixtureinfo=fixtureinfo)
        else:
            # Add funcargs() as fixturedefs to fixtureinfo.arg2fixturedefs.
            fm = self.session._fixturemanager
            fixtures.add_funcarg_pseudo_fixture_def(self, metafunc, fm)

            # Add_funcarg_pseudo_fixture_def may have shadowed some fixtures
            # with direct parametrization, so make sure we update what the
            # function really needs.
            fixtureinfo.prune_dependency_tree()

            for callspec in metafunc._calls:
                subname = f"{name}[{callspec.id}]"
                yield Function.from_parent(
                    self,
                    name=subname,
                    callspec=callspec,
                    fixtureinfo=fixtureinfo,
                    keywords={callspec.id: True},
                    originalname=name,
                )


class Module(nodes.File, PyCollector):
    """Collector for test classes and functions."""

    def _getobj(self):
        return self._importtestmodule()

    def collect(self) -> Iterable[Union[nodes.Item, nodes.Collector]]:
        self._inject_setup_module_fixture()
        self._inject_setup_function_fixture()
        self.session._fixturemanager.parsefactories(self)
        return super().collect()

    def _inject_setup_module_fixture(self) -> None:
        """Inject a hidden autouse, module scoped fixture into the collected module object
        that invokes setUpModule/tearDownModule if either or both are available.

        Using a fixture to invoke this methods ensures we play nicely and unsurprisingly with
        other fixtures (#517).
        """
        has_nose = self.config.pluginmanager.has_plugin("nose")
        setup_module = _get_first_non_fixture_func(
            self.obj, ("setUpModule", "setup_module")
        )
        if setup_module is None and has_nose:
            # The name "setup" is too common - only treat as fixture if callable.
            setup_module = _get_first_non_fixture_func(self.obj, ("setup",))
            if not callable(setup_module):
                setup_module = None
        teardown_module = _get_first_non_fixture_func(
            self.obj, ("tearDownModule", "teardown_module")
        )
        if teardown_module is None and has_nose:
            teardown_module = _get_first_non_fixture_func(self.obj, ("teardown",))
            # Same as "setup" above - only treat as fixture if callable.
            if not callable(teardown_module):
                teardown_module = None

        if setup_module is None and teardown_module is None:
            return

        @fixtures.fixture(
            autouse=True,
            scope="module",
            # Use a unique name to speed up lookup.
            name=f"_xunit_setup_module_fixture_{self.obj.__name__}",
        )
        def xunit_setup_module_fixture(request) -> Generator[None, None, None]:
            if setup_module is not None:
                _call_with_optional_argument(setup_module, request.module)
            yield
            if teardown_module is not None:
                _call_with_optional_argument(teardown_module, request.module)

        self.obj.__pytest_setup_module = xunit_setup_module_fixture

    def _inject_setup_function_fixture(self) -> None:
        """Inject a hidden autouse, function scoped fixture into the collected module object
        that invokes setup_function/teardown_function if either or both are available.

        Using a fixture to invoke this methods ensures we play nicely and unsurprisingly with
        other fixtures (#517).
        """
        setup_function = _get_first_non_fixture_func(self.obj, ("setup_function",))
        teardown_function = _get_first_non_fixture_func(
            self.obj, ("teardown_function",)
        )
        if setup_function is None and teardown_function is None:
            return

        @fixtures.fixture(
            autouse=True,
            scope="function",
            # Use a unique name to speed up lookup.
            name=f"_xunit_setup_function_fixture_{self.obj.__name__}",
        )
        def xunit_setup_function_fixture(request) -> Generator[None, None, None]:
            if request.instance is not None:
                # in this case we are bound to an instance, so we need to let
                # setup_method handle this
                yield
                return
            if setup_function is not None:
                _call_with_optional_argument(setup_function, request.function)
            yield
            if teardown_function is not None:
                _call_with_optional_argument(teardown_function, request.function)

        self.obj.__pytest_setup_function = xunit_setup_function_fixture

    def _importtestmodule(self):
        # We assume we are only called once per module.
        importmode = self.config.getoption("--import-mode")
        try:
            mod = import_path(self.path, mode=importmode, root=self.config.rootpath)
        except SyntaxError as e:
            raise self.CollectError(
                ExceptionInfo.from_current().getrepr(style="short")
            ) from e
        except ImportPathMismatchError as e:
            raise self.CollectError(
                "import file mismatch:\n"
                "imported module %r has this __file__ attribute:\n"
                "  %s\n"
                "which is not the same as the test file we want to collect:\n"
                "  %s\n"
                "HINT: remove __pycache__ / .pyc files and/or use a "
                "unique basename for your test file modules" % e.args
            ) from e
        except ImportError as e:
            exc_info = ExceptionInfo.from_current()
            if self.config.getoption("verbose") < 2:
                exc_info.traceback = exc_info.traceback.filter(filter_traceback)
            exc_repr = (
                exc_info.getrepr(style="short")
                if exc_info.traceback
                else exc_info.exconly()
            )
            formatted_tb = str(exc_repr)
            raise self.CollectError(
                "ImportError while importing test module '{path}'.\n"
                "Hint: make sure your test modules/packages have valid Python names.\n"
                "Traceback:\n"
                "{traceback}".format(path=self.path, traceback=formatted_tb)
            ) from e
        except skip.Exception as e:
            if e.allow_module_level:
                raise
            raise self.CollectError(
                "Using pytest.skip outside of a test will skip the entire module. "
                "If that's your intention, pass `allow_module_level=True`. "
                "If you want to skip a specific test or an entire class, "
                "use the @pytest.mark.skip or @pytest.mark.skipif decorators."
            ) from e
        self.config.pluginmanager.consider_module(mod)
        return mod


class Package(Module):
    def __init__(
        self,
        fspath: Optional[LEGACY_PATH],
        parent: nodes.Collector,
        # NOTE: following args are unused:
        config=None,
        session=None,
        nodeid=None,
        path=Optional[Path],
    ) -> None:
        # NOTE: Could be just the following, but kept as-is for compat.
        # nodes.FSCollector.__init__(self, fspath, parent=parent)
        session = parent.session
        nodes.FSCollector.__init__(
            self,
            fspath=fspath,
            path=path,
            parent=parent,
            config=config,
            session=session,
            nodeid=nodeid,
        )
        self.name = self.path.parent.name

    def setup(self) -> None:
        # Not using fixtures to call setup_module here because autouse fixtures
        # from packages are not called automatically (#4085).
        setup_module = _get_first_non_fixture_func(
            self.obj, ("setUpModule", "setup_module")
        )
        if setup_module is not None:
            _call_with_optional_argument(setup_module, self.obj)

        teardown_module = _get_first_non_fixture_func(
            self.obj, ("tearDownModule", "teardown_module")
        )
        if teardown_module is not None:
            func = partial(_call_with_optional_argument, teardown_module, self.obj)
            self.addfinalizer(func)

    def gethookproxy(self, fspath: "os.PathLike[str]"):
        warnings.warn(FSCOLLECTOR_GETHOOKPROXY_ISINITPATH, stacklevel=2)
        return self.session.gethookproxy(fspath)

    def isinitpath(self, path: Union[str, "os.PathLike[str]"]) -> bool:
        warnings.warn(FSCOLLECTOR_GETHOOKPROXY_ISINITPATH, stacklevel=2)
        return self.session.isinitpath(path)

    def _recurse(self, direntry: "os.DirEntry[str]") -> bool:
        if direntry.name == "__pycache__":
            return False
        fspath = Path(direntry.path)
        ihook = self.session.gethookproxy(fspath.parent)
        if ihook.pytest_ignore_collect(collection_path=fspath, config=self.config):
            return False
        norecursepatterns = self.config.getini("norecursedirs")
        if any(fnmatch_ex(pat, fspath) for pat in norecursepatterns):
            return False
        return True

    def _collectfile(
        self, fspath: Path, handle_dupes: bool = True
    ) -> Sequence[nodes.Collector]:
        assert (
            fspath.is_file()
        ), "{!r} is not a file (isdir={!r}, exists={!r}, islink={!r})".format(
            fspath, fspath.is_dir(), fspath.exists(), fspath.is_symlink()
        )
        ihook = self.session.gethookproxy(fspath)
        if not self.session.isinitpath(fspath):
            if ihook.pytest_ignore_collect(collection_path=fspath, config=self.config):
                return ()

        if handle_dupes:
            keepduplicates = self.config.getoption("keepduplicates")
            if not keepduplicates:
                duplicate_paths = self.config.pluginmanager._duplicatepaths
                if fspath in duplicate_paths:
                    return ()
                else:
                    duplicate_paths.add(fspath)

        return ihook.pytest_collect_file(file_path=fspath, parent=self)  # type: ignore[no-any-return]

    def collect(self) -> Iterable[Union[nodes.Item, nodes.Collector]]:
        this_path = self.path.parent
        init_module = this_path / "__init__.py"
        if init_module.is_file() and path_matches_patterns(
            init_module, self.config.getini("python_files")
        ):
            yield Module.from_parent(self, path=init_module)
        pkg_prefixes: Set[Path] = set()
        for direntry in visit(str(this_path), recurse=self._recurse):
            path = Path(direntry.path)

            # We will visit our own __init__.py file, in which case we skip it.
            if direntry.is_file():
                if direntry.name == "__init__.py" and path.parent == this_path:
                    continue

            parts_ = parts(direntry.path)
            if any(
                str(pkg_prefix) in parts_ and pkg_prefix / "__init__.py" != path
                for pkg_prefix in pkg_prefixes
            ):
                continue

            if direntry.is_file():
                yield from self._collectfile(path)
            elif not direntry.is_dir():
                # Broken symlink or invalid/missing file.
                continue
            elif path.joinpath("__init__.py").is_file():
                pkg_prefixes.add(path)


def _call_with_optional_argument(func, arg) -> None:
    """Call the given function with the given argument if func accepts one argument, otherwise
    calls func without arguments."""
    arg_count = func.__code__.co_argcount
    if inspect.ismethod(func):
        arg_count -= 1
    if arg_count:
        func(arg)
    else:
        func()


def _get_first_non_fixture_func(obj: object, names: Iterable[str]) -> Optional[object]:
    """Return the attribute from the given object to be used as a setup/teardown
    xunit-style function, but only if not marked as a fixture to avoid calling it twice.
    """
    for name in names:
        meth: Optional[object] = getattr(obj, name, None)
        if meth is not None and fixtures.getfixturemarker(meth) is None:
            return meth
    return None


class Class(PyCollector):
    """Collector for test methods."""

    @classmethod
    def from_parent(cls, parent, *, name, obj=None, **kw):
        """The public constructor."""
        return super().from_parent(name=name, parent=parent, **kw)

    def newinstance(self):
        return self.obj()

    def collect(self) -> Iterable[Union[nodes.Item, nodes.Collector]]:
        if not safe_getattr(self.obj, "__test__", True):
            return []
        if hasinit(self.obj):
            assert self.parent is not None
            self.warn(
                PytestCollectionWarning(
                    "cannot collect test class %r because it has a "
                    "__init__ constructor (from: %s)"
                    % (self.obj.__name__, self.parent.nodeid)
                )
            )
            return []
        elif hasnew(self.obj):
            assert self.parent is not None
            self.warn(
                PytestCollectionWarning(
                    "cannot collect test class %r because it has a "
                    "__new__ constructor (from: %s)"
                    % (self.obj.__name__, self.parent.nodeid)
                )
            )
            return []

        self._inject_setup_class_fixture()
        self._inject_setup_method_fixture()

        self.session._fixturemanager.parsefactories(self.newinstance(), self.nodeid)

        return super().collect()

    def _inject_setup_class_fixture(self) -> None:
        """Inject a hidden autouse, class scoped fixture into the collected class object
        that invokes setup_class/teardown_class if either or both are available.

        Using a fixture to invoke this methods ensures we play nicely and unsurprisingly with
        other fixtures (#517).
        """
        setup_class = _get_first_non_fixture_func(self.obj, ("setup_class",))
        teardown_class = _get_first_non_fixture_func(self.obj, ("teardown_class",))
        if setup_class is None and teardown_class is None:
            return

        @fixtures.fixture(
            autouse=True,
            scope="class",
            # Use a unique name to speed up lookup.
            name=f"_xunit_setup_class_fixture_{self.obj.__qualname__}",
        )
        def xunit_setup_class_fixture(cls) -> Generator[None, None, None]:
            if setup_class is not None:
                func = getimfunc(setup_class)
                _call_with_optional_argument(func, self.obj)
            yield
            if teardown_class is not None:
                func = getimfunc(teardown_class)
                _call_with_optional_argument(func, self.obj)

        self.obj.__pytest_setup_class = xunit_setup_class_fixture

    def _inject_setup_method_fixture(self) -> None:
        """Inject a hidden autouse, function scoped fixture into the collected class object
        that invokes setup_method/teardown_method if either or both are available.

        Using a fixture to invoke these methods ensures we play nicely and unsurprisingly with
        other fixtures (#517).
        """
        has_nose = self.config.pluginmanager.has_plugin("nose")
        setup_name = "setup_method"
        setup_method = _get_first_non_fixture_func(self.obj, (setup_name,))
        emit_nose_setup_warning = False
        if setup_method is None and has_nose:
            setup_name = "setup"
            emit_nose_setup_warning = True
            setup_method = _get_first_non_fixture_func(self.obj, (setup_name,))
        teardown_name = "teardown_method"
        teardown_method = _get_first_non_fixture_func(self.obj, (teardown_name,))
        emit_nose_teardown_warning = False
        if teardown_method is None and has_nose:
            teardown_name = "teardown"
            emit_nose_teardown_warning = True
            teardown_method = _get_first_non_fixture_func(self.obj, (teardown_name,))
        if setup_method is None and teardown_method is None:
            return

        @fixtures.fixture(
            autouse=True,
            scope="function",
            # Use a unique name to speed up lookup.
            name=f"_xunit_setup_method_fixture_{self.obj.__qualname__}",
        )
        def xunit_setup_method_fixture(self, request) -> Generator[None, None, None]:
            method = request.function
            if setup_method is not None:
                func = getattr(self, setup_name)
                _call_with_optional_argument(func, method)
                if emit_nose_setup_warning:
                    warnings.warn(
                        NOSE_SUPPORT_METHOD.format(
                            nodeid=request.node.nodeid, method="setup"
                        ),
                        stacklevel=2,
                    )
            yield
            if teardown_method is not None:
                func = getattr(self, teardown_name)
                _call_with_optional_argument(func, method)
                if emit_nose_teardown_warning:
                    warnings.warn(
                        NOSE_SUPPORT_METHOD.format(
                            nodeid=request.node.nodeid, method="teardown"
                        ),
                        stacklevel=2,
                    )

        self.obj.__pytest_setup_method = xunit_setup_method_fixture


def hasinit(obj: object) -> bool:
    init: object = getattr(obj, "__init__", None)
    if init:
        return init != object.__init__
    return False


def hasnew(obj: object) -> bool:
    new: object = getattr(obj, "__new__", None)
    if new:
        return new != object.__new__
    return False


class Function(PyobjMixin, nodes.Item):
    """An Item responsible for setting up and executing a Python test function.

    :param name:
        The full function name, including any decorations like those
        added by parametrization (``my_func[my_param]``).
    :param parent:
        The parent Node.
    :param config:
        The pytest Config object.
    :param callspec:
        If given, this is function has been parametrized and the callspec contains
        meta information about the parametrization.
    :param callobj:
        If given, the object which will be called when the Function is invoked,
        otherwise the callobj will be obtained from ``parent`` using ``originalname``.
    :param keywords:
        Keywords bound to the function object for "-k" matching.
    :param session:
        The pytest Session object.
    :param fixtureinfo:
        Fixture information already resolved at this fixture node..
    :param originalname:
        The attribute name to use for accessing the underlying function object.
        Defaults to ``name``. Set this if name is different from the original name,
        for example when it contains decorations like those added by parametrization
        (``my_func[my_param]``).
    """

    # Disable since functions handle it themselves.
    _ALLOW_MARKERS = False

    def __init__(
        self,
        name: str,
        parent,
        config: Optional[Config] = None,
        callspec: Optional[CallSpec2] = None,
        callobj=NOTSET,
        keywords: Optional[Mapping[str, Any]] = None,
        session: Optional[Session] = None,
        fixtureinfo: Optional[FuncFixtureInfo] = None,
        originalname: Optional[str] = None,
    ) -> None:
        super().__init__(name, parent, config=config, session=session)

        if callobj is not NOTSET:
            self.obj = callobj

        #: Original function name, without any decorations (for example
        #: parametrization adds a ``"[...]"`` suffix to function names), used to access
        #: the underlying function object from ``parent`` (in case ``callobj`` is not given
        #: explicitly).
        #:
        #: .. versionadded:: 3.0
        self.originalname = originalname or name

        # Note: when FunctionDefinition is introduced, we should change ``originalname``
        # to a readonly property that returns FunctionDefinition.name.

        self.own_markers.extend(get_unpacked_marks(self.obj))
        if callspec:
            self.callspec = callspec
            self.own_markers.extend(callspec.marks)

        # todo: this is a hell of a hack
        # https://github.com/pytest-dev/pytest/issues/4569
        # Note: the order of the updates is important here; indicates what
        # takes priority (ctor argument over function attributes over markers).
        # Take own_markers only; NodeKeywords handles parent traversal on its own.
        self.keywords.update((mark.name, mark) for mark in self.own_markers)
        self.keywords.update(self.obj.__dict__)
        if keywords:
            self.keywords.update(keywords)

        if fixtureinfo is None:
            fixtureinfo = self.session._fixturemanager.getfixtureinfo(
                self, self.obj, self.cls, funcargs=True
            )
        self._fixtureinfo: FuncFixtureInfo = fixtureinfo
        self.fixturenames = fixtureinfo.names_closure
        self._initrequest()

    @classmethod
    def from_parent(cls, parent, **kw):  # todo: determine sound type limitations
        """The public constructor."""
        return super().from_parent(parent=parent, **kw)

    def _initrequest(self) -> None:
        self.funcargs: Dict[str, object] = {}
        self._request = fixtures.FixtureRequest(self, _ispytest=True)

    @property
    def function(self):
        """Underlying python 'function' object."""
        return getimfunc(self.obj)

    def _getobj(self):
        assert self.parent is not None
        if isinstance(self.parent, Class):
            # Each Function gets a fresh class instance.
            parent_obj = self.parent.newinstance()
        else:
            parent_obj = self.parent.obj  # type: ignore[attr-defined]
        return getattr(parent_obj, self.originalname)

    @property
    def _pyfuncitem(self):
        """(compatonly) for code expecting pytest-2.2 style request objects."""
        return self

    def runtest(self) -> None:
        """Execute the underlying test function."""
        self.ihook.pytest_pyfunc_call(pyfuncitem=self)

    def setup(self) -> None:
        self._request._fillfixtures()

    def _prunetraceback(self, excinfo: ExceptionInfo[BaseException]) -> None:
        if hasattr(self, "_obj") and not self.config.getoption("fulltrace", False):
            code = _pytest._code.Code.from_function(get_real_func(self.obj))
            path, firstlineno = code.path, code.firstlineno
            traceback = excinfo.traceback
            ntraceback = traceback.cut(path=path, firstlineno=firstlineno)
            if ntraceback == traceback:
                ntraceback = ntraceback.cut(path=path)
                if ntraceback == traceback:
                    ntraceback = ntraceback.filter(filter_traceback)
                    if not ntraceback:
                        ntraceback = traceback

            excinfo.traceback = ntraceback.filter()
            # issue364: mark all but first and last frames to
            # only show a single-line message for each frame.
            if self.config.getoption("tbstyle", "auto") == "auto":
                if len(excinfo.traceback) > 2:
                    for entry in excinfo.traceback[1:-1]:
                        entry.set_repr_style("short")

    # TODO: Type ignored -- breaks Liskov Substitution.
    def repr_failure(  # type: ignore[override]
        self,
        excinfo: ExceptionInfo[BaseException],
    ) -> Union[str, TerminalRepr]:
        style = self.config.getoption("tbstyle", "auto")
        if style == "auto":
            style = "long"
        return self._repr_failure_py(excinfo, style=style)


class FunctionDefinition(Function):
    """
    This class is a step gap solution until we evolve to have actual function definition nodes
    and manage to get rid of ``metafunc``.
    """

    def runtest(self) -> None:
        raise RuntimeError("function definitions are not supposed to be run as tests")

    setup = runtest

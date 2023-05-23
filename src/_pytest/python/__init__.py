"""Python test discovery, setup and run of test functions."""
import inspect
import warnings
from pathlib import Path
from typing import List
from typing import Optional
from typing import TYPE_CHECKING
from typing import Union

from . import nodes
from .. import nodes as base_nodes
from _pytest._code import getfslineno
from _pytest.compat import get_real_func
from _pytest.compat import is_async_function
from _pytest.compat import is_generator
from _pytest.compat import safe_isclass
from _pytest.config import Config
from _pytest.config import ExitCode
from _pytest.config import hookimpl
from _pytest.config.argparsing import Parser
from _pytest.deprecated import INSTANCE_COLLECTOR
from _pytest.mark import MARK_GEN
from _pytest.outcomes import skip
from _pytest.python.metafunc import Metafunc
from _pytest.python.nodes import path_matches_patterns
from _pytest.warning_types import PytestCollectionWarning
from _pytest.warning_types import PytestReturnNotNoneWarning
from _pytest.warning_types import PytestUnhandledCoroutineWarning

if TYPE_CHECKING:
    pass


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("general")
    group.addoption(
        "--fixtures",
        "--funcargs",
        action="store_true",
        dest="showfixtures",
        default=False,
        help="Show available fixtures, sorted by plugin appearance "
        "(fixtures with leading '_' are only shown with '-v')",
    )
    group.addoption(
        "--fixtures-per-test",
        action="store_true",
        dest="show_fixtures_per_test",
        default=False,
        help="Show fixtures per test",
    )
    parser.addini(
        "python_files",
        type="args",
        # NOTE: default is also used in AssertionRewritingHook.
        default=["test_*.py", "*_test.py"],
        help="Glob-style file patterns for Python test module discovery",
    )
    parser.addini(
        "python_classes",
        type="args",
        default=["Test"],
        help="Prefixes or glob names for Python test class discovery",
    )
    parser.addini(
        "python_functions",
        type="args",
        default=["test"],
        help="Prefixes or glob names for Python test function and method discovery",
    )
    parser.addini(
        "disable_test_id_escaping_and_forfeit_all_rights_to_community_support",
        type="bool",
        default=False,
        help="Disable string escape non-ASCII characters, might cause unwanted "
        "side effects(use at your own risk)",
    )


def pytest_cmdline_main(config: Config) -> Optional[Union[int, ExitCode]]:
    if config.option.showfixtures:
        from .show_fixtures import showfixtures

        return showfixtures(config)
    if config.option.show_fixtures_per_test:
        from .show_fixtures import show_fixtures_per_test

        return show_fixtures_per_test(config)
    return None


def pytest_generate_tests(metafunc: "Metafunc") -> None:
    for marker in metafunc.definition.iter_markers(name="parametrize"):
        metafunc.parametrize(*marker.args, **marker.kwargs, _param_mark=marker)


def pytest_configure(config: Config) -> None:
    config.addinivalue_line(
        "markers",
        "parametrize(argnames, argvalues): call a test function multiple "
        "times passing in different arguments in turn. argvalues generally "
        "needs to be a list of values if argnames specifies only one name "
        "or a list of tuples of values if argnames specifies multiple names. "
        "Example: @parametrize('arg1', [1,2]) would lead to two calls of the "
        "decorated test function, one with arg1=1 and another with arg1=2."
        "see https://docs.pytest.org/en/stable/how-to/parametrize.html for more info "
        "and examples.",
    )
    config.addinivalue_line(
        "markers",
        "usefixtures(fixturename1, fixturename2, ...): mark tests as needing "
        "all of the specified fixtures. see "
        "https://docs.pytest.org/en/stable/explanation/fixtures.html#usefixtures ",
    )


def async_warn_and_skip(nodeid: str) -> None:
    msg = "async def functions are not natively supported and have been skipped.\n"
    msg += (
        "You need to install a suitable plugin for your async framework, for example:\n"
    )
    msg += "  - anyio\n"
    msg += "  - pytest-asyncio\n"
    msg += "  - pytest-tornasync\n"
    msg += "  - pytest-trio\n"
    msg += "  - pytest-twisted"
    warnings.warn(PytestUnhandledCoroutineWarning(msg.format(nodeid)))
    skip(reason="async def function and no async plugin installed (see warnings)")


@hookimpl(trylast=True)
def pytest_pyfunc_call(pyfuncitem: "nodes.Function") -> Optional[object]:
    testfunction = pyfuncitem.obj
    if is_async_function(testfunction):
        async_warn_and_skip(pyfuncitem.nodeid)
    funcargs = pyfuncitem.funcargs
    testargs = {arg: funcargs[arg] for arg in pyfuncitem._fixtureinfo.argnames}
    result = testfunction(**testargs)
    if hasattr(result, "__await__") or hasattr(result, "__aiter__"):
        async_warn_and_skip(pyfuncitem.nodeid)
    elif result is not None:
        warnings.warn(
            PytestReturnNotNoneWarning(
                f"Expected None, but {pyfuncitem.nodeid} returned {result!r}, which will be an error in a "
                "future version of pytest.  Did you mean to use `assert` instead of `return`?"
            )
        )
    return True


def pytest_collect_file(
    file_path: Path, parent: base_nodes.Collector
) -> Optional["nodes.Module"]:
    if file_path.suffix == ".py":
        if not parent.session.isinitpath(file_path):
            if not path_matches_patterns(
                file_path, parent.config.getini("python_files") + ["__init__.py"]
            ):
                return None
        ihook = parent.session.gethookproxy(file_path)
        module: nodes.Module = ihook.pytest_pycollect_makemodule(
            module_path=file_path, parent=parent
        )
        return module
    return None


def pytest_pycollect_makemodule(module_path: Path, parent) -> "nodes.Module":
    if module_path.name == "__init__.py":
        pkg: nodes.Package = nodes.Package.from_parent(parent, path=module_path)
        return pkg
    mod: nodes.Module = nodes.Module.from_parent(parent, path=module_path)
    return mod


@hookimpl(trylast=True)
def pytest_pycollect_makeitem(
    collector: Union["nodes.Module", "nodes.Class"], name: str, obj: object
) -> Union[
    None,
    base_nodes.Item,
    base_nodes.Collector,
    List[Union[base_nodes.Item, base_nodes.Collector]],
]:
    assert isinstance(collector, (nodes.Class, nodes.Module)), type(collector)
    # Nothing was collected elsewhere, let's do it here.
    if safe_isclass(obj):
        if collector.istestclass(obj, name):
            klass: nodes.Class = nodes.Class.from_parent(collector, name=name, obj=obj)
            return klass
    elif collector.istestfunction(obj, name):
        # mock seems to store unbound methods (issue473), normalize it.
        obj = getattr(obj, "__func__", obj)
        # We need to try and unwrap the function if it's a functools.partial
        # or a functools.wrapped.
        # We mustn't if it's been wrapped with mock.patch (python 2 only).
        if not (inspect.isfunction(obj) or inspect.isfunction(get_real_func(obj))):
            filename, lineno = getfslineno(obj)
            warnings.warn_explicit(
                message=PytestCollectionWarning(
                    "cannot collect %r because it is not a function." % name
                ),
                category=None,
                filename=str(filename),
                lineno=lineno + 1,
            )
        elif getattr(obj, "__test__", True):
            if is_generator(obj):
                res: nodes.Function = nodes.Function.from_parent(collector, name=name)
                reason = "yield tests were removed in pytest 4.0 - {name} will be ignored".format(
                    name=name
                )
                res.add_marker(MARK_GEN.xfail(run=False, reason=reason))
                res.warn(PytestCollectionWarning(reason))
                return res
            else:
                return list(collector._genfunctions(name, obj))
    return None


class InstanceDummy:
    """Instance used to be a node type between Class and Function. It has been
    removed in pytest 7.0. Some plugins exist which reference `pytest.Instance`
    only to ignore it; this dummy class keeps them working. This will be removed
    in pytest 8."""


def __getattr__(name: str) -> object:
    if name == "Instance":
        warnings.warn(INSTANCE_COLLECTOR, 2)
        return InstanceDummy
    raise AttributeError(f"module {__name__} has no attribute {name}")


class FunctionDefinition(nodes.Function):
    """
    This class is a step gap solution until we evolve to have actual function definition nodes
    and manage to get rid of ``metafunc``.
    """

    def runtest(self) -> None:
        raise RuntimeError("function definitions are not supposed to be run as tests")

    setup = runtest

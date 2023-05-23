import inspect
from pathlib import Path
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Union

import _pytest
from _pytest import fixtures
from _pytest import nodes
from _pytest._io import TerminalWriter
from _pytest.compat import getlocation
from _pytest.config import Config
from _pytest.config import ExitCode
from _pytest.fixtures import FuncFixtureInfo
from _pytest.main import Session
from _pytest.pathlib import bestrelpath


_PYTEST_DIR = Path(_pytest.__file__).parent


def _showfixtures_main(config: Config, session: Session) -> None:
    import _pytest.config

    session.perform_collect()
    curdir = Path.cwd()
    tw = _pytest.config.create_terminal_writer(config)
    verbose = config.getvalue("verbose")

    fm = session._fixturemanager

    available = []
    seen: Set[Tuple[str, str]] = set()

    for argname, fixturedefs in fm._arg2fixturedefs.items():
        assert fixturedefs is not None
        if not fixturedefs:
            continue
        for fixturedef in fixturedefs:
            loc = getlocation(fixturedef.func, str(curdir))
            if (fixturedef.argname, loc) in seen:
                continue
            seen.add((fixturedef.argname, loc))
            available.append(
                (
                    len(fixturedef.baseid),
                    fixturedef.func.__module__,
                    _pretty_fixture_path(fixturedef.func),
                    fixturedef.argname,
                    fixturedef,
                )
            )

    available.sort()
    currentmodule = None
    for baseid, module, prettypath, argname, fixturedef in available:
        if currentmodule != module:
            if not module.startswith("_pytest."):
                tw.line()
                tw.sep("-", f"fixtures defined from {module}")
                currentmodule = module
        if verbose <= 0 and argname.startswith("_"):
            continue
        tw.write(f"{argname}", green=True)
        if fixturedef.scope != "function":
            tw.write(" [%s scope]" % fixturedef.scope, cyan=True)
        tw.write(f" -- {prettypath}", yellow=True)
        tw.write("\n")
        doc = inspect.getdoc(fixturedef.func)
        if doc:
            write_docstring(tw, doc.split("\n\n")[0] if verbose <= 0 else doc)
        else:
            tw.line("    no docstring available", red=True)
        tw.line()


def _pretty_fixture_path(func) -> str:
    cwd = Path.cwd()
    loc = Path(getlocation(func, str(cwd)))
    prefix = Path("...", "_pytest")
    try:
        return str(prefix / loc.relative_to(_PYTEST_DIR))
    except ValueError:
        return bestrelpath(cwd, loc)


def show_fixtures_per_test(config: Config) -> Union[int, ExitCode]:
    from _pytest.main import wrap_session

    return wrap_session(config, _show_fixtures_per_test)


def _show_fixtures_per_test(config: Config, session: Session) -> None:
    import _pytest.config

    session.perform_collect()
    curdir = Path.cwd()
    tw = _pytest.config.create_terminal_writer(config)
    verbose = config.getvalue("verbose")

    def get_best_relpath(func) -> str:
        loc = getlocation(func, str(curdir))
        return bestrelpath(curdir, Path(loc))

    def write_fixture(fixture_def: fixtures.FixtureDef[object]) -> None:
        argname = fixture_def.argname
        if verbose <= 0 and argname.startswith("_"):
            return
        prettypath = _pretty_fixture_path(fixture_def.func)
        tw.write(f"{argname}", green=True)
        tw.write(f" -- {prettypath}", yellow=True)
        tw.write("\n")
        fixture_doc = inspect.getdoc(fixture_def.func)
        if fixture_doc:
            write_docstring(
                tw, fixture_doc.split("\n\n")[0] if verbose <= 0 else fixture_doc
            )
        else:
            tw.line("    no docstring available", red=True)

    def write_item(item: nodes.Item) -> None:
        # Not all items have _fixtureinfo attribute.
        info: Optional[FuncFixtureInfo] = getattr(item, "_fixtureinfo", None)
        if info is None or not info.name2fixturedefs:
            # This test item does not use any fixtures.
            return
        tw.line()
        tw.sep("-", f"fixtures used by {item.name}")
        # TODO: Fix this type ignore.
        tw.sep("-", f"({get_best_relpath(item.function)})")  # type: ignore[attr-defined]
        # dict key not used in loop but needed for sorting.
        for _, fixturedefs in sorted(info.name2fixturedefs.items()):
            assert fixturedefs is not None
            if not fixturedefs:
                continue
            # Last item is expected to be the one used by the test item.
            write_fixture(fixturedefs[-1])

    for session_item in session.items:
        write_item(session_item)


def showfixtures(config: Config) -> Union[int, ExitCode]:
    from _pytest.main import wrap_session

    return wrap_session(config, _showfixtures_main)


def write_docstring(tw: TerminalWriter, doc: str, indent: str = "    ") -> None:
    for line in doc.split("\n"):
        tw.line(indent + line)

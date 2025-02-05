"""
test example file exposing mltiple issues with corutine exception passover in case of stopiteration

the stdlib contextmanager implementation explicitly catches
and reshapes in case a StopIteration was send in and is raised out
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pluggy


def test_stop() -> None:
    raise StopIteration()


hookspec = pluggy.HookspecMarker("myproject")
hookimpl = pluggy.HookimplMarker("myproject")


class MySpec:
    """A hook specification namespace."""

    @hookspec
    def myhook(self, arg1: int, arg2: int) -> int:  # type: ignore[empty-body]
        """My special little hook that you can customize."""


class Plugin_1:
    """A hook implementation namespace."""

    @hookimpl
    def myhook(self, arg1: int, arg2: int) -> int:
        print("inside Plugin_1.myhook()")
        raise StopIteration()


class Plugin_2:
    """A 2nd hook implementation namespace."""

    @hookimpl(wrapper=True)
    def myhook(self) -> Iterator[None]:
        return (yield)


def try_pluggy() -> None:
    # create a manager and add the spec
    pm = pluggy.PluginManager("myproject")
    pm.add_hookspecs(MySpec)

    # register plugins
    pm.register(Plugin_1())
    pm.register(Plugin_2())

    # call our ``myhook`` hook
    results = pm.hook.myhook(arg1=1, arg2=2)
    print(results)


@contextmanager
def my_cm() -> Iterator[None]:
    try:
        yield
    except Exception as e:
        print(e)
    raise StopIteration()


def inner() -> None:
    with my_cm():
        raise StopIteration()


def try_context() -> None:
    inner()


mains = {"pluggy": try_pluggy, "context": try_context}

if __name__ == "__main__":
    import sys

    if len(sys.argv) == 2:
        mains[sys.argv[1]]()

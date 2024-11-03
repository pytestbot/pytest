from __future__ import annotations

import sys
import traceback
from types import TracebackType
from typing import Any
from typing import Callable
from typing import Iterator
from typing import TYPE_CHECKING
import warnings

import pytest


if TYPE_CHECKING:
    from typing_extensions import Self


# Copied from cpython/Lib/test/support/__init__.py, with modifications.
class catch_unraisable_exception:
    """Context manager catching unraisable exception using sys.unraisablehook.

    Storing the exception value (cm.unraisable.exc_value) creates a reference
    cycle. The reference cycle is broken explicitly when the context manager
    exits.

    Storing the object (cm.unraisable.object) can resurrect it if it is set to
    an object which is being finalized. Exiting the context manager clears the
    stored object.

    Usage:
        with catch_unraisable_exception() as cm:
            # code creating an "unraisable exception"
            ...
            # check the unraisable exception: use cm.unraisable
            ...
        # cm.unraisable attribute no longer exists at this point
        # (to break a reference cycle)
    """

    unraisable: sys.UnraisableHookArgs | None = None
    _old_hook: Callable[[sys.UnraisableHookArgs], Any] | None = None

    def _hook(self, unraisable: sys.UnraisableHookArgs) -> None:
        # Storing unraisable.object can resurrect an object which is being
        # finalized. Storing unraisable.exc_value creates a reference cycle.
        self.unraisable = unraisable

    def _warn_if_triggered(self) -> None:
        if self.unraisable:
            if self.unraisable.err_msg is not None:
                err_msg = self.unraisable.err_msg
            else:
                err_msg = "Exception ignored in"
            msg = f"{err_msg}: {self.unraisable.object!r}\n\n"
            msg += "".join(
                traceback.format_exception(
                    self.unraisable.exc_type,
                    self.unraisable.exc_value,
                    self.unraisable.exc_traceback,
                )
            )
            warnings.warn(pytest.PytestUnraisableExceptionWarning(msg))

    def __enter__(self) -> Self:
        self._old_hook = sys.unraisablehook
        sys.unraisablehook = self._hook
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._old_hook is not None
        sys.unraisablehook = self._old_hook
        self._old_hook = None
        self._warn_if_triggered()
        if "unraisable" in vars(self):
            del self.unraisable


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_setup() -> Iterator[None]:
    with catch_unraisable_exception():
        yield


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_call() -> Iterator[None]:
    with catch_unraisable_exception():
        yield


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown() -> Iterator[None]:
    with catch_unraisable_exception():
        yield

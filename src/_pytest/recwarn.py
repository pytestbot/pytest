"""Record warnings during test function execution."""
import re
import warnings
from pprint import pformat
from types import TracebackType
from typing import Any
from typing import Callable
from typing import final
from typing import Generator
from typing import Iterator
from typing import List
from typing import Optional
from typing import overload
from typing import Pattern
from typing import Tuple
from typing import Type
from typing import TypeVar
from typing import Union

from _pytest.deprecated import check_ispytest
from _pytest.deprecated import WARNS_NONE_ARG
from _pytest.fixtures import fixture
from _pytest.outcomes import fail


T = TypeVar("T")


@fixture
def recwarn() -> Generator["WarningsRecorder", None, None]:
    """Return a :class:`WarningsRecorder` instance that records all warnings emitted by test functions.

    See https://docs.pytest.org/en/latest/how-to/capture-warnings.html for information
    on warning categories.
    """
    wrec = WarningsRecorder(_ispytest=True)
    with wrec:
        warnings.simplefilter("default")
        yield wrec


@overload
def deprecated_call(
    *, match: Optional[Union[str, Pattern[str]]] = ...
) -> "WarningsRecorder":
    ...


@overload
def deprecated_call(  # noqa: F811
    func: Callable[..., T], *args: Any, **kwargs: Any
) -> T:
    ...


def deprecated_call(  # noqa: F811
    func: Optional[Callable[..., Any]] = None, *args: Any, **kwargs: Any
) -> Union["WarningsRecorder", Any]:
    """Assert that code produces a ``DeprecationWarning`` or ``PendingDeprecationWarning`` or ``FutureWarning``.

    This function can be used as a context manager::

        >>> import warnings
        >>> def api_call_v2():
        ...     warnings.warn('use v3 of this api', DeprecationWarning)
        ...     return 200

        >>> import pytest
        >>> with pytest.deprecated_call():
        ...    assert api_call_v2() == 200

    It can also be used by passing a function and ``*args`` and ``**kwargs``,
    in which case it will ensure calling ``func(*args, **kwargs)`` produces one of
    the warnings types above. The return value is the return value of the function.

    In the context manager form you may use the keyword argument ``match`` to assert
    that the warning matches a text or regex.

    The context manager produces a list of :class:`warnings.WarningMessage` objects,
    one for each warning raised.
    """
    __tracebackhide__ = True
    if func is not None:
        args = (func,) + args
    return warns(
        (DeprecationWarning, PendingDeprecationWarning, FutureWarning), *args, **kwargs
    )


@overload
def warns(
    expected_warning: Union[Type[Warning], Tuple[Type[Warning], ...]] = ...,
    *,
    match: Optional[Union[str, Pattern[str]]] = ...,
) -> "WarningsChecker":
    ...


@overload
def warns(  # noqa: F811
    expected_warning: Union[Type[Warning], Tuple[Type[Warning], ...]],
    func: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    ...


def warns(  # noqa: F811
    expected_warning: Union[Type[Warning], Tuple[Type[Warning], ...]] = Warning,
    *args: Any,
    match: Optional[Union[str, Pattern[str]]] = None,
    **kwargs: Any,
) -> Union["WarningsChecker", Any]:
    r"""Assert that code raises a particular class of warning.

    Specifically, the parameter ``expected_warning`` can be a warning class or sequence
    of warning classes, and the code inside the ``with`` block must issue at least one
    warning of that class or classes.

    This helper produces a list of :class:`warnings.WarningMessage` objects, one for
    each warning emitted (regardless of whether it is an ``expected_warning`` or not).
    Since pytest 8.0, unmatched warnings are also re-emitted when the context closes.

    This function can be used as a context manager::

        >>> import pytest
        >>> with pytest.warns(RuntimeWarning):
        ...    warnings.warn("my warning", RuntimeWarning)

    In the context manager form you may use the keyword argument ``match`` to assert
    that the warning matches a text or regex::

        >>> with pytest.warns(UserWarning, match='must be 0 or None'):
        ...     warnings.warn("value must be 0 or None", UserWarning)

        >>> with pytest.warns(UserWarning, match=r'must be \d+$'):
        ...     warnings.warn("value must be 42", UserWarning)

        >>> with pytest.warns(UserWarning):  # catch re-emitted warning
        ...     with pytest.warns(UserWarning, match=r'must be \d+$'):
        ...         warnings.warn("this is not here", UserWarning)
        Traceback (most recent call last):
          ...
        Failed: DID NOT WARN. No warnings of type ...UserWarning... were emitted...

    **Using with** ``pytest.mark.parametrize``

    When using :ref:`pytest.mark.parametrize ref` it is possible to parametrize tests
    such that some runs raise a warning and others do not.

    This could be achieved in the same way as with exceptions, see
    :ref:`parametrizing_conditional_raising` for an example.

    """
    __tracebackhide__ = True
    if not args:
        if kwargs:
            argnames = ", ".join(sorted(kwargs))
            raise TypeError(
                f"Unexpected keyword arguments passed to pytest.warns: {argnames}"
                "\nUse context-manager form instead?"
            )
        return WarningsChecker(expected_warning, match_expr=match, _ispytest=True)
    else:
        func = args[0]
        if not callable(func):
            raise TypeError(f"{func!r} object (type: {type(func)}) must be callable")
        with WarningsChecker(expected_warning, _ispytest=True):
            return func(*args[1:], **kwargs)


class WarningsRecorder(warnings.catch_warnings):  # type:ignore[type-arg]
    """A context manager to record raised warnings.

    Each recorded warning is an instance of :class:`warnings.WarningMessage`.

    Adapted from `warnings.catch_warnings`.

    .. note::
        ``DeprecationWarning`` and ``PendingDeprecationWarning`` are treated
        differently; see :ref:`ensuring_function_triggers`.

    """

    def __init__(self, *, _ispytest: bool = False) -> None:
        check_ispytest(_ispytest)
        # Type ignored due to the way typeshed handles warnings.catch_warnings.
        super().__init__(record=True)  # type: ignore[call-arg]
        self._entered = False
        self._list: List[warnings.WarningMessage] = []

    @property
    def list(self) -> List["warnings.WarningMessage"]:
        """The list of recorded warnings."""
        return self._list

    def __getitem__(self, i: int) -> "warnings.WarningMessage":
        """Get a recorded warning by index."""
        return self._list[i]

    def __iter__(self) -> Iterator["warnings.WarningMessage"]:
        """Iterate through the recorded warnings."""
        return iter(self._list)

    def __len__(self) -> int:
        """The number of recorded warnings."""
        return len(self._list)

    def pop(self, cls: Type[Warning] = Warning) -> "warnings.WarningMessage":
        """Pop the first recorded warning which is an instance of ``cls``,
        but not an instance of a child class of any other match.
        Raises ``AssertionError`` if there is no match.
        """
        best_idx: Optional[int] = None
        for i, w in enumerate(self._list):
            if w.category == cls:
                return self._list.pop(i)  # exact match, stop looking
            if issubclass(w.category, cls) and (
                best_idx is None
                or not issubclass(w.category, self._list[best_idx].category)
            ):
                best_idx = i
        if best_idx is not None:
            return self._list.pop(best_idx)
        __tracebackhide__ = True
        raise AssertionError(f"{cls!r} not found in warning list")

    def clear(self) -> None:
        """Clear the list of recorded warnings."""
        self._list[:] = []

    # Type ignored because it doesn't exactly warnings.catch_warnings.__enter__
    # -- it returns a List but we only emulate one.
    def __enter__(self) -> "WarningsRecorder":  # type: ignore
        if self._entered:
            __tracebackhide__ = True
            raise RuntimeError(f"Cannot enter {self!r} twice")
        _list = super().__enter__()
        # record=True means it's None.
        assert _list is not None
        self._list = _list
        warnings.simplefilter("always")
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        if not self._entered:
            __tracebackhide__ = True
            raise RuntimeError(f"Cannot exit {self!r} without entering first")

        super().__exit__(exc_type, exc_val, exc_tb)

        # Built-in catch_warnings does not reset entered state so we do it
        # manually here for this context manager to become reusable.
        self._entered = False


@final
class WarningsChecker(WarningsRecorder):
    def __init__(
        self,
        expected_warning: Optional[
            Union[Type[Warning], Tuple[Type[Warning], ...]]
        ] = Warning,
        match_expr: Optional[Union[str, Pattern[str]]] = None,
        *,
        _ispytest: bool = False,
    ) -> None:
        check_ispytest(_ispytest)
        super().__init__(_ispytest=True)

        msg = "exceptions must be derived from Warning, not %s"
        if expected_warning is None:
            warnings.warn(WARNS_NONE_ARG, stacklevel=4)
            expected_warning_tup = None
        elif isinstance(expected_warning, tuple):
            for exc in expected_warning:
                if not issubclass(exc, Warning):
                    raise TypeError(msg % type(exc))
            expected_warning_tup = expected_warning
        elif issubclass(expected_warning, Warning):
            expected_warning_tup = (expected_warning,)
        else:
            raise TypeError(msg % type(expected_warning))

        self.expected_warning = expected_warning_tup
        self.match_expr = match_expr

    def matches(self, warning: warnings.WarningMessage) -> bool:
        assert self.expected_warning is not None
        return issubclass(warning.category, self.expected_warning) and bool(
            self.match_expr is None or re.search(self.match_expr, str(warning.message))
        )

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        super().__exit__(exc_type, exc_val, exc_tb)

        __tracebackhide__ = True

        if self.expected_warning is None:
            # nothing to do in this deprecated case, see WARNS_NONE_ARG above
            return

        def found_str():
            return pformat([record.message for record in self], indent=2)

        try:
            if not any(issubclass(w.category, self.expected_warning) for w in self):
                fail(
                    f"DID NOT WARN. No warnings of type {self.expected_warning} were emitted.\n"
                    f" Emitted warnings: {found_str()}."
                )
            elif not any(self.matches(w) for w in self):
                fail(
                    f"DID NOT WARN. No warnings of type {self.expected_warning} matching the regex were emitted.\n"
                    f" Regex: {self.match_expr}\n"
                    f" Emitted warnings: {found_str()}."
                )
        finally:
            # Whether or not any warnings matched, we want to re-emit all unmatched warnings.
            for w in self:
                if not self.matches(w):
                    warnings.warn_explicit(
                        str(w.message),
                        w.message.__class__,  # type: ignore[arg-type]
                        w.filename,
                        w.lineno,
                        module=w.__module__,
                        source=w.source,
                    )

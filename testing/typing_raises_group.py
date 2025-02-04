from __future__ import annotations

import sys
from typing import Callable
from typing import Union

from typing_extensions import assert_type

from _pytest._raises_group import Matcher
from _pytest._raises_group import RaisesGroup


if sys.version_info < (3, 11):
    from exceptiongroup import BaseExceptionGroup
    from exceptiongroup import ExceptionGroup

# split into functions to isolate the different scopes


def check_matcher_typevar_default(e: Matcher) -> None:
    assert e.exception_type is not None
    _exc: type[BaseException] = e.exception_type
    # this would previously pass, as the type would be `Any`
    e.exception_type().blah()  # type: ignore


def check_basic_contextmanager() -> None:
    with RaisesGroup(ValueError) as e:
        raise ExceptionGroup("foo", (ValueError(),))
    assert_type(e.value, ExceptionGroup[ValueError])


def check_basic_matches() -> None:
    # check that matches gets rid of the naked ValueError in the union
    exc: ExceptionGroup[ValueError] | ValueError = ExceptionGroup("", (ValueError(),))
    if RaisesGroup(ValueError).matches(exc):
        assert_type(exc, ExceptionGroup[ValueError])

    # also check that BaseExceptionGroup shows up for BaseExceptions
    if RaisesGroup(KeyboardInterrupt).matches(exc):
        assert_type(exc, BaseExceptionGroup[KeyboardInterrupt])


def check_matches_with_different_exception_type() -> None:
    e: BaseExceptionGroup[KeyboardInterrupt] = BaseExceptionGroup(
        "",
        (KeyboardInterrupt(),),
    )

    # note: it might be tempting to have this warn.
    # however, that isn't possible with current typing
    if RaisesGroup(ValueError).matches(e):
        assert_type(e, ExceptionGroup[ValueError])


def check_matcher_init() -> None:
    def check_exc(exc: BaseException) -> bool:
        return isinstance(exc, ValueError)

    # Check various combinations of constructor signatures.
    # At least 1 arg must be provided.
    Matcher()  # type: ignore
    Matcher(ValueError)
    Matcher(ValueError, "regex")
    Matcher(ValueError, "regex", check_exc)
    Matcher(exception_type=ValueError)
    Matcher(match="regex")
    Matcher(check=check_exc)
    Matcher(ValueError, match="regex")
    Matcher(match="regex", check=check_exc)

    def check_filenotfound(exc: FileNotFoundError) -> bool:
        return not exc.filename.endswith(".tmp")

    # If exception_type is provided, that narrows the `check` method's argument.
    Matcher(FileNotFoundError, check=check_filenotfound)
    Matcher(ValueError, check=check_filenotfound)  # type: ignore
    Matcher(check=check_filenotfound)  # type: ignore
    Matcher(FileNotFoundError, match="regex", check=check_filenotfound)


def raisesgroup_check_type_narrowing() -> None:
    """Check type narrowing on the `check` argument to `RaisesGroup`.
    All `type: ignore`s are correctly pointing out type errors.
    """

    def handle_exc(e: BaseExceptionGroup[BaseException]) -> bool:
        return True

    def handle_kbi(e: BaseExceptionGroup[KeyboardInterrupt]) -> bool:
        return True

    def handle_value(e: BaseExceptionGroup[ValueError]) -> bool:
        return True

    RaisesGroup(BaseException, check=handle_exc)
    RaisesGroup(BaseException, check=handle_kbi)  # type: ignore

    RaisesGroup(Exception, check=handle_exc)
    RaisesGroup(Exception, check=handle_value)  # type: ignore

    RaisesGroup(KeyboardInterrupt, check=handle_exc)
    RaisesGroup(KeyboardInterrupt, check=handle_kbi)
    RaisesGroup(KeyboardInterrupt, check=handle_value)  # type: ignore

    RaisesGroup(ValueError, check=handle_exc)
    RaisesGroup(ValueError, check=handle_kbi)  # type: ignore
    RaisesGroup(ValueError, check=handle_value)

    RaisesGroup(ValueError, KeyboardInterrupt, check=handle_exc)
    RaisesGroup(ValueError, KeyboardInterrupt, check=handle_kbi)  # type: ignore
    RaisesGroup(ValueError, KeyboardInterrupt, check=handle_value)  # type: ignore


def raisesgroup_narrow_baseexceptiongroup() -> None:
    """Check type narrowing specifically for the container exceptiongroup."""

    def handle_group(e: ExceptionGroup[Exception]) -> bool:
        return True

    def handle_group_value(e: ExceptionGroup[ValueError]) -> bool:
        return True

    RaisesGroup(ValueError, check=handle_group_value)

    RaisesGroup(Exception, check=handle_group)


def check_matcher_transparent() -> None:
    with RaisesGroup(Matcher(ValueError)) as e:
        ...
    _: BaseExceptionGroup[ValueError] = e.value
    assert_type(e.value, ExceptionGroup[ValueError])


def check_nested_raisesgroups_contextmanager() -> None:
    with RaisesGroup(RaisesGroup(ValueError)) as excinfo:
        raise ExceptionGroup("foo", (ValueError(),))

    _: BaseExceptionGroup[BaseExceptionGroup[ValueError]] = excinfo.value

    assert_type(
        excinfo.value,
        ExceptionGroup[ExceptionGroup[ValueError]],
    )

    assert_type(
        excinfo.value.exceptions[0],
        # this union is because of how typeshed defines .exceptions
        Union[
            ExceptionGroup[ValueError],
            ExceptionGroup[ExceptionGroup[ValueError]],
        ],
    )


def check_nested_raisesgroups_matches() -> None:
    """Check nested RaisesGroups with .matches"""
    exc: ExceptionGroup[ExceptionGroup[ValueError]] = ExceptionGroup(
        "",
        (ExceptionGroup("", (ValueError(),)),),
    )

    if RaisesGroup(RaisesGroup(ValueError)).matches(exc):
        assert_type(exc, ExceptionGroup[ExceptionGroup[ValueError]])


def check_multiple_exceptions_1() -> None:
    a = RaisesGroup(ValueError, ValueError)
    b = RaisesGroup(Matcher(ValueError), Matcher(ValueError))
    c = RaisesGroup(ValueError, Matcher(ValueError))

    d: RaisesGroup[ValueError]
    d = a
    d = b
    d = c
    assert d


def check_multiple_exceptions_2() -> None:
    # This previously failed due to lack of covariance in the TypeVar
    a = RaisesGroup(Matcher(ValueError), Matcher(TypeError))
    b = RaisesGroup(Matcher(ValueError), TypeError)
    c = RaisesGroup(ValueError, TypeError)

    d: RaisesGroup[Exception]
    d = a
    d = b
    d = c
    assert d


def check_raisesgroup_overloads() -> None:
    # allow_unwrapped=True does not allow:
    # multiple exceptions
    RaisesGroup(ValueError, TypeError, allow_unwrapped=True)  # type: ignore
    # nested RaisesGroup
    RaisesGroup(RaisesGroup(ValueError), allow_unwrapped=True)  # type: ignore
    # specifying match
    RaisesGroup(ValueError, match="foo", allow_unwrapped=True)  # type: ignore
    # specifying check
    RaisesGroup(ValueError, check=bool, allow_unwrapped=True)  # type: ignore
    # allowed variants
    RaisesGroup(ValueError, allow_unwrapped=True)
    RaisesGroup(ValueError, allow_unwrapped=True, flatten_subgroups=True)
    RaisesGroup(Matcher(ValueError), allow_unwrapped=True)

    # flatten_subgroups=True does not allow nested RaisesGroup
    RaisesGroup(RaisesGroup(ValueError), flatten_subgroups=True)  # type: ignore
    # but rest is plenty fine
    RaisesGroup(ValueError, TypeError, flatten_subgroups=True)
    RaisesGroup(ValueError, match="foo", flatten_subgroups=True)
    RaisesGroup(ValueError, check=bool, flatten_subgroups=True)
    RaisesGroup(ValueError, flatten_subgroups=True)
    RaisesGroup(Matcher(ValueError), flatten_subgroups=True)

    # if they're both false we can of course specify nested raisesgroup
    RaisesGroup(RaisesGroup(ValueError))


def check_triple_nested_raisesgroup() -> None:
    with RaisesGroup(RaisesGroup(RaisesGroup(ValueError))) as e:
        assert_type(e.value, ExceptionGroup[ExceptionGroup[ExceptionGroup[ValueError]]])


def check_check_typing() -> None:
    # `BaseExceptiongroup` should perhaps be `ExceptionGroup`, but close enough
    assert_type(
        RaisesGroup(ValueError).check,
        Union[
            Callable[[BaseExceptionGroup[ValueError]], bool],
            None,
        ],
    )

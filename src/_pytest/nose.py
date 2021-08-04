"""Run testsuites written for nose."""
from _pytest.config import hookimpl
from _pytest.fixtures import getfixturemarker
from _pytest.nodes import Item
from _pytest.python import Function
from _pytest.unittest import TestCaseFunction


@hookimpl(trylast=True)
def pytest_runtest_setup(item: Item) -> None:
    if not isinstance(item, Function):
        return
    # Don't do nose style setup/teardown on direct unittest style classes.
    if isinstance(item, TestCaseFunction):
        return

    # Capture the narrowed type of item for the teardown closure,
    # see https://github.com/python/mypy/issues/2608
    func = item

    if not call_optional(func.obj, "setup"):
        # Call module level setup if there is no object level one.
        assert func.parent is not None
        call_optional(func.parent.obj, "setup")  # type: ignore[attr-defined]

    def teardown_nose() -> None:
        if not call_optional(func.obj, "teardown"):
            assert func.parent is not None
            call_optional(func.parent.obj, "teardown")  # type: ignore[attr-defined]

    # XXX This implies we only call teardown when setup worked.
    func.addfinalizer(teardown_nose)


def call_optional(obj: object, name: str) -> bool:
    method = getattr(obj, name, None)
    if method is None:
        return False
    is_fixture = getfixturemarker(method) is not None
    if is_fixture:
        return False
    if not callable(method):
        return False
    # If there are any problems allow the exception to raise rather than
    # silently ignoring it.
    method()
    return True

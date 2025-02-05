from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call() -> Iterator[None]:
    yield

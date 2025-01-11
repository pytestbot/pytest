from __future__ import annotations

from _pytest.pytester import Pytester


def test_setup_teardown_executed_for_every_fixture_usage_without_caching(
    pytester: Pytester,
) -> None:
    pytester.makepyfile(
        """
        import pytest
        import logging

        @pytest.fixture(scope="invocation")
        def fixt():
            logging.info("&&Setting up fixt&&")
            yield
            logging.info("&&Tearing down fixt&&")


        @pytest.fixture()
        def a(fixt):
            ...


        @pytest.fixture()
        def b(fixt):
            ...


        def test(a, b, fixt):
            assert False
    """
    )

    result = pytester.runpytest("--log-level=INFO")
    assert result.ret == 1
    result.stdout.fnmatch_lines(
        [
            *["*&&Setting up fixt&&*"] * 3,
            *["*&&Tearing down fixt&&*"] * 3,
        ]
    )


def test_setup_teardown_executed_for_every_getfixturevalue_usage_without_caching(
    pytester: Pytester,
) -> None:
    pytester.makepyfile(
        """
        import pytest
        import logging

        @pytest.fixture(scope="invocation")
        def fixt():
            logging.info("&&Setting up fixt&&")
            yield
            logging.info("&&Tearing down fixt&&")


        def test(request):
            random_nums = [request.getfixturevalue('fixt') for _ in range(3)]
            assert False
    """
    )
    result = pytester.runpytest("--log-level=INFO")
    assert result.ret == 1
    result.stdout.fnmatch_lines(
        [
            *["*&&Setting up fixt&&*"] * 3,
            *["*&&Tearing down fixt&&*"] * 3,
        ]
    )


def test_non_cached_fixture_generates_unique_values_per_usage(
    pytester: Pytester,
) -> None:
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture(scope="invocation")
        def random_num():
            import random
            return random.randint(-100_000_000_000, 100_000_000_000)


        @pytest.fixture()
        def a(random_num):
            return random_num


        @pytest.fixture()
        def b(random_num):
            return random_num


        def test(a, b, random_num):
            assert a != b != random_num
    """
    )
    pytester.runpytest().assert_outcomes(passed=1)


def test_non_cached_fixture_generates_unique_values_per_getfixturevalue_usage(
    pytester: Pytester,
) -> None:
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture(scope="invocation")
        def random_num():
            import random
            yield random.randint(-100_000_000_000, 100_000_000_000)


        def test(request):
            random_nums = [request.getfixturevalue('random_num') for _ in range(3)]
            assert random_nums[0] != random_nums[1] != random_nums[2]
    """
    )
    pytester.runpytest().assert_outcomes(passed=1)

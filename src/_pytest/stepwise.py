from typing import List
from typing import Optional
from typing import TYPE_CHECKING

import pytest
from _pytest import nodes
from _pytest.config import Config
from _pytest.config.argparsing import Parser
from _pytest.main import Session
from _pytest.reports import TestReport

if TYPE_CHECKING:
    from _pytest.cacheprovider import Cache

STEPWISE_CACHE_DIR = "cache/stepwise"
NO_PREVIOUSLY_FAILED_WONT_SKIP = "no previously failed tests, not skipping."
PREVIOUSLY_FAILED_TEST_NOT_FOUND = "previously failed test not found, not skipping."
SKIPPING_PASSED_ITEMS = "skipping {} already passed items."


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("general")
    group.addoption(
        "--sw",
        "--stepwise",
        action="store_true",
        default=False,
        dest="stepwise",
        help="exit on test failure and continue from last failing test next time",
    )
    group.addoption(
        "--stepwise-skip",
        "--sw-skip",
        action="store_true",
        default=False,
        dest="stepwise_skip",
        help="ignore the first failing test but stop on the next failing test",
    )


@pytest.hookimpl
def pytest_configure(config: Config) -> None:
    # We should always have a cache as cache provider plugin uses tryfirst=True
    if config.option.stepwise:
        config.pluginmanager.register(StepwisePlugin(config), "stepwiseplugin")


def pytest_sessionfinish(session: Session) -> None:
    config = session.config
    assert config.cache is not None
    if not config.option.stepwise:
        # This hook exists so that --cache-show when empty will not output the empty [] for stepwise by default.
        # Perhaps this is ok to set in the unconfigure hook?  But I added it here to avoid changing behaviour.
        config.cache.set(STEPWISE_CACHE_DIR, [])


class StepwisePlugin:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.session: Optional[Session] = None
        self.report_status = ""
        assert config.cache is not None
        self.cache: Cache = config.cache
        self.lastfailed: Optional[str] = self.cache.get(STEPWISE_CACHE_DIR, None)
        self.skip: bool = config.option.stepwise_skip

    def pytest_sessionstart(self, session: Session) -> None:
        self.session = session

    def pytest_collection_modifyitems(
        self, config: Config, items: List[nodes.Item]
    ) -> None:
        if not self.lastfailed:
            self.report_status = NO_PREVIOUSLY_FAILED_WONT_SKIP
            return
        already_passed = []
        found = False

        # Make a list of all tests that have been run before the last failing one.
        for item in items:
            if item.nodeid == self.lastfailed:
                found = True
                break
            else:
                already_passed.append(item)
        # If the previously failed test was not found among the test items,
        # do not skip any tests.
        if not found:
            self.report_status = PREVIOUSLY_FAILED_TEST_NOT_FOUND
            already_passed = []
        else:
            self.report_status = SKIPPING_PASSED_ITEMS.format(len(already_passed))

        for item in already_passed:
            items.remove(item)

        config.hook.pytest_deselected(items=already_passed)

    def pytest_runtest_logreport(self, report: TestReport) -> None:
        if report.failed:
            if self.skip:
                # Remove test from the failed ones (if it exists) and unset the skip option
                # to make sure the following tests will not be skipped.
                if report.nodeid == self.lastfailed:
                    self.lastfailed = None

                self.skip = False
            else:
                # Mark test as the last failing and interrupt the test session.
                self.lastfailed = report.nodeid
                assert self.session is not None
                self.session.shouldstop = (
                    "Test failed, continuing from this test next run."
                )

        else:
            # If the test was actually run and did pass.
            if report.when == "call":
                # Remove test from the failed ones, if exists.
                if report.nodeid == self.lastfailed:
                    self.lastfailed = None

    def pytest_report_collectionfinish(self) -> Optional[str]:
        if self.config.getoption("verbose") >= 0 and self.report_status:
            return f"stepwise: {self.report_status}"
        return None

    def pytest_sessionfinish(self) -> None:
        self.cache.set(STEPWISE_CACHE_DIR, self.lastfailed)

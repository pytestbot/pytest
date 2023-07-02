"""Basic collect and runtest protocol implementations."""
import bdb
import dataclasses
import os
import sys
from typing import Callable
from typing import cast
from typing import Dict
from typing import final
from typing import Generic
from typing import List
from typing import Optional
from typing import Tuple
from typing import Type
from typing import TYPE_CHECKING
from typing import TypeVar
from typing import Union

from .reports import BaseReport
from .reports import CollectErrorRepr
from .reports import CollectReport
from .reports import TestReport
from _pytest import timing
from _pytest._code.code import ExceptionChainRepr
from _pytest._code.code import ExceptionInfo
from _pytest._code.code import TerminalRepr
from _pytest.config.argparsing import Parser
from _pytest.deprecated import check_ispytest
from _pytest.nodes import Collector
from _pytest.nodes import Item
from _pytest.nodes import Node
from _pytest.outcomes import Exit
from _pytest.outcomes import OutcomeException
from _pytest.outcomes import Skipped
from _pytest.outcomes import TEST_OUTCOME

if sys.version_info[:2] < (3, 11):
    from exceptiongroup import BaseExceptionGroup

if TYPE_CHECKING:
    from typing_extensions import Literal

    from _pytest.main import Session
    from _pytest.terminal import TerminalReporter

#
# pytest plugin hooks.


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("terminal reporting", "Reporting", after="general")
    group.addoption(
        "--durations",
        action="store",
        type=int,
        default=None,
        metavar="N",
        help="Show N slowest setup/test durations (N=0 for all)",
    )
    group.addoption(
        "--durations-min",
        action="store",
        type=float,
        default=0.005,
        metavar="N",
        help="Minimal duration in seconds for inclusion in slowest list. "
        "Default: 0.005.",
    )


def pytest_terminal_summary(terminalreporter: "TerminalReporter") -> None:
    durations = terminalreporter.config.option.durations
    durations_min = terminalreporter.config.option.durations_min
    verbose = terminalreporter.config.getvalue("verbose")
    if durations is None:
        return
    tr = terminalreporter
    dlist = []
    for replist in tr.stats.values():
        for rep in replist:
            if hasattr(rep, "duration"):
                dlist.append(rep)
    if not dlist:
        return
    dlist.sort(key=lambda x: x.duration, reverse=True)  # type: ignore[no-any-return]
    if not durations:
        tr.write_sep("=", "slowest durations")
    else:
        tr.write_sep("=", "slowest %s durations" % durations)
        dlist = dlist[:durations]

    for i, rep in enumerate(dlist):
        if verbose < 2 and rep.duration < durations_min:
            tr.write_line("")
            tr.write_line(
                "(%s durations < %gs hidden.  Use -vv to show these durations.)"
                % (len(dlist) - i, durations_min)
            )
            break
        tr.write_line(f"{rep.duration:02.2f}s {rep.when:<8} {rep.nodeid}")


def pytest_sessionstart(session: "Session") -> None:
    session._setupstate = SetupState()


def pytest_sessionfinish(session: "Session") -> None:
    session._setupstate.teardown_exact(None)


def pytest_runtest_protocol(item: Item, nextitem: Optional[Item]) -> bool:
    ihook = item.ihook
    ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    runtestprotocol(item, nextitem=nextitem)
    ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True


def runtestprotocol(
    item: Item, log: bool = True, nextitem: Optional[Item] = None
) -> List[TestReport]:
    hasrequest = hasattr(item, "_request")
    if hasrequest and not item._request:  # type: ignore[attr-defined]
        # This only happens if the item is re-run, as is done by
        # pytest-rerunfailures.
        item._initrequest()  # type: ignore[attr-defined]
    rep = call_and_report(item, "setup", log)
    reports = [rep]
    if rep.passed:
        if item.config.getoption("setupshow", False):
            show_test_item(item)
        if not item.config.getoption("setuponly", False):
            reports.append(call_and_report(item, "call", log))
    reports.append(call_and_report(item, "teardown", log, nextitem=nextitem))
    # After all teardown hooks have been called
    # want funcargs and request info to go away.
    if hasrequest:
        item._request = False  # type: ignore[attr-defined]
        item.funcargs = None  # type: ignore[attr-defined]
    return reports


def show_test_item(item: Item) -> None:
    """Show test function, parameters and the fixtures of the test item."""
    tw = item.config.get_terminal_writer()
    tw.line()
    tw.write(" " * 8)
    tw.write(item.nodeid)
    used_fixtures = sorted(getattr(item, "fixturenames", []))
    if used_fixtures:
        tw.write(" (fixtures used: {})".format(", ".join(used_fixtures)))
    tw.flush()


def pytest_runtest_setup(item: Item) -> None:
    _update_current_test_var(item, "setup")
    item.session._setupstate.setup(item)


def pytest_runtest_call(item: Item) -> None:
    _update_current_test_var(item, "call")
    try:
        del sys.last_type
        del sys.last_value
        del sys.last_traceback
    except AttributeError:
        pass
    try:
        item.runtest()
    except Exception as e:
        # Store trace info to allow postmortem debugging
        sys.last_type = type(e)
        sys.last_value = e
        assert e.__traceback__ is not None
        # Skip *this* frame
        sys.last_traceback = e.__traceback__.tb_next
        raise e


def pytest_runtest_teardown(item: Item, nextitem: Optional[Item]) -> None:
    _update_current_test_var(item, "teardown")
    item.session._setupstate.teardown_exact(nextitem)
    _update_current_test_var(item, None)


def _update_current_test_var(
    item: Item, when: Optional["Literal['setup', 'call', 'teardown']"]
) -> None:
    """Update :envvar:`PYTEST_CURRENT_TEST` to reflect the current item and stage.

    If ``when`` is None, delete ``PYTEST_CURRENT_TEST`` from the environment.
    """
    var_name = "PYTEST_CURRENT_TEST"
    if when:
        value = f"{item.nodeid} ({when})"
        # don't allow null bytes on environment variables (see #2644, #2957)
        value = value.replace("\x00", "(null)")
        os.environ[var_name] = value
    else:
        os.environ.pop(var_name)


def pytest_report_teststatus(report: BaseReport) -> Optional[Tuple[str, str, str]]:
    if report.when in ("setup", "teardown"):
        if report.failed:
            #      category, shortletter, verbose-word
            return "error", "E", "ERROR"
        elif report.skipped:
            return "skipped", "s", "SKIPPED"
        else:
            return "", "", ""
    return None


#
# Implementation


def call_and_report(
    item: Item, when: "Literal['setup', 'call', 'teardown']", log: bool = True, **kwds
) -> TestReport:
    call = call_runtest_hook(item, when, **kwds)
    hook = item.ihook
    report: TestReport = hook.pytest_runtest_makereport(item=item, call=call)
    if log:
        hook.pytest_runtest_logreport(report=report)
    if check_interactive_exception(call, report):
        hook.pytest_exception_interact(node=item, call=call, report=report)
    return report


def check_interactive_exception(call: "CallInfo[object]", report: BaseReport) -> bool:
    """Check whether the call raised an exception that should be reported as
    interactive."""
    if call.excinfo is None:
        # Didn't raise.
        return False
    if hasattr(report, "wasxfail"):
        # Exception was expected.
        return False
    if isinstance(call.excinfo.value, (Skipped, bdb.BdbQuit)):
        # Special control flow exception.
        return False
    return True


def call_runtest_hook(
    item: Item, when: "Literal['setup', 'call', 'teardown']", **kwds
) -> "CallInfo[None]":
    if when == "setup":
        ihook: Callable[..., None] = item.ihook.pytest_runtest_setup
    elif when == "call":
        ihook = item.ihook.pytest_runtest_call
    elif when == "teardown":
        ihook = item.ihook.pytest_runtest_teardown
    else:
        assert False, f"Unhandled runtest hook case: {when}"
    reraise: Tuple[Type[BaseException], ...] = (Exit,)
    if not item.config.getoption("usepdb", False):
        reraise += (KeyboardInterrupt,)
    return CallInfo.from_call(
        lambda: ihook(item=item, **kwds), when=when, reraise=reraise
    )


TResult = TypeVar("TResult", covariant=True)


@final
@dataclasses.dataclass
class CallInfo(Generic[TResult]):
    """Result/Exception info of a function invocation."""

    _result: Optional[TResult]
    #: The captured exception of the call, if it raised.
    excinfo: Optional[ExceptionInfo[BaseException]]
    #: The system time when the call started, in seconds since the epoch.
    start: float
    #: The system time when the call ended, in seconds since the epoch.
    stop: float
    #: The call duration, in seconds.
    duration: float
    #: The context of invocation: "collect", "setup", "call" or "teardown".
    when: "Literal['collect', 'setup', 'call', 'teardown']"

    def __init__(
        self,
        result: Optional[TResult],
        excinfo: Optional[ExceptionInfo[BaseException]],
        start: float,
        stop: float,
        duration: float,
        when: "Literal['collect', 'setup', 'call', 'teardown']",
        *,
        _ispytest: bool = False,
    ) -> None:
        check_ispytest(_ispytest)
        self._result = result
        self.excinfo = excinfo
        self.start = start
        self.stop = stop
        self.duration = duration
        self.when = when

    @property
    def result(self) -> TResult:
        """The return value of the call, if it didn't raise.

        Can only be accessed if excinfo is None.
        """
        if self.excinfo is not None:
            raise AttributeError(f"{self!r} has no valid result")
        # The cast is safe because an exception wasn't raised, hence
        # _result has the expected function return type (which may be
        #  None, that's why a cast and not an assert).
        return cast(TResult, self._result)

    @classmethod
    def from_call(
        cls,
        func: "Callable[[], TResult]",
        when: "Literal['collect', 'setup', 'call', 'teardown']",
        reraise: Optional[
            Union[Type[BaseException], Tuple[Type[BaseException], ...]]
        ] = None,
    ) -> "CallInfo[TResult]":
        """Call func, wrapping the result in a CallInfo.

        :param func:
            The function to call. Called without arguments.
        :param when:
            The phase in which the function is called.
        :param reraise:
            Exception or exceptions that shall propagate if raised by the
            function, instead of being wrapped in the CallInfo.
        """
        excinfo = None
        start = timing.time()
        precise_start = timing.perf_counter()
        try:
            result: Optional[TResult] = func()
        except BaseException:
            excinfo = ExceptionInfo.from_current()
            if reraise is not None and isinstance(excinfo.value, reraise):
                raise
            result = None
        # use the perf counter
        precise_stop = timing.perf_counter()
        duration = precise_stop - precise_start
        stop = timing.time()
        return cls(
            start=start,
            stop=stop,
            duration=duration,
            when=when,
            result=result,
            excinfo=excinfo,
            _ispytest=True,
        )

    def __repr__(self) -> str:
        if self.excinfo is None:
            return f"<CallInfo when={self.when!r} result: {self._result!r}>"
        return f"<CallInfo when={self.when!r} excinfo={self.excinfo!r}>"


def pytest_runtest_makereport(item: Item, call: CallInfo[None]) -> TestReport:
    return TestReport.from_item_and_call(item, call)


def pytest_make_collect_report(collector: Collector) -> CollectReport:
    call = CallInfo.from_call(lambda: list(collector.collect()), "collect")
    longrepr: Union[None, Tuple[str, int, str], str, TerminalRepr] = None
    if not call.excinfo:
        outcome: Literal["passed", "skipped", "failed"] = "passed"
    else:
        skip_exceptions = [Skipped]
        unittest = sys.modules.get("unittest")
        if unittest is not None:
            # Type ignored because unittest is loaded dynamically.
            skip_exceptions.append(unittest.SkipTest)  # type: ignore
        if isinstance(call.excinfo.value, tuple(skip_exceptions)):
            outcome = "skipped"
            r_ = collector._repr_failure_py(call.excinfo, "line")
            assert isinstance(r_, ExceptionChainRepr), repr(r_)
            r = r_.reprcrash
            assert r
            longrepr = (str(r.path), r.lineno, r.message)
        else:
            outcome = "failed"
            errorinfo = collector.repr_failure(call.excinfo)
            if not hasattr(errorinfo, "toterminal"):
                assert isinstance(errorinfo, str)
                errorinfo = CollectErrorRepr(errorinfo)
            longrepr = errorinfo
    result = call.result if not call.excinfo else None
    rep = CollectReport(collector.nodeid, outcome, longrepr, result)
    rep.call = call  # type: ignore # see collect_one_node
    return rep


class SetupState:
    """Shared state for setting up/tearing down test items or collectors
    in a session.

    Suppose we have a collection tree as follows:

    <Session session>
        <Module mod1>
            <Function item1>
        <Module mod2>
            <Function item2>

    The SetupState maintains a stack. The stack starts out empty:

        []

    During the setup phase of item1, setup(item1) is called. What it does
    is:

        push session to stack, run session.setup()
        push mod1 to stack, run mod1.setup()
        push item1 to stack, run item1.setup()

    The stack is:

        [session, mod1, item1]

    While the stack is in this shape, it is allowed to add finalizers to
    each of session, mod1, item1 using addfinalizer().

    During the teardown phase of item1, teardown_exact(item2) is called,
    where item2 is the next item to item1. What it does is:

        pop item1 from stack, run its teardowns
        pop mod1 from stack, run its teardowns

    mod1 was popped because it ended its purpose with item1. The stack is:

        [session]

    During the setup phase of item2, setup(item2) is called. What it does
    is:

        push mod2 to stack, run mod2.setup()
        push item2 to stack, run item2.setup()

    Stack:

        [session, mod2, item2]

    During the teardown phase of item2, teardown_exact(None) is called,
    because item2 is the last item. What it does is:

        pop item2 from stack, run its teardowns
        pop mod2 from stack, run its teardowns
        pop session from stack, run its teardowns

    Stack:

        []

    The end!
    """

    def __init__(self) -> None:
        # The stack is in the dict insertion order.
        self.stack: Dict[
            Node,
            Tuple[
                # Node's finalizers.
                List[Callable[[], object]],
                # Node's exception, if its setup raised.
                Optional[Union[OutcomeException, Exception]],
            ],
        ] = {}

    def setup(self, item: Item) -> None:
        """Setup objects along the collector chain to the item."""
        needed_collectors = item.listchain()

        # If a collector fails its setup, fail its entire subtree of items.
        # The setup is not retried for each item - the same exception is used.
        for col, (finalizers, exc) in self.stack.items():
            assert col in needed_collectors, "previous item was not torn down properly"
            if exc:
                raise exc

        for col in needed_collectors[len(self.stack) :]:
            assert col not in self.stack
            # Push onto the stack.
            self.stack[col] = ([col.teardown], None)
            try:
                col.setup()
            except TEST_OUTCOME as exc:
                self.stack[col] = (self.stack[col][0], exc)
                raise exc

    def addfinalizer(self, finalizer: Callable[[], object], node: Node) -> None:
        """Attach a finalizer to the given node.

        The node must be currently active in the stack.
        """
        assert node and not isinstance(node, tuple)
        assert callable(finalizer)
        assert node in self.stack, (node, self.stack)
        self.stack[node][0].append(finalizer)

    def teardown_exact(self, nextitem: Optional[Item]) -> None:
        """Teardown the current stack up until reaching nodes that nextitem
        also descends from.

        When nextitem is None (meaning we're at the last item), the entire
        stack is torn down.
        """
        needed_collectors = nextitem and nextitem.listchain() or []
        exceptions: List[BaseException] = []
        while self.stack:
            if list(self.stack.keys()) == needed_collectors[: len(self.stack)]:
                break
            node, (finalizers, _) = self.stack.popitem()
            these_exceptions = []
            while finalizers:
                fin = finalizers.pop()
                try:
                    fin()
                except TEST_OUTCOME as e:
                    these_exceptions.append(e)

            if len(these_exceptions) == 1:
                exceptions.extend(these_exceptions)
            elif these_exceptions:
                msg = f"errors while tearing down {node!r}"
                exceptions.append(BaseExceptionGroup(msg, these_exceptions[::-1]))

        if len(exceptions) == 1:
            raise exceptions[0]
        elif exceptions:
            raise BaseExceptionGroup("errors during test teardown", exceptions[::-1])
        if nextitem is None:
            assert not self.stack


def collect_one_node(collector: Collector) -> CollectReport:
    ihook = collector.ihook
    ihook.pytest_collectstart(collector=collector)
    rep: CollectReport = ihook.pytest_make_collect_report(collector=collector)
    call = rep.__dict__.pop("call", None)
    if call and check_interactive_exception(call, rep):
        ihook.pytest_exception_interact(node=collector, call=call, report=rep)
    return rep

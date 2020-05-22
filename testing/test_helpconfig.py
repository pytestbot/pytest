import pytest
from _pytest.config import ExitCode


def test_version(testdir, pytestconfig):
    testdir.monkeypatch.delenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD")
    result = testdir.runpytest("--version")
    assert result.ret == 0
    # p = py.path.local(py.__file__).dirpath()
    result.stderr.fnmatch_lines(
        ["*pytest*{}*imported from*".format(pytest.__version__)]
    )
    if pytestconfig.pluginmanager.list_plugin_distinfo():
        result.stderr.fnmatch_lines(["*setuptools registered plugins:", "*at*"])


def test_help(testdir):
    result = testdir.runpytest("--help")
    assert result.ret == 0
    result.stdout.fnmatch_lines(
        """
        reporting:
          --durations=N *
        *setup.cfg*
        *minversion*
        *to see*markers*pytest --markers*
        *to see*fixtures*pytest --fixtures*
    """
    )


def test_help_matchexpr(testdir):
    result = testdir.runpytest("--help")
    assert result.ret == 0
    result.stdout.fnmatch_lines(
        """
          -m MARKEXPR           only run tests matching given mark expression.
                                For example: -m 'mark1 and not mark2'.
                                Multiple -m cli options are appended with 'and'.
        *
        """
    )


def test_help_keywordexpr(testdir):
    result = testdir.runpytest("--help")
    assert result.ret == 0
    result.stdout.fnmatch_lines(
        """
          -k EXPRESSION         only run tests which match the given substring
                                expression. An expression is a python evaluatable
                                expression where all names are substring-matched against
                                test names and their parent classes. Example: -k
                                'test_method or test_other' matches all test functions
                                and classes whose name contains 'test_method' or
                                'test_other', while -k 'not test_method' matches those
                                that don't contain 'test_method' in their names. -k 'not
                                test_method and not test_other' will eliminate the
                                matches. Additionally keywords are matched to classes
                                and functions containing extra names in their
                                'extra_keyword_matches' set, as well as functions which
                                have names assigned directly to them. The matching is
                                case-insensitive. Multiple -k cli options are appended
                                with 'and'.
        *
        """
    )


def test_hookvalidation_unknown(testdir):
    testdir.makeconftest(
        """
        def pytest_hello(xyz):
            pass
    """
    )
    result = testdir.runpytest()
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*unknown hook*pytest_hello*"])


def test_hookvalidation_optional(testdir):
    testdir.makeconftest(
        """
        import pytest
        @pytest.hookimpl(optionalhook=True)
        def pytest_hello(xyz):
            pass
    """
    )
    result = testdir.runpytest()
    assert result.ret == ExitCode.NO_TESTS_COLLECTED


def test_traceconfig(testdir):
    result = testdir.runpytest("--traceconfig")
    result.stdout.fnmatch_lines(["*using*pytest*py*", "*active plugins*"])


def test_debug(testdir):
    result = testdir.runpytest_subprocess("--debug")
    assert result.ret == ExitCode.NO_TESTS_COLLECTED
    p = testdir.tmpdir.join("pytestdebug.log")
    assert "pytest_sessionstart" in p.read()


def test_PYTEST_DEBUG(testdir, monkeypatch):
    monkeypatch.setenv("PYTEST_DEBUG", "1")
    result = testdir.runpytest_subprocess()
    assert result.ret == ExitCode.NO_TESTS_COLLECTED
    result.stderr.fnmatch_lines(
        ["*pytest_plugin_registered*", "*manager*PluginManager*"]
    )

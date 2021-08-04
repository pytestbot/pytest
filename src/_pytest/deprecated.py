"""Deprecation messages and bits of code used elsewhere in the codebase that
is planned to be removed in the next pytest release.

Keeping it in a central location makes it easy to track what is deprecated and should
be removed when the time comes.

All constants defined in this module should be either instances of
:class:`PytestWarning`, or :class:`UnformattedWarning`
in case of warnings which need to format their messages.
"""
from warnings import warn

from _pytest.warning_types import PytestDeprecationWarning
from _pytest.warning_types import UnformattedWarning

# set of plugins which have been integrated into the core; we use this list to ignore
# them during registration to avoid conflicts
DEPRECATED_EXTERNAL_PLUGINS = {
    "pytest_catchlog",
    "pytest_capturelog",
    "pytest_faulthandler",
}


FILLFUNCARGS = UnformattedWarning(
    PytestDeprecationWarning,
    "{name} is deprecated, use "
    "function._request._fillfixtures() instead if you cannot avoid reaching into internals.",
)

PYTEST_COLLECT_MODULE = UnformattedWarning(
    PytestDeprecationWarning,
    "pytest.collect.{name} was moved to pytest.{name}\n"
    "Please update to the new name.",
)

YIELD_FIXTURE = PytestDeprecationWarning(
    "@pytest.yield_fixture is deprecated.\n"
    "Use @pytest.fixture instead; they are the same."
)

MINUS_K_DASH = PytestDeprecationWarning(
    "The `-k '-expr'` syntax to -k is deprecated.\nUse `-k 'not expr'` instead."
)

MINUS_K_COLON = PytestDeprecationWarning(
    "The `-k 'expr:'` syntax to -k is deprecated.\n"
    "Please open an issue if you use this and want a replacement."
)

WARNING_CAPTURED_HOOK = PytestDeprecationWarning(
    "The pytest_warning_captured is deprecated and will be removed in a future release.\n"
    "Please use pytest_warning_recorded instead."
)

WARNING_CMDLINE_PREPARSE_HOOK = PytestDeprecationWarning(
    "The pytest_cmdline_preparse hook is deprecated and will be removed in a future release. \n"
    "Please use pytest_load_initial_conftests hook instead."
)

FSCOLLECTOR_GETHOOKPROXY_ISINITPATH = PytestDeprecationWarning(
    "The gethookproxy() and isinitpath() methods of FSCollector and Package are deprecated; "
    "use self.session.gethookproxy() and self.session.isinitpath() instead. "
)

STRICT_OPTION = PytestDeprecationWarning(
    "The --strict option is deprecated, use --strict-markers instead."
)

PRIVATE = PytestDeprecationWarning("A private pytest class or function was used.")

UNITTEST_SKIP_DURING_COLLECTION = PytestDeprecationWarning(
    "Raising unittest.SkipTest to skip tests during collection is deprecated. "
    "Use pytest.skip() instead."
)

ARGUMENT_PERCENT_DEFAULT = PytestDeprecationWarning(
    'pytest now uses argparse. "%default" should be changed to "%(default)s"',
)

ARGUMENT_TYPE_STR_CHOICE = UnformattedWarning(
    PytestDeprecationWarning,
    "`type` argument to addoption() is the string {typ!r}."
    " For choices this is optional and can be omitted, "
    " but when supplied should be a type (for example `str` or `int`)."
    " (options: {names})",
)

ARGUMENT_TYPE_STR = UnformattedWarning(
    PytestDeprecationWarning,
    "`type` argument to addoption() is the string {typ!r}, "
    " but when supplied should be a type (for example `str` or `int`)."
    " (options: {names})",
)


HOOK_LEGACY_PATH_ARG = UnformattedWarning(
    PytestDeprecationWarning,
    "The ({pylib_path_arg}: py.path.local) argument is deprecated, please use ({pathlib_path_arg}: pathlib.Path)\n"
    "see https://docs.pytest.org/en/latest/deprecations.html"
    "#py-path-local-arguments-for-hooks-replaced-with-pathlib-path",
)

WARNS_NONE_ARG = PytestDeprecationWarning(
    "Passing None to catch any warning has been deprecated, pass no arguments instead:\n"
    " Replace pytest.warns(None) by simply pytest.warns()."
)

# You want to make some `__init__` or function "private".
#
#   def my_private_function(some, args):
#       ...
#
# Do this:
#
#   def my_private_function(some, args, *, _ispytest: bool = False):
#       check_ispytest(_ispytest)
#       ...
#
# Change all internal/allowed calls to
#
#   my_private_function(some, args, _ispytest=True)
#
# All other calls will get the default _ispytest=False and trigger
# the warning (possibly error in the future).


def check_ispytest(ispytest: bool) -> None:
    if not ispytest:
        warn(PRIVATE, stacklevel=3)

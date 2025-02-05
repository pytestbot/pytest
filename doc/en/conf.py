from __future__ import annotations

import os
from pathlib import Path
import shutil
from textwrap import dedent
from typing import TYPE_CHECKING

from pytest import __version__ as full_version


if TYPE_CHECKING:
    import sphinx.application

PROJECT_ROOT_DIR = Path(__file__).parents[2].resolve()

# -- Project information ---------------------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "pytest"
copyright = "2015, holger krekel and pytest-dev team"
version = full_version.split("+")[0]
release = ".".join(version.split(".")[:2])

# -- General configuration -------------------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

root_doc = "index"
extensions = [
    "pygments_pytest",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.todo",
    "sphinx.ext.viewcode",
    "sphinx_removed_in",
    "sphinxcontrib_trio",
    "sphinxcontrib.towncrier.ext",  # provides `towncrier-draft-entries` directive
    "sphinx_issues",  # implements `:issue:`, `:pr:` and other GH-related roles
]

# Building PDF docs on readthedocs requires inkscape for svg to pdf
# conversion. The relevant plugin is not useful for normal HTML builds, but
# it still raises warnings and fails CI if inkscape is not available. So
# only use the plugin if inkscape is actually available.
if shutil.which("inkscape"):
    extensions.append("sphinxcontrib.inkscapeconverter")

exclude_patterns = [
    "_build",
    "naming20.rst",
    "test/*",
    "old_*",
    "*attic*",
    "*/attic*",
    "funcargs.rst",
    "setup.rst",
    "example/remoteinterp.rst",
]
templates_path = ["_templates"]
default_role = "literal"

nitpicky = True
nitpick_ignore = [
    # TODO (fix in pluggy?)
    ("py:class", "HookCaller"),
    ("py:class", "HookspecMarker"),
    ("py:exc", "PluginValidationError"),
    # Might want to expose/TODO (https://github.com/pytest-dev/pytest/issues/7469)
    ("py:class", "ExceptionRepr"),
    ("py:class", "Exit"),
    ("py:class", "SubRequest"),
    ("py:class", "SubRequest"),
    ("py:class", "TerminalReporter"),
    ("py:class", "_pytest._code.code.TerminalRepr"),
    ("py:class", "TerminalRepr"),
    ("py:class", "_pytest.fixtures.FixtureFunctionMarker"),
    ("py:class", "_pytest.fixtures.FixtureFunctionDefinition"),
    ("py:class", "_pytest.logging.LogCaptureHandler"),
    ("py:class", "_pytest.mark.structures.ParameterSet"),
    # Intentionally undocumented/private
    ("py:class", "_pytest._code.code.Traceback"),
    ("py:class", "_pytest._py.path.LocalPath"),
    ("py:class", "_pytest.capture.CaptureResult"),
    ("py:class", "_pytest.compat.NotSetType"),
    ("py:class", "_pytest.python.PyCollector"),
    ("py:class", "_pytest.python.PyobjMixin"),
    ("py:class", "_pytest.python_api.RaisesContext"),
    ("py:class", "_pytest.recwarn.WarningsChecker"),
    ("py:class", "_pytest.reports.BaseReport"),
    # Undocumented third parties
    ("py:class", "_tracing.TagTracerSub"),
    ("py:class", "warnings.WarningMessage"),
    # Undocumented type aliases
    ("py:class", "LEGACY_PATH"),
    ("py:class", "_PluggyPlugin"),
    # TypeVars
    ("py:class", "_pytest._code.code.E"),
    ("py:class", "E"),  # due to delayed annotation
    ("py:class", "_pytest.fixtures.FixtureFunction"),
    ("py:class", "_pytest.nodes._NodeType"),
    ("py:class", "_NodeType"),  # due to delayed annotation
    ("py:class", "_pytest.python_api.E"),
    ("py:class", "_pytest.recwarn.T"),
    ("py:class", "_pytest.runner.TResult"),
    ("py:obj", "_pytest.fixtures.FixtureValue"),
    ("py:obj", "_pytest.stash.T"),
    ("py:class", "_ScopeName"),
]

add_module_names = False

# -- Options for Autodoc --------------------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html#configuration

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_typehints_description_target = "documented"

# -- Options for intersphinx ----------------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/extensions/intersphinx.html#configuration

intersphinx_mapping = {
    "pluggy": ("https://pluggy.readthedocs.io/en/stable", None),
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable", None),
    "pip": ("https://pip.pypa.io/en/stable", None),
    "tox": ("https://tox.wiki/en/stable", None),
    "virtualenv": ("https://virtualenv.pypa.io/en/stable", None),
    "setuptools": ("https://setuptools.pypa.io/en/stable", None),
    "packaging": ("https://packaging.python.org/en/latest", None),
}

# -- Options for todo -----------------------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/extensions/todo.html#configuration

todo_include_todos = True

# -- Options for linkcheck builder ----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-the-linkcheck-builder

linkcheck_ignore = [
    "https://blogs.msdn.microsoft.com/bharry/2017/06/28/testing-in-a-cloud-delivery-cadence/",
    "http://pythontesting.net/framework/pytest-introduction/",
    r"https://pypi\.org/project/pytest.*",
    r"https://github\.com/sponsors/.*",
    r"https://github\.com/pytest-dev/pytest/issues/.*",
    r"https://github\.com/pytest-dev/pytest/pull/.*",
]

linkcheck_workers = 20
linkcheck_timeout = 30
linkcheck_retries = 2
linkcheck_anchors = False
linkcheck_rate_limit_timeout = 2.0
linkcheck_delay = 2.0

# -- Options for HTML output ----------------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "furo"
html_theme_options = {"sidebar_hide_name": True}

html_static_path = ["_static"]
html_css_files = [
    "pytest-custom.css",
]

html_title = "pytest documentation"
html_short_title = f"pytest-{release}"

html_logo = "_static/pytest1.png"
html_favicon = "img/favicon.png"

html_use_index = False
html_show_sourcelink = False

html_baseurl = "https://docs.pytest.org/en/stable/"

# -- Options for HTML Help output -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-help-output

htmlhelp_basename = "pytestdoc"


# -- Options for manual page output ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-manual-page-output

man_pages = [
    ("how-to/usage", "pytest", "pytest usage", ["holger krekel at merlinux eu"], 1)
]

# -- Options for epub output ----------------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-epub-output

epub_title = "pytest"
epub_author = "holger krekel at merlinux eu"
epub_publisher = "holger krekel at merlinux eu"
epub_copyright = "2013, holger krekel et alii"

# -- Options for LaTeX output --------------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-latex-output

latex_documents = [
    (
        "contents",
        "pytest.tex",
        "pytest Documentation",
        "holger krekel, trainer and consultant, https://merlinux.eu/",
        "manual",
    )
]
latex_domain_indices = False
latex_engine = "lualatex"
latex_elements = {
    "preamble": dedent(
        r"""
        \directlua{
            luaotfload.add_fallback("fallbacks", {
                "Noto Serif CJK SC:style=Regular;",
                "Symbola:Style=Regular;"
            })
        }

        \setmainfont{FreeSerif}[RawFeature={fallback=fallbacks}]
        """
    )
}

# -- Options for texinfo output -------------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-texinfo-output

texinfo_documents = [
    (
        root_doc,
        "pytest",
        "pytest Documentation",
        (
            "Holger Krekel@*Benjamin Peterson@*Ronny Pfannschmidt@*"
            "Floris Bruynooghe@*others"
        ),
        "pytest",
        "simple powerful testing with Python",
        "Programming",
        1,
    )
]

# -- Options for towncrier_draft extension --------------------------------------------
# https://sphinxcontrib-towncrier.readthedocs.io/en/latest/#how-to-use-this

towncrier_draft_autoversion_mode = "draft"  # or: 'sphinx-version', 'sphinx-release'
towncrier_draft_include_empty = True
towncrier_draft_working_directory = PROJECT_ROOT_DIR
towncrier_draft_config_path = "pyproject.toml"  # relative to cwd

# -- Options for sphinx_issues extension -----------------------------------
# https://github.com/sloria/sphinx-issues#installation-and-configuration

issues_github_path = "pytest-dev/pytest"

# -- Custom Read the Docs build configuration -----------------------------------------
# https://docs.readthedocs.io/en/stable/reference/environment-variables.html#environment-variable-reference
# https://www.sphinx-doc.org/en/master/usage/restructuredtext/directives.html#including-content-based-on-tags

IS_RELEASE_ON_RTD = (
    os.getenv("READTHEDOCS", "False") == "True"
    and os.environ["READTHEDOCS_VERSION_TYPE"] == "tag"
)
if IS_RELEASE_ON_RTD:
    tags: set[str]
    # pylint: disable-next=used-before-assignment
    tags.add("is_release")  # noqa: F821

# -- Custom documentation plugin ------------------------------------------------------
# https://www.sphinx-doc.org/en/master/development/tutorials/extending_syntax.html#the-setup-function


def setup(app: sphinx.application.Sphinx) -> None:
    app.add_crossref_type(
        "fixture",
        "fixture",
        objname="built-in fixture",
        indextemplate="pair: %s; fixture",
    )

    app.add_object_type(
        "confval",
        "confval",
        objname="configuration value",
        indextemplate="pair: %s; configuration value",
    )

    app.add_object_type(
        "globalvar",
        "globalvar",
        objname="global variable interpreted by pytest",
        indextemplate="pair: %s; global variable interpreted by pytest",
    )

    app.add_crossref_type(
        directivename="hook",
        rolename="hook",
        objname="pytest hook",
        indextemplate="pair: %s; hook",
    )

    # legacypath.py monkey-patches pytest.Testdir in. Import the file so
    # that autodoc can discover references to it.
    import _pytest.legacypath  # noqa: F401

#
# pytest documentation build configuration file, created by
# sphinx-quickstart on Fri Oct  8 17:54:28 2010.
#
# This file is execfile()d with the current directory set to its containing dir.
#
# Note that not all possible configuration values are present in this
# autogenerated file.
#
# All configuration values have a default; values that are commented out
# serve to show the default.
# The version info for the project you're documenting, acts as replacement for
# |version| and |release|, also used in various other places throughout the
# built documents.
#
# The full version, including alpha/beta/rc tags.
# The short X.Y version.
from __future__ import annotations

import os
from pathlib import Path
import shutil
from textwrap import dedent
from typing import TYPE_CHECKING

from _pytest import __version__ as full_version


version = full_version.split("+")[0]

if TYPE_CHECKING:
    import sphinx.application


PROJECT_ROOT_DIR = Path(__file__).parents[2].resolve()
IS_RELEASE_ON_RTD = (
    os.getenv("READTHEDOCS", "False") == "True"
    and os.environ["READTHEDOCS_VERSION_TYPE"] == "tag"
)
if IS_RELEASE_ON_RTD:
    tags: set[str]
    # pylint: disable-next=used-before-assignment
    tags.add("is_release")  # noqa: F821

release = ".".join(version.split(".")[:2])

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
# sys.path.insert(0, os.path.abspath('.'))

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_typehints_description_target = "documented"
todo_include_todos = 1

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

# -- General configuration -----------------------------------------------------

# If your documentation needs a minimal Sphinx version, state it here.
# needs_sphinx = '1.0'

# Add any Sphinx extension module names here, as strings. They can be extensions
# coming with Sphinx (named 'sphinx.ext.*') or your custom ones.
extensions = [
    "pygments_pytest",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.extlinks",
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

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# The suffix of source filenames.
source_suffix = ".rst"

# The encoding of source files.
# source_encoding = 'utf-8-sig'

# The master toctree document.
master_doc = "contents"

# General information about the project.
project = "pytest"
copyright = "2015, holger krekel and pytest-dev team"


# The language for content autogenerated by Sphinx. Refer to documentation
# for a list of supported languages.
# language = None

# There are two options for replacing |today|: either, you set today to some
# non-false value, then it is used:
# today = ''
# Else, today_fmt is used as the format for a strftime call.
# today_fmt = '%B %d, %Y'

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
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


# The reST default role (used for this markup: `text`) to use for all documents.
default_role = "literal"

# If true, '()' will be appended to :func: etc. cross-reference text.
# add_function_parentheses = True

# If true, the current module name will be prepended to all description
# unit titles (such as .. function::).
add_module_names = False

# If true, sectionauthor and moduleauthor directives will be shown in the
# output. They are ignored by default.
# show_authors = False

# A list of ignored prefixes for module index sorting.
# modindex_common_prefix = []

# A list of regular expressions that match URIs that should not be checked when
# doing a linkcheck.
linkcheck_ignore = [
    "https://blogs.msdn.microsoft.com/bharry/2017/06/28/testing-in-a-cloud-delivery-cadence/",
    "http://pythontesting.net/framework/pytest-introduction/",
    r"https://github.com/pytest-dev/pytest/issues/\d+",
    r"https://github.com/pytest-dev/pytest/pull/\d+",
]

# The number of worker threads to use when checking links (default=5).
linkcheck_workers = 5


extlinks = {
    "bpo": ("https://bugs.python.org/issue%s", "bpo-%s"),
}


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


# -- Options for HTML output ---------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
html_theme = "furo"

# Theme options are theme-specific and customize the look and feel of a theme
# further.  For a list of options available for each theme, see the
# documentation.
# html_theme_options = {"index_logo": None}

# Add any paths that contain custom themes here, relative to this directory.
# html_theme_path = []

# The name for this set of Sphinx documents.  If None, it defaults to
# "<project> v<release> documentation".
html_title = "pytest documentation"

# A shorter title for the navigation bar.  Default is the same as html_title.
html_short_title = f"pytest-{release}"

# The name of an image file (relative to this directory) to place at the top
# of the sidebar.
html_logo = "img/pytest_logo_curves.svg"

# The name of an image file (within the static path) to use as favicon of the
# docs.  This file should be a Windows icon file (.ico) being 16x16 or 32x32
# pixels large.
html_favicon = "img/favicon.png"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
# html_static_path = ['_static']

# If not '', a 'Last updated on:' timestamp is inserted at every page bottom,
# using the given strftime format.
# html_last_updated_fmt = '%b %d, %Y'

# If true, SmartyPants will be used to convert quotes and dashes to
# typographically correct entities.
# html_use_smartypants = True

# Custom sidebar templates, maps document names to template names.
# html_sidebars = {}
# html_sidebars = {'index': 'indexsidebar.html'}

html_sidebars = {
    "index": [
        "sidebar/brand.html",
        "sidebar/search.html",
        "sidebar/scroll-start.html",
        "sidebarintro.html",
        "globaltoc.html",
        "links.html",
        "sidebar/scroll-end.html",
        "style.html",
    ],
    "**": [
        "sidebar/brand.html",
        "sidebar/search.html",
        "sidebar/scroll-start.html",
        "globaltoc.html",
        "relations.html",
        "links.html",
        "sidebar/scroll-end.html",
        "style.html",
    ],
}

# Additional templates that should be rendered to pages, maps page names to
# template names.
# html_additional_pages = {}
# html_additional_pages = {'index': 'index.html'}


# If false, no module index is generated.
html_domain_indices = True

# If false, no index is generated.
html_use_index = False

# If true, the index is split into individual pages for each letter.
# html_split_index = False

# If true, links to the reST sources are added to the pages.
html_show_sourcelink = False

# If true, "Created using Sphinx" is shown in the HTML footer. Default is True.
# html_show_sphinx = True

# If true, "(C) Copyright ..." is shown in the HTML footer. Default is True.
# html_show_copyright = True

# If true, an OpenSearch description file will be output, and all pages will
# contain a <link> tag referring to it.  The value of this option must be the
# base URL from which the finished HTML is served.
# html_use_opensearch = ''

# This is the file name suffix for HTML files (e.g. ".xhtml").
# html_file_suffix = None

# Output file base name for HTML help builder.
htmlhelp_basename = "pytestdoc"

# The base URL which points to the root of the HTML documentation. It is used
# to indicate the location of document using the canonical link relation (#12363).
html_baseurl = "https://docs.pytest.org/en/stable/"

# -- Options for LaTeX output --------------------------------------------------

# The paper size ('letter' or 'a4').
# latex_paper_size = 'letter'

# The font size ('10pt', '11pt' or '12pt').
# latex_font_size = '10pt'

# Grouping the document tree into LaTeX files. List of tuples
# (source start file, target name, title, author, documentclass [howto/manual]).
latex_documents = [
    (
        "contents",
        "pytest.tex",
        "pytest Documentation",
        "holger krekel, trainer and consultant, https://merlinux.eu/",
        "manual",
    )
]

# For "manual" documents, if this is true, then toplevel headings are parts,
# not chapters.
# latex_use_parts = False

# If true, show page references after internal links.
# latex_show_pagerefs = False

# If true, show URL addresses after external links.
# latex_show_urls = False

# Additional stuff for the LaTeX preamble.
# latex_preamble = ''

# Documents to append as an appendix to all manuals.
# latex_appendices = []

# If false, no module index is generated.
latex_domain_indices = False

# -- Options for manual page output --------------------------------------------

# One entry per manual page. List of tuples
# (source start file, name, description, authors, manual section).
man_pages = [
    ("how-to/usage", "pytest", "pytest usage", ["holger krekel at merlinux eu"], 1)
]


# -- Options for Epub output ---------------------------------------------------

# Bibliographic Dublin Core info.
epub_title = "pytest"
epub_author = "holger krekel at merlinux eu"
epub_publisher = "holger krekel at merlinux eu"
epub_copyright = "2013, holger krekel et alii"

# The language of the text. It defaults to the language option
# or en if the language is not set.
# epub_language = ''

# The scheme of the identifier. Typical schemes are ISBN or URL.
# epub_scheme = ''

# The unique identifier of the text. This can be an ISBN number
# or the project homepage.
# epub_identifier = ''

# A unique identification for the text.
# epub_uid = ''

# HTML files that should be inserted before the pages created by sphinx.
# The format is a list of tuples containing the path and title.
# epub_pre_files = []

# HTML files that should be inserted after the pages created by sphinx.
# The format is a list of tuples containing the path and title.
# epub_post_files = []

# A list of files that should not be packed into the epub file.
# epub_exclude_files = []

# The depth of the table of contents in toc.ncx.
# epub_tocdepth = 3

# Allow duplicate toc entries.
# epub_tocdup = True


# -- Options for texinfo output ------------------------------------------------

texinfo_documents = [
    (
        master_doc,
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

# -- Options for towncrier_draft extension -----------------------------------

towncrier_draft_autoversion_mode = "draft"  # or: 'sphinx-version', 'sphinx-release'
towncrier_draft_include_empty = True
towncrier_draft_working_directory = PROJECT_ROOT_DIR
towncrier_draft_config_path = "pyproject.toml"  # relative to cwd


# -- Options for sphinx_issues extension -----------------------------------

issues_github_path = "pytest-dev/pytest"


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

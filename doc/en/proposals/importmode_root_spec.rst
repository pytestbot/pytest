:orphan:

===================================
PROPOSAL: Parametrize with fixtures
===================================

.. warning::

    This document outlines a proposal around creating a new import mode that supports pep420 and
    supports the dissonance between installed and editable installed


Problem
=======

test module discovery in pytest currently is is pre-pep40 and create pains for users
by adding anything things to sys.path and creating easy conflicts in sys.modules

Additionally this will not solve the dissonance between doctest tests vs installed modules

The importlib mode in contrast breaks all kinds of expectations by importing test modules in a magical way
that breaks relative imports and leaves them out of ``sys.modules``


Proposed Solution
=================

A new import mode take a definition of import roots.

a import root is either a folder in the worktree that will no be installed,
or a folder that will be installed either normally or editable.

In any case pytest will collect the file tree in the working directory.

If normal install us used and the content of the imported file differs from the working directory,
pytest will fail collection with a "ContentMissmatch" error.

When import roots are specified, nothing will be added to sys.path
instead installable content will rely in installs, and local content will create namespace packages that limit the path of subitems

everything else will be discovered using normal imports




.. code-block::

  # contents of pytest.ini
  [pytest]
  import-roots =
    testing/ local
    src/mypkg installed as mypkg
    # alterntively
    src/ installed

## todo testcases

* handle install vs editable install

    * when installed - imported name will differe from name in soruce tree
      fail collection when content differs
      (optional strictly check all files/missing files)
    * when installed editable - import using the right name

   * when not installed:

* testing folders
  * if no __init__.py -> create fake namespace, use it for importing
  * if __init__.py -> create normal package, dont change sys.path

* ensure each folder gets a collection package

import os.path

import _pytest.python.nodes

mydir = os.path.dirname(__file__)


def pytest_runtest_setup(item):
    if isinstance(item, _pytest.python.nodes.Function):
        if not item.fspath.relto(mydir):
            return
        mod = item.getparent(_pytest.python.nodes.Module).obj
        if hasattr(mod, "hello"):
            print(f"mod.hello {mod.hello!r}")

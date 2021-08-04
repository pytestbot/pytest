import pytest


class MyFile(pytest.File):
    def collect(self):
        return [MyItem.from_parent(name="hello", parent=self)]


def pytest_collect_file(fspath, parent):
    return MyFile.from_parent(path=fspath, parent=parent)


class MyItem(pytest.Item):
    pass

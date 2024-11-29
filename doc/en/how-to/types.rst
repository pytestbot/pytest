.. _types:

Enhancing Type Annotations with Pytest
======================================

This page assumes the reader is familiar with Python's typing system and its advantages.
For more information, refer to `Python's Typing Documentation <https://docs.python.org/3/library/typing.html>`_.

Why Type Tests?
---------------

Typing tests in pytest provide unique advantages distinct from typing production code. Typed tests emphasize robustness in edge cases and diverse datasets.
Type annotations provide an additional layer of validation, reducing the risk of runtime failures.

- **Test Clarity:** Clearly defines expected inputs and outputs, improving readability, especially in complex or parameterized tests.

- **Type Safety:** Helps catch mistakes in test data early, reducing runtime errors.

- **Refactoring Support:** Serves as in-code documentation, clarifying data expectations and minimizing errors during test suite modifications.

These benefits make typed tests a powerful tool for maintaining clarity, consistency, and safety throughout the testing process.

Typing Test Functions
---------------------
By adding type annotations to test functions, tests are easier to read and understand.
This is particularly helpful when developers need to refactor code or revisit tests after some time.

For example:

.. code-block:: python

    import pytest


    def add(a: int, b: int) -> int:
        return a + b


    def test_add() -> None:
        result = add(2, 3)
        assert result == 5

Here, `test_add` is annotated with `-> None`, as it does not return a value.
While `-> None` typing may seem unnecessary, it ensures type checkers validate the function and helps identifying potential issues during refactoring.


Typing Fixtures
---------------
Fixtures in pytest helps set up data or provides resources needed for tests.
Adding type annotations to fixtures makes it clear what data they return, which helps with debugging and readability.

* Basic Fixture Typing

.. code-block:: python

    import pytest


    @pytest.fixture
    def sample_fixture() -> int:
        return 38


    def test_sample_fixture(sample_fixture: int) -> None:
        assert sample_fixture == 38

Here, `sample_fixture()` is typed to return an `int`. This ensures consistency and helps identify mismatch types during refactoring.


* Typing Fixtures with Lists and Dictionaries
This example shows how to use List and Dict types in pytest.

.. code-block:: python

    from typing import List, Dict
    import pytest


    @pytest.fixture
    def sample_list() -> List[int]:
        return [5, 10, 15]


    def test_sample_list(sample_list: List[int]) -> None:
        assert sum(sample_list) == 30


    @pytest.fixture
    def sample_dict() -> Dict[str, int]:
        return {"a": 50, "b": 100}


    def test_sample_dict(sample_dict: Dict[str, int]) -> None:
        assert sample_dict["a"] == 50

Annotating fixtures with types like List[int] and Dict[str, int] ensures data consistency and helps prevent runtime errors when performing operations.
This ensures that only `int` values are allowed in the list and that `str` keys map to `int` values in the dictionary, helping avoid type-related issues.

Typing Parameterized Tests
--------------------------
With `@pytest.mark.parametrize`, adding typing annotations to the input parameters reinforce type safety and reduce errors with multiple data sets.

For example, you are testing if adding 1 to `input_value` results in `expected_output` for each set of arguments.

.. code-block:: python

    import pytest


    @pytest.mark.parametrize("input_value, expected_output", [(1, 2), (5, 6), (10, 11)])
    def test_increment(input_value: int, expected_output: int) -> None:
        assert input_value + 1 == expected_output

Here, typing clarifies that both `input_value` and `expected_output` are expected as integers, promoting consistency.
While parameterized tests can involve varied data types and that annotations simplify maintenance when datasets grow.


Typing for Monkeypatching
-------------------------
Monkeypatching modifies functions or environment variables during runtime.
Adding typing, such as `monkeypatch: pytest.MonkeyPatch`, clarifies the expected patching behaviour and reduces the risk of errors.

* Example of Typing Monkeypatching Environment Variables

This example is based on the pytest documentation for `Monkeypatching <https://github.com/pytest-dev/pytest/blob/main/doc/en/how-to/monkeypatch.rst>`_, with the addition of typing annotations.

.. code-block:: python

    # contents of our original code file e.g. code.py
    import pytest
    import os
    from typing import Optional


    def get_os_user_lower() -> str:
        """Simple retrieval function. Returns lowercase USER or raises OSError."""
        username: Optional[str] = os.getenv("USER")

        if username is None:
            raise OSError("USER environment is not set.")

        return username.lower()


    # contents of our test file e.g. test_code.py
    @pytest.fixture
    def mock_env_user(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USER", "TestingUser")


    @pytest.fixture
    def mock_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("USER", raising=False)


    def test_upper_to_lower(mock_env_user: None) -> None:
        assert get_os_user_lower() == "testinguser"


    def test_raise_exception(mock_env_missing: None) -> None:
        with pytest.raises(OSError):
            _ = get_os_user_lower()

Here:

- **username: Optional[str]:** Indicates the variable `username` may either be a string or `None`.
- **get_os_user_lower() -> str:** Specifies this function will return a string, providing explicit return value type.
- **monkeypatch fixture is typed as pytest.MonkeyPatch:** Shows that it will provide an object for patching environment variables during the test. This clarifies the intended use of the fixture and helps developers to use it correctly.
- **Fixture return ->  None, like mock_env_user:** Specifies they do not return any value, but instead modify the test environment.

Typing annotations can also be extended to `monkeypatch` usage in pytest for class methods, instance attributes, or standalone functions.
This enhances type safety and clarity when patching the test environment.


Typing Temporary Directories and Paths
--------------------------------------
Temporary directories and paths are commonly used in pytest to create isolated environments for testing file and directory operations.
The `tmp_path` and `tmpdir` fixtures provide these capabilities.
Adding typing annotations enhances clarity about the types of objects these fixtures return, which is particularly useful when performing file operations.

Below examples are based on the pytest documentation for `Temporary Directories and Files in tests <https://github.com/pytest-dev/pytest/blob/main/doc/en/how-to/tmp_path.rst>`_, with the addition of typing annotations.

* Typing with `tmp_path` for File Creation

.. code-block:: python

    import pytest
    from pathlib import Path

    # content of test_tmp_path.py
    CONTENT = "content"


    def test_create_file(tmp_path: Path) -> None:
        d = tmp_path / "sub"
        d.mkdir()
        p = d / "hello.txt"
        p.write_text(CONTENT, encoding="utf-8")
        assert p.read_text(encoding="utf-8") == CONTENT
        assert len(list(tmp_path.iterdir())) == 1

Typing `tmp_path: Path` explicitly defines it as a Path object, improving code readability and catching type issues early.

* Typing with `tmp_path_factory` fixture for creating temporary files during a session

.. code-block:: python

    # contents of conftest.py
    import pytest
    from pathlib import Path


    @pytest.fixture(scope="session")
    def image_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
        img = compute_expensive_image()
        fn: Path = tmp_path_factory.mktemp("data") / "img.png"
        img.save(fn)
        return fn


    # contents of test_image.py
    def test_histogram(image_file: Path) -> None:
        img = load_image(image_file)
        # compute and test histogram

Here:

- **tmp_path_factory: pytest.TempPathFactory:** Indicates that `tmp_path_factory` is an instance of pytest’s `TempPathFactory`, responsible for creating temporary directories and paths during testing.
- **fn: Path:** Identifies that `fn` is a `Path` object, emphasizing its role as a file path and clarifying the expected file operations.
- **Return type -> Path:** Specifies the fixture returns a `Path` object, clarifying its expected structure.
- **image_file: Path:** Defines `image_file` as a Path object, ensuring compatibility with `load_image`.

Conclusion
----------
Incorporating typing into pytest tests enhances **clarity**, improves **debugging** and **maintenance**, and ensures **type safety**.
These practices lead to a **robust**, **readable**, and **easily maintainable** test suite that is better equipped to handle future changes with minimal risk of errors.

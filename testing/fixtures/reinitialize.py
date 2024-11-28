from __future__ import annotations

import functools

import pytest


NUM_EXECUTIONS = 5
cur_request = None
executions = None


# Should logically be scope="item", but we want the stored request to be
# function-scoped. Avoids signature-rewriting ugliness in inner_runner
# in order to access the request.
@pytest.fixture(scope="function", autouse=True)
def fetch_request(request):
    global cur_request
    cur_request = request
    yield
    cur_request = None


@pytest.fixture(scope="item", autouse=True)
def reset_executions():
    global executions
    executions = 0
    yield
    assert executions == NUM_EXECUTIONS


def update(kwargs):
    global executions
    if executions > 0:
        updated = cur_request._reset_function_scoped_fixtures()
        kwargs |= {k: v for k, v in updated.items() if k in kwargs}
    executions += 1


def reset_between_executions(fn):
    @functools.wraps(fn)
    def wrapped(**kwargs):
        update(kwargs)
        fn(**kwargs)

    return wrapped


def run_many_times(fn):
    @functools.wraps(fn)
    def wrapped(**kwargs):
        for i in range(NUM_EXECUTIONS):
            fn(**kwargs)

    return wrapped


num_func = num_item = num_param = num_ex = num_test = 0
params_seen = set()


@pytest.fixture(scope="function")
def fixture_func():
    """Once per example"""
    global num_func
    num_func += 1
    print("->func")
    return num_func


@pytest.fixture(scope="item")
def fixture_item():
    """Once per test item"""
    global num_item
    num_item += 1
    print("->item")
    return num_item


@pytest.fixture(scope="function")
def fixture_func_item(fixture_func, fixture_item):
    """mixed-scope transitivity"""
    return (fixture_func, fixture_item)


@pytest.fixture(scope="function")
def fixture_test(fixture_test):
    """Overrides conftest fixture of same name"""
    global num_test
    num_test += 1
    print("->test")
    return (fixture_test, num_test)


@pytest.fixture(scope="function", params=range(4))
def fixture_param(request):
    """parameterized, per-example"""
    global num_param
    print("->param")
    num_param += 1
    return (num_param, request.param)


@run_many_times
@reset_between_executions
def test_resetting_function_scoped_fixtures(
    fixture_func_item, fixture_test, fixture_param, fixture_test_2
):
    global num_ex
    num_ex += 1

    # All these should be used only by this test, to avoid counter headaches
    print(
        f"{num_ex=} {fixture_func_item=} {fixture_test=} {fixture_param=} {fixture_test_2=}"
    )

    # 1. fixture_test is a tuple (num_conftest_calls, num_module_calls), and
    #    both are function-scoped so should be called per-example
    assert fixture_test == (num_ex, num_ex)
    # 2. fixture_test_2 should have picked up the module-level fixture_test, not
    #    the conftest-level one
    assert fixture_test_2 == fixture_test
    # 3. check that the parameterized fixture was also re-executed for each example
    assert fixture_param[0] == num_ex
    # 4. number of calls to _func should be the same as number of examples, while
    #    number of calls to _item should be the number of parametrized items seen
    params_seen.add(fixture_param[1])
    assert fixture_func_item == (num_ex, len(params_seen))
    #
    print("---------")


@pytest.fixture(scope="function")
def fixt_1(fixt_1, fixt_2):
    return f"f1_m({fixt_1}, {fixt_2})"


@pytest.fixture(scope="function")
def fixt_2(fixt_1, fixt_2):
    return f"f2_m({fixt_1}, {fixt_2})"


@pytest.fixture(scope="function")
def fixt_3(fixt_1, fixt_3):
    return f"f3_m({fixt_1}, {fixt_3})"


@run_many_times
@reset_between_executions
def test_complex_fixture_dependency(fixt_1, fixt_2, fixt_3):
    # Notice how fixt_2 and fixt_3 resolve different values for fixt_1
    # - module.fixt_2 receives conftest.fixt_1
    # - module.fixt_3 receives module.fixt_1
    # However, what we want to ensure here is that the result stays the same
    # after fixture reset, not why it is what it is in the first place.
    assert fixt_1 == "f1_m(f1_c, f2_m(f1_c, f2_c(f1_c)))"
    assert fixt_2 == "f2_m(f1_c, f2_c(f1_c))"
    assert (
        fixt_3
        == "f3_m(f1_m(f1_c, f2_m(f1_c, f2_c(f1_c))), f3_c(f1_m(f1_c, f2_m(f1_c, f2_c(f1_c)))))"
    )


@run_many_times
@reset_between_executions
def test_try_all_known_fixtures(
    cache,
    capsys,
    doctest_namespace,
    pytestconfig,
    record_property,
    record_xml_attribute,
    record_testsuite_property,
    testdir,
    tmpdir_factory,
    tmpdir,
    caplog,
    monkeypatch,
    linecomp,
    LineMatcher,
    pytester,
    recwarn,
    tmp_path_factory,
    tmp_path,
):
    pass

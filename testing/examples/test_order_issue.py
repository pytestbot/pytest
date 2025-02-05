from _pytest.pytester import Pytester


def test_order(pytester: Pytester) -> None:
    pytester.copy_example("order_issue.py")
    rep = pytester.runpytest("order_issue.py", "--setup-show")
    rep.assert_outcomes(passed=2)

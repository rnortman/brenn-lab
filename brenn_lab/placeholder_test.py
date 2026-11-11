from brenn_lab.placeholder import mean_of


def test_mean_of() -> None:
    assert mean_of([1.0, 2.0, 3.0]) == 2.0


def test_mean_of_tuple() -> None:
    assert mean_of((-1.5, 1.5)) == 0.0

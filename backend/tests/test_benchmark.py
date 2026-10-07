from reel_lab.benchmark import _hit_rate, _nearest_offsets


def test_nearest_offsets_keep_direction() -> None:
    offsets = _nearest_offsets([1.1, 2.9, 5.0], [1.0, 3.0])

    assert offsets == [0.1, -0.1, 2.0]


def test_hit_rate_ignores_missing_markers() -> None:
    assert _hit_rate([0.05, -0.2, None], 0.12) == 0.5
    assert _hit_rate([None], 0.12) == 0.0

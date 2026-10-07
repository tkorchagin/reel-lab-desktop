from reel_lab.music_audit import audit_window_starts


def test_audit_windows_cover_track_edges_and_highlight() -> None:
    starts = audit_window_starts(198.5, 15.0, 30.0, 27.8)

    assert starts[0] == 0.0
    assert 27.8 in starts
    assert 30.0 in starts
    assert starts[-1] == 183.5
    assert starts == sorted(set(starts))

import random
from pathlib import Path
from statistics import mean

from reel_lab.models import AudioMap, AudioSection, EnergyPoint, MediaCandidate, TimelineItem
from reel_lab.timeline import (
    ORDERED_STYLES,
    _available_video_windows,
    _build_music_grid,
    _choose_available_window,
    _coalesce_adjacent_source_items,
    _coherent_random_order,
    _rhythmic_clip_boundaries,
    generate_ordered_variants,
    generate_variants,
)


def test_random_order_reduces_harsh_visual_jumps() -> None:
    brightness = [0.12, 0.88, 0.22, 0.78, 0.32, 0.68]
    paths = [f"/tmp/visual-{index}.mp4" for index in range(len(brightness))]
    grouped = {
        path: [
            MediaCandidate(
                id=f"visual-{index}",
                path=path,
                kind="video",
                start=0.0,
                end=4.0,
                width=1080,
                height=1920,
                quality=0.8,
                intensity=0.55,
                sharpness=0.8,
                brightness=value,
            )
        ]
        for index, (path, value) in enumerate(zip(paths, brightness, strict=True))
    }

    ordered = _coherent_random_order(
        paths,
        grouped,
        paths[0],
        ORDERED_STYLES[2],
        random.Random(17),
    )
    by_path = dict(zip(paths, brightness, strict=True))
    original_jump = mean(
        abs(left - right) for left, right in zip(brightness, brightness[1:], strict=False)
    )
    ordered_values = [by_path[path] for path in ordered]
    ordered_jump = mean(
        abs(left - right)
        for left, right in zip(ordered_values, ordered_values[1:], strict=False)
    )

    assert set(ordered) == set(paths)
    assert ordered[0] == paths[0]
    assert ordered_jump < original_jump * 0.5


def test_detected_beat_phase_is_not_mixed_with_a_global_bpm_grid() -> None:
    detected = [0.0004, 0.6506, 1.2775, 1.9044, 2.5314, 3.1119, 3.7388, 4.389]
    audio_map = AudioMap(
        source_path="track.mp3",
        normalized_path="track.mp3",
        analyzer="test",
        duration=4.5,
        bpm=95.7,
        beats=detected,
        downbeats=[0.0004, 2.5314],
        onsets=[0.3255, 1.115, 3.4602],
        sections=[AudioSection(0.0, 4.5, "body", 0.6)],
        energy=[EnergyPoint(0.0, 0.5), EnergyPoint(4.5, 0.5)],
        edit_points=[0.0, 4.5],
    )

    grid = _build_music_grid(audio_map)

    assert grid.beats == detected
    assert 0.6273 not in grid.beats
    assert all(
        min(abs(onset - anchor) for anchor in [*grid.beats, *grid.half_beats]) < 0.0001
        for onset in grid.onsets
    )


def test_ordered_cadence_starts_on_phrase_and_changes_in_musical_units() -> None:
    beats = [
        0.2431,
        0.8468,
        1.4273,
        1.9846,
        2.5883,
        3.192,
        3.7725,
        4.3762,
        4.9567,
        5.5605,
        6.1642,
        6.7447,
        7.3252,
        7.9289,
        8.5326,
        9.1363,
        9.74,
        10.2973,
        10.901,
        11.4815,
        12.062,
        12.6658,
        13.2695,
        13.85,
        14.4537,
    ]
    audio_map = AudioMap(
        source_path="track.mp3",
        normalized_path="track.wav",
        analyzer="test",
        duration=15.0,
        bpm=99.38,
        beats=beats,
        downbeats=[1.9846, 4.3762, 6.7447, 9.1363, 11.4815, 13.85],
        onsets=beats,
        sections=[AudioSection(0.0, 15.0, "body", 0.65)],
        energy=[EnergyPoint(0.0, 0.55), EnergyPoint(15.0, 0.78)],
        edit_points=[0.0, 15.0],
    )

    boundaries = {
        style.name: _rhythmic_clip_boundaries(
            audio_map,
            style,
            min_slot_duration=0.6,
            max_slots=25,
        )
        for style in ORDERED_STYLES
    }

    for values in boundaries.values():
        assert values[1] == 1.9846
        assert all(min(abs(cut - beat) for beat in beats) <= 0.0001 for cut in values[1:-1])
    rhythm_durations = [
        right - left
        for left, right in zip(boundaries["Rhythm"], boundaries["Rhythm"][1:], strict=False)
    ]
    ramp_durations = [
        right - left
        for left, right in zip(boundaries["Ramp"], boundaries["Ramp"][1:], strict=False)
    ]
    drive_durations = [
        right - left
        for left, right in zip(boundaries["Drive"], boundaries["Drive"][1:], strict=False)
    ]
    kino_durations = [
        right - left
        for left, right in zip(boundaries["Cinema"], boundaries["Cinema"][1:], strict=False)
    ]
    assert len(rhythm_durations) == 12
    assert max(rhythm_durations[1:-1]) - min(rhythm_durations[1:-1]) < 0.06
    assert mean(ramp_durations[:4]) > mean(ramp_durations[-4:]) * 1.8
    drive_short = [duration < 0.75 for duration in drive_durations[1:]]
    assert "111" not in "".join("1" if value else "0" for value in drive_short)
    assert all(
        long_shot > short_shot * 2.7
        for long_shot, short_shot in zip(kino_durations[1::2], kino_durations[2::2], strict=False)
    )


def test_low_confidence_grid_disables_half_beats() -> None:
    audio_map = AudioMap(
        source_path="track.mp3",
        normalized_path="track.mp3",
        analyzer="test",
        duration=5.0,
        bpm=120.0,
        beats=[0.25 + index * 0.5 for index in range(10)],
        downbeats=[0.25, 2.25, 4.25],
        onsets=[0.25 + index * 0.5 for index in range(10)],
        sections=[AudioSection(0.0, 5.0, "body", 0.5)],
        energy=[EnergyPoint(0.0, 0.5), EnergyPoint(5.0, 0.5)],
        edit_points=[0.0, 5.0],
        beat_confidence=0.3,
    )

    grid = _build_music_grid(audio_map)

    assert grid.half_beats == []


def test_all_ordered_styles_use_musical_boundaries() -> None:
    beats = [
        0.0004,
        0.6506,
        1.2775,
        1.9044,
        2.5314,
        3.1119,
        3.7388,
        4.389,
        5.0624,
        5.8286,
        6.4556,
        7.1057,
        7.8952,
        8.5221,
        9.1723,
        9.7992,
        10.4262,
        11.0531,
        11.68,
        12.307,
        12.9339,
        13.5841,
        14.211,
        14.838,
    ]
    audio_map = AudioMap(
        source_path="track.mp3",
        normalized_path="track.mp3",
        analyzer="test",
        duration=15.0,
        bpm=95.7,
        beats=beats,
        downbeats=[0.0004, 2.5314, 5.0624, 7.8952, 10.4262, 12.9339],
        onsets=[index * 0.31 for index in range(49)],
        sections=[
            AudioSection(0.0, 14.211, "intro", 0.25),
            AudioSection(14.211, 15.0, "body", 0.7),
        ],
        energy=[EnergyPoint(0.0, 0.25), EnergyPoint(15.0, 0.7)],
        edit_points=[0.0, 5.0624, 10.4262, 14.211],
    )
    paths = [Path(f"/tmp/musical-source-{index}.mp4") for index in range(12)]
    candidates = [
        MediaCandidate(
            id=f"musical-{index}",
            path=str(path),
            kind="video",
            start=0.0,
            end=20.0,
            width=1080,
            height=1920,
            quality=0.8,
            intensity=0.6,
            sharpness=0.8,
            brightness=0.5,
        )
        for index, path in enumerate(paths)
    ]

    variants = generate_ordered_variants(
        audio_map,
        candidates,
        paths,
        count=4,
        clip_reuse_mode="single",
    )
    grid = _build_music_grid(audio_map)
    musical = [*grid.beats, *grid.half_beats, *grid.onsets, *grid.downbeats, *grid.sections]

    for variant in variants:
        cuts = [item.timeline_start for item in variant.items[1:]]
        assert all(min(abs(cut - anchor) for anchor in musical) < 0.0001 for cut in cuts)
        if variant.name == "Rhythm":
            assert all(min(abs(cut - beat) for beat in grid.beats) < 0.0001 for cut in cuts)
        if variant.name == "Rhythm":
            expected = [grid.beats[index] for index in range(2, len(grid.beats), 2)]
            assert cuts == expected
    assert {variant.name for variant in variants} == {"Rhythm", "Drive", "Ramp", "Cinema"}


def test_dense_peak_controls_exact_source_window() -> None:
    start, end = _choose_available_window(
        [(2.0, 6.0)],
        2.0,
        random.Random(7),
        preferred_start=5.16,
    )

    assert start == 5.16
    assert end == 7.16


def test_generate_ten_complete_variants() -> None:
    audio_map = AudioMap(
        source_path="track.mp4",
        normalized_path="track.wav",
        analyzer="test",
        duration=12.0,
        bpm=120.0,
        beats=[index * 0.5 for index in range(25)],
        downbeats=[index * 2.0 for index in range(7)],
        onsets=[],
        sections=[
            AudioSection(0.0, 4.0, "intro", 0.3),
            AudioSection(4.0, 10.0, "high_energy", 0.8),
            AudioSection(10.0, 12.0, "outro", 0.4),
        ],
        energy=[EnergyPoint(index * 0.5, min(1.0, index / 20)) for index in range(25)],
        edit_points=[index * 2.0 for index in range(7)],
    )
    candidates = [
        MediaCandidate(
            id=f"clip-{index}",
            path=f"video-{index % 3}.mp4",
            kind="video",
            start=0.0,
            end=8.0,
            width=720,
            height=1280,
            quality=0.65 + index * 0.02,
            intensity=index / 9,
            sharpness=0.8,
            brightness=0.5,
        )
        for index in range(10)
    ]

    variants = generate_variants(audio_map, candidates, count=10, seed=42)

    assert len(variants) == 10
    assert len({variant.name for variant in variants}) == 10
    for variant in variants:
        assert variant.items[0].timeline_start == 0.0
        assert variant.items[-1].timeline_end == audio_map.duration
        for previous, current in zip(variant.items, variant.items[1:], strict=False):
            assert previous.timeline_end == current.timeline_start
        assert 0.0 <= variant.score <= 1.0


def test_generation_is_deterministic() -> None:
    audio_map = AudioMap(
        source_path="track.mp4",
        normalized_path="track.wav",
        analyzer="test",
        duration=4.0,
        bpm=120.0,
        beats=[index * 0.5 for index in range(9)],
        downbeats=[0.0, 2.0, 4.0],
        onsets=[],
        sections=[AudioSection(0.0, 4.0, "body", 0.5)],
        energy=[EnergyPoint(0.0, 0.5), EnergyPoint(4.0, 0.5)],
        edit_points=[0.0, 2.0, 4.0],
    )
    candidate = MediaCandidate(
        id="only",
        path="video.mp4",
        kind="video",
        start=0.0,
        end=10.0,
        width=720,
        height=1280,
        quality=0.8,
        intensity=0.5,
        sharpness=0.8,
        brightness=0.5,
    )

    first = generate_variants(audio_map, [candidate], count=1, seed=7)[0].to_dict()
    second = generate_variants(audio_map, [candidate], count=1, seed=7)[0].to_dict()

    assert first == second


def test_ordered_variants_keep_every_source_in_order() -> None:
    audio_map = AudioMap(
        source_path="track.mp4",
        normalized_path="track.wav",
        analyzer="test",
        duration=30.0,
        bpm=120.0,
        beats=[index * 0.5 for index in range(61)],
        downbeats=[index * 2.0 for index in range(16)],
        onsets=[index * 0.25 for index in range(121)],
        sections=[AudioSection(0.0, 30.0, "body", 0.6)],
        energy=[EnergyPoint(0.0, 0.5), EnergyPoint(30.0, 0.8)],
        edit_points=[0.0, 30.0],
    )
    paths = [Path(f"/tmp/source-{index}.mp4") for index in range(58)]
    candidates = [
        MediaCandidate(
            id=f"candidate-{index}",
            path=str(path),
            kind="video",
            start=0.0,
            end=4.0,
            width=720,
            height=1280,
            quality=0.8,
            intensity=0.6,
            sharpness=0.8,
            brightness=0.5,
        )
        for index, path in enumerate(paths)
    ]

    variants = generate_ordered_variants(
        audio_map,
        candidates,
        paths,
        count=4,
        clip_reuse_mode="single",
    )

    assert len(variants) == 4
    for variant in variants:
        assert [item.path for item in variant.items] == [str(path.resolve()) for path in paths]
        assert len(variant.items) == 58
        assert variant.items[0].timeline_start == 0.0
        assert variant.items[-1].timeline_end == 30.0
        assert all(item.duration >= 0.22 for item in variant.items)

    selective = generate_ordered_variants(
        audio_map,
        candidates,
        paths,
        count=4,
        include_all=False,
        clip_reuse_mode="single",
    )
    source_indexes = {str(path.resolve()): index for index, path in enumerate(paths)}
    for variant in selective:
        indexes = [source_indexes[item.path] for item in variant.items]
        assert 26 <= len(indexes) <= 40
        assert indexes == sorted(indexes)

    random_order = generate_ordered_variants(
        audio_map,
        candidates,
        paths,
        count=4,
        media_order="random",
        clip_reuse_mode="single",
    )
    expected = {str(path.resolve()) for path in paths}
    assert any(
        [item.path for item in variant.items] != [str(path.resolve()) for path in paths]
        for variant in random_order
    )
    assert len({tuple(item.path for item in variant.items) for variant in random_order}) > 1
    for variant in random_order:
        assert {item.path for item in variant.items} == expected
        assert len(variant.items) == 58


def test_random_order_starts_with_best_hook_candidate() -> None:
    audio_map = AudioMap(
        source_path="track.mp4",
        normalized_path="track.wav",
        analyzer="test",
        duration=8.0,
        bpm=120.0,
        beats=[index * 0.5 for index in range(17)],
        downbeats=[0.0, 2.0, 4.0, 6.0, 8.0],
        onsets=[],
        sections=[AudioSection(0.0, 8.0, "body", 0.7)],
        energy=[EnergyPoint(0.0, 0.7), EnergyPoint(8.0, 0.7)],
        edit_points=[0.0, 8.0],
    )
    paths = [Path(f"/tmp/hook-source-{index}.mp4") for index in range(6)]
    candidates = [
        MediaCandidate(
            id=f"candidate-{index}",
            path=str(path),
            kind="video",
            start=0.0,
            end=4.0,
            width=720,
            height=1280,
            quality=0.45,
            intensity=0.35,
            sharpness=0.45,
            brightness=0.32,
        )
        for index, path in enumerate(paths)
    ]
    candidates[4] = MediaCandidate(
        id="hero",
        path=str(paths[4]),
        kind="video",
        start=1.0,
        end=5.0,
        width=1080,
        height=1920,
        quality=0.98,
        intensity=0.72,
        sharpness=0.97,
        brightness=0.56,
    )

    variants = generate_ordered_variants(
        audio_map,
        candidates,
        paths,
        count=4,
        media_order="random",
        seed=99,
    )

    assert {variant.items[0].candidate_id for variant in variants} == {"hero"}
    assert {variant.items[0].path for variant in variants} == {str(paths[4].resolve())}


def test_random_order_seed_rerolls_first_hook_from_top_pool() -> None:
    audio_map = AudioMap(
        source_path="track.mp4",
        normalized_path="track.wav",
        analyzer="test",
        duration=10.0,
        bpm=120.0,
        beats=[index * 0.5 for index in range(21)],
        downbeats=[index * 2.0 for index in range(6)],
        onsets=[],
        sections=[AudioSection(0.0, 10.0, "body", 0.7)],
        energy=[EnergyPoint(0.0, 0.7), EnergyPoint(10.0, 0.7)],
        edit_points=[0.0, 10.0],
    )
    paths = [Path(f"/tmp/reroll-hook-{index}.mp4") for index in range(6)]
    candidates = [
        MediaCandidate(
            id=f"hook-{index}",
            path=str(path),
            kind="video",
            start=0.0,
            end=4.0,
            width=1080,
            height=1920,
            quality=0.93 - index * 0.015,
            intensity=0.72 - index * 0.015,
            sharpness=0.93 - index * 0.01,
            brightness=0.56,
        )
        for index, path in enumerate(paths)
    ]

    first_items = [
        generate_ordered_variants(
            audio_map,
            candidates,
            paths,
            count=1,
            media_order="random",
            seed=seed,
        )[0].items[0]
        for seed in range(1, 9)
    ]

    assert len({item.candidate_id for item in first_items}) > 1
    assert all(item.score >= 0.9 for item in first_items)


def test_random_order_can_use_multiple_fragments_from_one_video() -> None:
    audio_map = AudioMap(
        source_path="track.mp4",
        normalized_path="track.wav",
        analyzer="test",
        duration=20.0,
        bpm=150.0,
        beats=[index * 0.4 for index in range(51)],
        downbeats=[index * 1.6 for index in range(13)],
        onsets=[index * 0.2 for index in range(101)],
        sections=[AudioSection(0.0, 20.0, "high_energy", 0.82)],
        energy=[EnergyPoint(0.0, 0.8), EnergyPoint(20.0, 0.86)],
        edit_points=[0.0, 20.0],
    )
    paths = [Path(f"/tmp/clip-source-{path_index}.mp4") for path_index in range(4)]
    candidates = [
        MediaCandidate(
            id=f"clip-{path_index}-{candidate_index}",
            path=str(path),
            kind="video",
            start=candidate_index * 1.2,
            end=candidate_index * 1.2 + 3.0,
            width=1080,
            height=1920,
            quality=0.88 - path_index * 0.02,
            intensity=0.7 + candidate_index * 0.04,
            sharpness=0.86,
            brightness=0.52,
        )
        for path_index, path in enumerate(paths)
        for candidate_index in range(4)
    ]

    variant = generate_ordered_variants(
        audio_map,
        candidates,
        paths,
        count=1,
        seed=44,
        media_order="random",
        min_clip_duration=0.4,
    )[0]

    path_counts = {
        path: sum(item.path == path for item in variant.items)
        for path in {str(path.resolve()) for path in paths}
    }
    assert len(variant.items) > len(paths)
    assert set(path_counts) == {str(path.resolve()) for path in paths}
    assert any(count > 1 for count in path_counts.values())
    assert all(item.duration >= 0.39 for item in variant.items)


def test_ordered_modes_group_multiple_fragments_by_source_order() -> None:
    audio_map = AudioMap(
        source_path="track.mp4",
        normalized_path="track.wav",
        analyzer="test",
        duration=18.0,
        bpm=150.0,
        beats=[index * 0.4 for index in range(46)],
        downbeats=[index * 1.6 for index in range(12)],
        onsets=[index * 0.2 for index in range(91)],
        sections=[AudioSection(0.0, 18.0, "high_energy", 0.84)],
        energy=[EnergyPoint(0.0, 0.82), EnergyPoint(18.0, 0.84)],
        edit_points=[0.0, 18.0],
    )
    paths = [Path(f"/tmp/manual-group-source-{path_index}.mp4") for path_index in range(5)]
    candidates = [
        MediaCandidate(
            id=f"manual-group-{path_index}-{candidate_index}",
            path=str(path),
            kind="video",
            start=candidate_index * 1.0,
            end=candidate_index * 1.0 + 3.0,
            width=1080,
            height=1920,
            quality=0.84 + path_index * 0.01,
            intensity=0.72 + candidate_index * 0.03,
            sharpness=0.84,
            brightness=0.52,
        )
        for path_index, path in enumerate(paths)
        for candidate_index in range(4)
    ]

    source_indexes = {str(path.resolve()): index for index, path in enumerate(paths)}
    for media_order in ("manual", "capture_date"):
        variant = generate_ordered_variants(
            audio_map,
            candidates,
            paths,
            count=1,
            seed=45,
            media_order=media_order,
            min_clip_duration=0.2,
            clip_reuse_mode="multiple",
        )[0]
        indexes = [source_indexes[item.path] for item in variant.items]
        assert len(variant.items) > len(paths)
        assert indexes == sorted(indexes)
        assert len(set(indexes)) == len(paths)
        assert indexes.count(0) == 1
        for path in {item.path for item in variant.items}:
            source_starts = [item.source_start for item in variant.items if item.path == path]
            assert source_starts == sorted(source_starts)


def test_source_windows_reject_repeated_footage() -> None:
    candidates = [
        MediaCandidate(
            id="first",
            path="source.mov",
            kind="video",
            start=0.0,
            end=4.0,
            width=1080,
            height=1920,
            quality=0.9,
            intensity=0.8,
            sharpness=0.9,
            brightness=0.5,
        ),
        MediaCandidate(
            id="second",
            path="source.mov",
            kind="video",
            start=3.0,
            end=8.0,
            width=1080,
            height=1920,
            quality=0.88,
            intensity=0.78,
            sharpness=0.88,
            brightness=0.5,
        ),
    ]

    windows = _available_video_windows(candidates[1], 2.0, candidates, [(2.0, 4.0)])

    assert windows
    assert all(start >= 4.04 for start, _end in windows)


def test_close_source_fragments_become_one_continuous_shot() -> None:
    items = [
        TimelineItem(0.0, 3.0, "first", "source.mov", "video", 1.0, 4.0, 1080, 1920, 0.9),
        TimelineItem(3.0, 6.0, "second", "source.mov", "video", 4.4, 7.4, 1080, 1920, 0.8),
    ]

    merged = _coalesce_adjacent_source_items(items)

    assert len(merged) == 1
    assert merged[0].timeline_end == 6.0
    assert merged[0].source_start == 1.0
    assert merged[0].source_end == 7.0


def test_single_clip_reuse_keeps_one_fragment_per_video() -> None:
    audio_map = AudioMap(
        source_path="track.mp4",
        normalized_path="track.wav",
        analyzer="test",
        duration=20.0,
        bpm=150.0,
        beats=[index * 0.4 for index in range(51)],
        downbeats=[index * 1.6 for index in range(13)],
        onsets=[index * 0.2 for index in range(101)],
        sections=[AudioSection(0.0, 20.0, "high_energy", 0.82)],
        energy=[EnergyPoint(0.0, 0.8), EnergyPoint(20.0, 0.86)],
        edit_points=[0.0, 20.0],
    )
    paths = [Path(f"/tmp/single-source-{path_index}.mp4") for path_index in range(6)]
    candidates = [
        MediaCandidate(
            id=f"single-{path_index}-{candidate_index}",
            path=str(path),
            kind="video",
            start=candidate_index * 1.2,
            end=candidate_index * 1.2 + 3.0,
            width=1080,
            height=1920,
            quality=0.86,
            intensity=0.74,
            sharpness=0.86,
            brightness=0.52,
        )
        for path_index, path in enumerate(paths)
        for candidate_index in range(4)
    ]

    variant = generate_ordered_variants(
        audio_map,
        candidates,
        paths,
        count=1,
        seed=44,
        media_order="random",
        min_clip_duration=0.2,
        clip_reuse_mode="single",
    )[0]

    assert len(variant.items) == len(paths)
    assert len({item.path for item in variant.items}) == len(paths)


def test_min_clip_duration_controls_random_cut_density() -> None:
    audio_map = AudioMap(
        source_path="track.mp4",
        normalized_path="track.wav",
        analyzer="test",
        duration=18.0,
        bpm=148.0,
        beats=[index * 0.405 for index in range(45)],
        downbeats=[index * 1.62 for index in range(12)],
        onsets=[index * 0.2025 for index in range(90)],
        sections=[AudioSection(0.0, 18.0, "high_energy", 0.84)],
        energy=[EnergyPoint(0.0, 0.82), EnergyPoint(18.0, 0.82)],
        edit_points=[0.0, 18.0],
    )
    paths = [Path(f"/tmp/density-source-{index}.mp4") for index in range(5)]
    candidates = [
        MediaCandidate(
            id=f"density-{path_index}-{candidate_index}",
            path=str(path),
            kind="video",
            start=candidate_index * 1.0,
            end=candidate_index * 1.0 + 3.0,
            width=720,
            height=1280,
            quality=0.82,
            intensity=0.76,
            sharpness=0.8,
            brightness=0.5,
        )
        for path_index, path in enumerate(paths)
        for candidate_index in range(4)
    ]

    fast = generate_ordered_variants(
        audio_map,
        candidates,
        paths,
        count=1,
        seed=51,
        media_order="random",
        min_clip_duration=0.2,
        clip_reuse_mode="multiple",
    )[0]
    calmer = generate_ordered_variants(
        audio_map,
        candidates,
        paths,
        count=1,
        seed=51,
        media_order="random",
        min_clip_duration=1.3,
        clip_reuse_mode="multiple",
    )[0]

    assert len(fast.items) > len(calmer.items)
    assert min(item.duration for item in calmer.items) >= 1.29


def test_fast_clip_boundaries_snap_to_musical_grid() -> None:
    bpm = 123.0
    beat = 60.0 / bpm
    duration = 8.0
    beats = [index * beat for index in range(int(duration / beat) + 2)]
    half_beats = [beat_time + beat / 2 for beat_time in beats if beat_time + beat / 2 < duration]
    downbeats = [index * beat * 4 for index in range(int(duration / (beat * 4)) + 2)]
    audio_map = AudioMap(
        source_path="track.mp4",
        normalized_path="track.wav",
        analyzer="test",
        duration=duration,
        bpm=bpm,
        beats=beats,
        downbeats=downbeats,
        onsets=[],
        sections=[AudioSection(0.0, duration, "high_energy", 0.86)],
        energy=[EnergyPoint(0.0, 0.84), EnergyPoint(duration, 0.86)],
        edit_points=[0.0, duration],
    )
    paths = [Path(f"/tmp/grid-source-{path_index}.mp4") for path_index in range(3)]
    candidates = [
        MediaCandidate(
            id=f"grid-{path_index}-{candidate_index}",
            path=str(path),
            kind="video",
            start=candidate_index * 0.7,
            end=candidate_index * 0.7 + 2.4,
            width=1080,
            height=1920,
            quality=0.84,
            intensity=0.78,
            sharpness=0.84,
            brightness=0.5,
        )
        for path_index, path in enumerate(paths)
        for candidate_index in range(5)
    ]

    variant = generate_ordered_variants(
        audio_map,
        candidates,
        paths,
        count=1,
        seed=71,
        media_order="random",
        min_clip_duration=0.2,
        clip_reuse_mode="multiple",
    )[0]

    grid = [*beats, *half_beats, *downbeats, duration]
    boundaries = [item.timeline_start for item in variant.items[1:]]
    assert len(variant.items) > len(paths)
    assert all(min(abs(boundary - point) for point in grid) <= 0.035 for boundary in boundaries)


def test_random_clip_modes_have_distinct_coherent_rhythms() -> None:
    bpm = 123.0
    beat = 60.0 / bpm
    duration = 15.0
    beats = [index * beat for index in range(int(duration / beat) + 1)]
    audio_map = AudioMap(
        source_path="track.mp4",
        normalized_path="track.wav",
        analyzer="test",
        duration=duration,
        bpm=bpm,
        beats=beats,
        downbeats=beats[::4],
        onsets=[point + beat / 2 for point in beats if point + beat / 2 < duration],
        sections=[AudioSection(0.0, duration, "high_energy", 0.86)],
        energy=[EnergyPoint(0.0, 0.82), EnergyPoint(duration, 0.86)],
        edit_points=[0.0, duration],
    )
    paths = [Path(f"/tmp/rhythm-source-{index}.mp4") for index in range(12)]
    candidates = [
        MediaCandidate(
            id=f"rhythm-{path_index}-{candidate_index}",
            path=str(path),
            kind="video",
            start=candidate_index * 1.0,
            end=candidate_index * 1.0 + 4.0,
            width=1080,
            height=1920,
            quality=0.86,
            intensity=0.76,
            sharpness=0.84,
            brightness=0.52,
        )
        for path_index, path in enumerate(paths)
        for candidate_index in range(4)
    ]

    variants = generate_ordered_variants(
        audio_map,
        candidates,
        paths,
        count=4,
        seed=81,
        media_order="random",
        min_clip_duration=0.2,
        clip_reuse_mode="multiple",
    )
    by_name = {variant.name: variant for variant in variants}
    expected_paths = {str(path.resolve()) for path in paths}

    for variant in variants:
        assert {item.path for item in variant.items} == expected_paths
        assert all(
            previous.path != current.path
            for previous, current in zip(variant.items, variant.items[1:], strict=False)
        )
        path_counts = [sum(item.path == path for item in variant.items) for path in expected_paths]
        assert max(path_counts) - min(path_counts) <= 1
        assert min(item.duration for item in variant.items) >= 0.2
        assert variant.items[0].duration <= beat * 4.1
        assert max(item.duration for item in variant.items[1:]) <= beat * 3.1
        assert variant.items[-1].duration >= beat * 0.9
        short_shots = [item.duration < beat * 0.7 for item in variant.items]
        assert all(
            not is_short
            or (index > 0 and short_shots[index - 1])
            or (index + 1 < len(short_shots) and short_shots[index + 1])
            for index, is_short in enumerate(short_shots)
        )

    assert set(by_name) == {"Rhythm", "Drive", "Ramp", "Cinema"}
    beat_durations = [item.duration for item in by_name["Rhythm"].items]
    assert all(beat * 0.7 <= value <= beat * 1.3 for value in beat_durations[:-1])
    assert beat_durations[-1] <= beat * 1.8
    assert len(by_name["Cinema"].items) < len(by_name["Drive"].items) < len(by_name["Rhythm"].items)
    rhythm_paths = [item.path for item in by_name["Rhythm"].items]
    assert rhythm_paths[:12] != rhythm_paths[12:24]
    assert all(variant.score >= 0.4 for variant in variants)


def test_rhythm_uses_whole_beats_when_one_beat_is_shorter_than_the_minimum() -> None:
    bpm = 161.5
    beat = 60.0 / bpm
    duration = 15.0
    beats = [beat * index for index in range(1, int(duration / beat) + 1)]
    audio_map = AudioMap(
        source_path="track.mp4",
        normalized_path="track.wav",
        analyzer="test",
        duration=duration,
        bpm=bpm,
        beats=beats,
        downbeats=beats[::4],
        onsets=beats,
        sections=[AudioSection(0.0, duration, "high_energy", 0.86)],
        energy=[EnergyPoint(0.0, 0.82), EnergyPoint(duration, 0.86)],
        edit_points=[0.0, duration],
    )

    boundaries = _rhythmic_clip_boundaries(
        audio_map,
        ORDERED_STYLES[0],
        min_slot_duration=0.4,
        max_slots=37,
    )
    durations = [
        right - left for left, right in zip(boundaries, boundaries[1:], strict=False)
    ]

    assert 18 <= len(durations) <= 20
    assert min(abs(boundaries[1] - downbeat) for downbeat in audio_map.downbeats) <= 0.0001
    assert all(min(abs(cut - anchor) for anchor in beats) <= 0.0001 for cut in boundaries[1:-1])
    assert all(value >= 0.4 for value in durations)
    assert max(durations[1:-1]) - min(durations[1:-1]) <= 0.0002


def test_ramp_accelerates_and_does_not_slow_down_at_the_finish() -> None:
    duration = 15.0
    paths = [Path(f"/tmp/ramp-source-{index}.mp4") for index in range(12)]
    candidates = [
        MediaCandidate(
            id=f"ramp-{path_index}-{candidate_index}",
            path=str(path),
            kind="video",
            start=candidate_index * 1.0,
            end=candidate_index * 1.0 + 5.0,
            width=1080,
            height=1920,
            quality=0.84,
            intensity=0.72,
            sharpness=0.82,
            brightness=0.52,
        )
        for path_index, path in enumerate(paths)
        for candidate_index in range(4)
    ]

    for bpm in (95.7, 117.45, 161.5):
        beat = 60.0 / bpm
        beats = [index * beat for index in range(int(duration / beat) + 1)]
        audio_map = AudioMap(
            source_path="track.mp4",
            normalized_path="track.wav",
            analyzer="test",
            duration=duration,
            bpm=bpm,
            beats=beats,
            downbeats=beats[::4],
            onsets=[point + beat / 2 for point in beats if point + beat / 2 < duration],
            sections=[AudioSection(0.0, duration, "high_energy", 0.86)],
            energy=[EnergyPoint(0.0, 0.82), EnergyPoint(duration, 0.86)],
            edit_points=[0.0, duration],
        )
        variants = generate_ordered_variants(
            audio_map,
            candidates,
            paths,
            count=4,
            seed=92,
            media_order="random",
            min_clip_duration=0.4,
            clip_reuse_mode="multiple",
        )
        durations = [item.duration for item in variants[2].items]
        first_end = len(durations) // 3
        second_end = 2 * len(durations) // 3
        first = mean(durations[:first_end])
        middle = mean(durations[first_end:second_end])
        final = mean(durations[second_end:])

        assert first >= middle * 1.18
        minimum_finish_ratio = 1.12 if beat >= 0.4 else 1.03
        assert middle >= final * minimum_finish_ratio
        # The music can end between two detected beats. Keep the final internal cut
        # on a real beat even when that leaves a slightly longer closing shot.
        assert durations[-1] <= final * 1.35
        for variant in variants:
            internal_cuts = [item.timeline_end for item in variant.items[:-1]]
            assert all(min(abs(cut - point) for point in beats) <= 0.0002 for cut in internal_cuts)


def test_ordered_variants_diversify_strong_candidates() -> None:
    audio_map = AudioMap(
        source_path="track.mp4",
        normalized_path="track.wav",
        analyzer="test",
        duration=18.0,
        bpm=120.0,
        beats=[index * 0.5 for index in range(37)],
        downbeats=[index * 2.0 for index in range(10)],
        onsets=[index * 0.25 for index in range(73)],
        sections=[AudioSection(0.0, 18.0, "body", 0.65)],
        energy=[EnergyPoint(0.0, 0.45), EnergyPoint(9.0, 0.75), EnergyPoint(18.0, 0.55)],
        edit_points=[0.0, 18.0],
    )
    paths = [Path(f"/tmp/diverse-source-{index}.mp4") for index in range(5)]
    candidates = [
        MediaCandidate(
            id=f"candidate-{path_index}-{candidate_index}",
            path=str(path),
            kind="video",
            start=candidate_index * 0.8,
            end=candidate_index * 0.8 + 6.0,
            width=720,
            height=1280,
            quality=0.86 - candidate_index * 0.01,
            intensity=0.45 + candidate_index * 0.08,
            sharpness=0.82,
            brightness=0.52,
        )
        for path_index, path in enumerate(paths)
        for candidate_index in range(5)
    ]

    variants = generate_ordered_variants(
        audio_map,
        candidates,
        paths,
        count=4,
        seed=123,
        media_order="manual",
    )

    candidate_sequences = {
        tuple(item.candidate_id for item in variant.items) for variant in variants
    }
    per_source_choices = [
        {variant.items[source_index].candidate_id for variant in variants}
        for source_index in range(len(paths))
    ]
    assert len(candidate_sequences) > 3
    assert any(len(choices) > 1 for choices in per_source_choices)


def test_ordered_video_window_can_span_neighboring_analysis_candidates() -> None:
    audio_map = AudioMap(
        source_path="track.wav",
        normalized_path="track.wav",
        analyzer="test",
        duration=8.0,
        bpm=120.0,
        beats=[0.0, 8.0],
        downbeats=[0.0, 8.0],
        onsets=[],
        sections=[AudioSection(0.0, 8.0, "body", 0.5)],
        energy=[EnergyPoint(0.0, 0.5), EnergyPoint(8.0, 0.5)],
        edit_points=[0.0, 8.0],
    )
    path = Path("/tmp/source.mp4")
    candidates = [
        MediaCandidate(
            id=f"candidate-{index}",
            path=str(path),
            kind="video",
            start=start,
            end=end,
            width=720,
            height=1280,
            quality=0.8,
            intensity=0.5,
            sharpness=0.8,
            brightness=0.5,
        )
        for index, (start, end) in enumerate(((0.0, 3.4), (3.3, 6.7), (6.6, 10.0)))
    ]

    variant = generate_ordered_variants(
        audio_map,
        candidates,
        [path],
        count=1,
        clip_reuse_mode="single",
    )[0]

    assert variant.items[0].source_end <= 10.0
    assert variant.items[0].source_end - variant.items[0].source_start == 8.0

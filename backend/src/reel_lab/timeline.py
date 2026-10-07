"""Generate diverse edit decision lists from music and media analysis."""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from reel_lab.audio import energy_at
from reel_lab.models import AudioMap, MediaCandidate, TimelineItem, Variant
from reel_lab.system import clamp


@dataclass(frozen=True)
class Style:
    """Creative defaults that alter pace and source selection."""

    name: str
    description: str
    bars_per_shot: float
    photo_bias: float = 0.0
    intensity_bias: float = 0.0


STYLES = (
    Style("Rhythm", "Confident cuts that follow the music without rushing", 0.9),
    Style(
        "Drive",
        "Quick accents where they enhance the music",
        0.68,
        intensity_bias=0.1,
    ),
    Style(
        "Ramp",
        "Shots get shorter as the music builds to its peak",
        1.2,
        intensity_bias=0.06,
    ),
    Style("Cinema", "Give the best moments more time on screen", 1.75, intensity_bias=-0.08),
)


@dataclass(frozen=True)
class OrderedStyle:
    """Pacing and source-window choices for chronological reels."""

    name: str
    description: str
    timing_curve: float
    snap_mode: str
    visual_mode: str


@dataclass(frozen=True)
class MusicGrid:
    """Quantized musical points used for edit boundaries."""

    duration: float
    beat_period: float
    beats: list[float]
    half_beats: list[float]
    downbeats: list[float]
    strong_beats: list[float]
    onsets: list[float]
    sections: list[float]


ORDERED_STYLES = (
    OrderedStyle("Rhythm", "Confident beat-aligned cuts without rushing", 1.0, "beat", "director"),
    OrderedStyle(
        "Drive",
        "Two quick shots, then a longer shot for the musical phrase",
        1.0,
        "onset",
        "motion",
    ),
    OrderedStyle("Ramp", "Shots get shorter toward the ending", 0.84, "mixed", "energy"),
    OrderedStyle("Cinema", "Best moments with deliberate, strong cuts", 1.08, "mixed", "quality"),
)

SOURCE_SEAM_SECONDS = 0.04
SOURCE_COALESCE_GAP_SECONDS = 0.9


def generate_variants(
    audio_map: AudioMap,
    candidates: list[MediaCandidate],
    *,
    count: int = 4,
    seed: int = 20260725,
    min_clip_duration: float = 0.62,
    clip_reuse_mode: str = "multiple",
) -> list[Variant]:
    """Generate scored, deterministic creative variants."""
    if count < 1:
        raise ValueError("Variant count must be positive")
    if not candidates:
        raise ValueError("No visual candidates")

    variants = []
    for index in range(count):
        style = STYLES[index % len(STYLES)]
        variant_seed = seed + index * 7919
        rng = random.Random(variant_seed)
        slots = _build_slots(audio_map, style, min_slot_duration=min_clip_duration)
        items = _fill_slots(
            slots,
            audio_map,
            candidates,
            style,
            rng,
            allow_source_reuse=clip_reuse_mode == "multiple",
        )
        score = _variant_score(items)
        variants.append(
            Variant(
                id=index + 1,
                name=style.name if index < len(STYLES) else f"{style.name} {index + 1}",
                description=style.description,
                seed=variant_seed,
                score=round(score, 4),
                items=items,
            )
        )
    return variants


def generate_ordered_variants(
    audio_map: AudioMap,
    candidates: list[MediaCandidate],
    media_paths: list[Path],
    *,
    count: int = 4,
    seed: int = 20260725,
    include_all: bool = True,
    media_order: str = "manual",
    min_clip_duration: float = 0.6,
    clip_reuse_mode: str = "multiple",
) -> list[Variant]:
    """Generate variants using manual, capture-date, or deterministic random ordering."""
    if media_order not in {"capture_date", "manual", "random"}:
        raise ValueError("Unknown media order")
    if clip_reuse_mode not in {"single", "multiple"}:
        raise ValueError("Unknown video reuse mode")
    ordered_paths = list(dict.fromkeys(str(path.expanduser().resolve()) for path in media_paths))
    grouped: dict[str, list[MediaCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(str(Path(candidate.path).resolve()), []).append(candidate)
    missing = [path for path in ordered_paths if path not in grouped]
    if missing:
        raise ValueError(f"No candidates for {len(missing)} sources")
    if audio_map.duration / max(1, len(ordered_paths)) < 0.22:
        raise ValueError("Track is too short to include every source")

    variants: list[Variant] = []
    for index in range(count):
        candidate_usage: dict[str, int] = {}
        style = ORDERED_STYLES[index % len(ORDERED_STYLES)]
        variant_seed = seed + index * 7919
        rng = random.Random(variant_seed)
        selected_paths = list(ordered_paths)
        if not include_all:
            target_count = min(
                len(ordered_paths), (34, 40, 32, 26)[index % len(ORDERED_STYLES)]
            )
            selected_paths = _select_ordered_paths(
                ordered_paths,
                grouped,
                target_count,
                style,
                index,
            )
        if media_order == "random":
            hero_path = _choose_hero_path(selected_paths, grouped, candidate_usage, rng)
            selected_paths = _coherent_random_order(
                selected_paths,
                grouped,
                hero_path,
                style,
                rng,
            )
        use_clip_fill = _should_use_clip_ordered_fill(clip_reuse_mode)
        boundaries = (
            _clip_ordered_boundaries(
                audio_map,
                len(selected_paths),
                style,
                index,
                min_clip_duration=min_clip_duration,
            )
            if use_clip_fill
            else _ordered_boundaries(audio_map, len(selected_paths), style, index)
        )
        if use_clip_fill and len(boundaries) - 1 > len(selected_paths):
            items = _fill_clip_ordered_slots(
                boundaries,
                audio_map,
                selected_paths,
                grouped,
                style,
                rng,
                index,
                candidate_usage=candidate_usage,
                hero_first=media_order == "random",
            )
        else:
            items = _fill_ordered_slots(
                boundaries,
                audio_map,
                selected_paths,
                grouped,
                style,
                rng,
                index,
                candidate_usage=candidate_usage,
                hero_first=media_order == "random",
            )
        description = style.description
        if not include_all:
            description += f"; best {len(selected_paths)} of {len(ordered_paths)}"
        if media_order == "capture_date":
            description += "; capture-date order"
        elif media_order == "random":
            description += "; random order"
        if len(items) > len(selected_paths):
            description += "; reuse the best video segments"
        elif clip_reuse_mode == "single":
            description += "; one segment per video"
        variants.append(
            Variant(
                id=index + 1,
                name=style.name,
                description=description,
                seed=variant_seed,
                score=round(float(np.mean([item.score for item in items])), 4),
                items=items,
            )
        )
    return variants


def _select_ordered_paths(
    ordered_paths: list[str],
    grouped: dict[str, list[MediaCandidate]],
    target_count: int,
    style: OrderedStyle,
    variant_index: int,
) -> list[str]:
    if target_count >= len(ordered_paths):
        return ordered_paths
    boundaries = np.linspace(0, len(ordered_paths), target_count + 1, dtype=int)
    selected: list[str] = []
    for bin_index, (start, end) in enumerate(zip(boundaries, boundaries[1:], strict=False)):
        choices = ordered_paths[start : max(start + 1, end)]
        selected.append(
            max(
                choices,
                key=lambda path: max(
                    _ordered_candidate_score(
                        candidate,
                        0.55 + 0.20 * np.sin((bin_index + variant_index) * 0.7),
                        style.visual_mode,
                        start + bin_index,
                    )
                    for candidate in grouped[path]
                ),
            )
        )
    return selected


def _choose_hero_path(
    ordered_paths: list[str],
    grouped: dict[str, list[MediaCandidate]],
    candidate_usage: dict[str, int],
    rng: random.Random,
) -> str:
    ranked: list[tuple[float, str, str]] = []
    for path in ordered_paths:
        score, candidate = max(
            ((_hero_candidate_score(candidate), candidate) for candidate in grouped[path]),
            key=lambda pair: pair[0],
        )
        ranked.append((score, candidate.id, path))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if len(ranked) == 1:
        return ranked[0][2]

    best_score = ranked[0][0]
    top_limit = min(len(ranked), max(3, min(8, round(len(ranked) * 0.35))))
    pool = [
        item
        for item in ranked[:top_limit]
        if item[0] >= best_score - 0.16 and item[0] >= 0.58
    ] or ranked[:1]
    adjusted: list[tuple[float, str]] = []
    for rank, (score, candidate_id, path) in enumerate(pool):
        usage_penalty = min(0.34, candidate_usage.get(candidate_id, 0) * 0.12)
        rank_penalty = rank * 0.014
        adjusted.append((clamp(score - usage_penalty - rank_penalty, 0.02, 1.0), path))
    weights = [score**4 for score, _ in adjusted]
    return rng.choices(adjusted, weights=weights, k=1)[0][1]


def _coherent_random_order(
    ordered_paths: list[str],
    grouped: dict[str, list[MediaCandidate]],
    hero_path: str,
    style: OrderedStyle,
    rng: random.Random,
) -> list[str]:
    """Keep random variants varied without alternating harsh visual extremes."""
    signatures = {
        path: _source_visual_signature(grouped[path], style)
        for path in ordered_paths
    }
    remaining = [path for path in ordered_paths if path != hero_path]
    result = [hero_path]
    while remaining:
        current = signatures[result[-1]]

        def transition_cost(
            path: str,
            current_signature: tuple[float, float, float, float] = current,
        ) -> float:
            brightness, action, face, photo_share = signatures[path]
            return (
                0.62 * abs(brightness - current_signature[0])
                + 0.24 * abs(action - current_signature[1])
                + 0.08 * abs(face - current_signature[2])
                + 0.06 * abs(photo_share - current_signature[3])
                + rng.random() * 0.045
            )

        selected = min(remaining, key=transition_cost)
        result.append(selected)
        remaining.remove(selected)
    return result


def _source_visual_signature(
    candidates: list[MediaCandidate],
    style: OrderedStyle,
) -> tuple[float, float, float, float]:
    representative = max(
        candidates,
        key=lambda candidate: _ordered_candidate_score(
            candidate,
            0.62,
            style.visual_mode,
            0,
        ),
    )
    return (
        representative.brightness,
        _candidate_action(representative),
        representative.face_score,
        float(representative.kind == "photo"),
    )


def _ordered_boundaries(
    audio_map: AudioMap,
    item_count: int,
    style: OrderedStyle,
    variant_index: int,
    *,
    minimum_duration: float | None = None,
) -> list[float]:
    duration = audio_map.duration
    minimum = min(0.24, duration / item_count * 0.62)
    if minimum_duration is not None:
        minimum = max(minimum, minimum_duration)
    grid = _build_music_grid(audio_map)
    targets = []
    for index in range(1, item_count):
        target = duration * (index / item_count) ** style.timing_curve
        if style.snap_mode in {"wave", "pulse"}:
            target += 0.08 * np.sin(index * 1.7 + variant_index)
        targets.append(target)

    required = []
    if style.snap_mode == "downbeat":
        required = _required_strong_beats(grid, len(targets), minimum)
    elif style.snap_mode == "beat":
        regular = _regular_beat_boundaries(grid, item_count, minimum)
        if regular is not None:
            return [0.0, *regular, round(duration, 4)]

    phrase_start = next(
        (
            point
            for point in grid.downbeats
            if max(minimum, grid.beat_period * 1.25) <= point <= min(3.0, duration * 0.24)
        ),
        None,
    )
    if targets and phrase_start is not None:
        available_after_phrase = sum(
            phrase_start <= point < duration - minimum for point in grid.beats
        )
        if available_after_phrase >= len(targets):
            targets = [phrase_start] + [
                phrase_start
                + (duration - phrase_start) * (index / (item_count - 1)) ** style.timing_curve
                for index in range(1, item_count - 1)
            ]
            required = sorted({*required, phrase_start})

    for dense in (False, True):
        events = _timing_events(audio_map, style.snap_mode, dense=dense)
        if phrase_start is not None and phrase_start in required:
            events = [event for event in events if event[0] >= phrase_start - 0.0001]
        selected = _select_musical_boundaries(
            events,
            targets,
            duration,
            minimum,
            _estimate_beat_period(audio_map),
            required,
        )
        if selected is not None:
            return [0.0, *selected, round(duration, 4)]
    raise ValueError("Beat grid is too sparse for the selected number of shots")


def _regular_beat_boundaries(
    grid: MusicGrid,
    item_count: int,
    minimum: float,
) -> list[float] | None:
    """Spread cuts by beat count so the cadence stays perceptually even."""
    required_count = item_count - 1
    for points in (grid.beats,):
        if len(points) < item_count:
            continue
        indexes = [round(index * len(points) / item_count) for index in range(1, item_count)]
        if len(set(indexes)) != required_count or any(index >= len(points) for index in indexes):
            continue
        selected = [points[index] for index in indexes]
        boundaries = [0.0, *selected, grid.duration]
        if all(
            right - left >= minimum
            for left, right in zip(boundaries, boundaries[1:], strict=False)
        ):
            return [round(point, 4) for point in selected]
    return None


def _select_musical_boundaries(
    events: list[tuple[float, float]],
    targets: list[float],
    duration: float,
    minimum: float,
    beat_period: float,
    required: list[float],
) -> list[float] | None:
    """Choose the whole cut sequence on musical anchors, not one cut at a time."""
    if not targets:
        return []
    candidates = [event for event in events if 0.0 < event[0] < duration]
    required_indexes = {
        index
        for index, (time, _priority) in enumerate(candidates)
        if any(abs(time - required_time) < 0.0001 for required_time in required)
    }
    if len(required_indexes) != len(required):
        return None
    previous: dict[int, tuple[float, tuple[int, ...]]] = {}
    for boundary_index, target in enumerate(targets):
        remaining = len(targets) - boundary_index - 1
        layer: dict[int, tuple[float, tuple[int, ...]]] = {}
        for event_index, (time, priority) in enumerate(candidates):
            if time < (boundary_index + 1) * minimum:
                continue
            if time > duration - (remaining + 1) * minimum:
                continue
            event_cost = abs(time - target) / max(beat_period, 0.01) - priority * 0.42
            if boundary_index == 0:
                if any(required_index < event_index for required_index in required_indexes):
                    continue
                layer[event_index] = (event_cost, (event_index,))
                continue
            usable = [
                state
                for previous_index, state in previous.items()
                if candidates[previous_index][0] <= time - minimum
                and not any(
                    previous_index < required_index < event_index
                    for required_index in required_indexes
                )
            ]
            if not usable:
                continue
            previous_cost, path = min(usable, key=lambda state: state[0])
            layer[event_index] = (previous_cost + event_cost, (*path, event_index))
        if not layer:
            return None
        previous = layer
    complete = [
        state
        for event_index, state in previous.items()
        if not any(required_index > event_index for required_index in required_indexes)
    ]
    if not complete:
        return None
    _cost, path = min(complete, key=lambda state: state[0])
    return [round(candidates[index][0], 4) for index in path]


def _timing_events(
    audio_map: AudioMap,
    mode: str,
    *,
    dense: bool = False,
) -> list[tuple[float, float]]:
    grid = _build_music_grid(audio_map)
    priorities: dict[float, float] = {}

    def add(values: list[float], priority: float) -> None:
        for value in values:
            key = round(value, 4)
            priorities[key] = max(priorities.get(key, 0.0), priority)

    if mode == "beat":
        add(grid.beats, 0.42)
    elif mode == "downbeat":
        add(grid.downbeats, 0.52)
        add(grid.strong_beats, 0.42)
        add(grid.beats, 0.24)
    elif mode == "onset":
        add(grid.downbeats, 0.48)
        add(grid.beats, 0.34)
    elif mode == "pulse":
        add(grid.onsets, 0.4)
        add(grid.beats, 0.34)
        add(grid.half_beats, 0.3)
        add(grid.downbeats, 0.48)
    elif mode == "mixed":
        add(grid.sections, 0.54)
        add(grid.downbeats, 0.48)
        add(grid.beats, 0.3)
    elif mode == "wave":
        add(grid.downbeats, 0.42)
        add(grid.beats, 0.34)
        add(grid.onsets, 0.28)
        add(grid.half_beats, 0.2)
    else:
        add(grid.downbeats, 0.46)
        add(grid.beats, 0.34)
    if dense:
        add(grid.beats, 0.12)
    return sorted(priorities.items())


def _required_strong_beats(
    grid: MusicGrid,
    capacity: int,
    minimum: float,
) -> list[float]:
    """Keep bar starts, then add mid-bar accents when the edit has room."""
    usable_downbeats = [
        point for point in grid.downbeats if minimum <= point <= grid.duration - minimum
    ]
    usable_accents = [
        point
        for point in grid.strong_beats
        if minimum <= point <= grid.duration - minimum
        and all(abs(point - downbeat) > 0.0001 for downbeat in usable_downbeats)
    ]
    if len(usable_downbeats) >= capacity:
        indexes = np.linspace(0, len(usable_downbeats) - 1, capacity, dtype=int)
        return [usable_downbeats[index] for index in indexes]
    remaining = capacity - len(usable_downbeats)
    if len(usable_accents) > remaining:
        indexes = np.linspace(0, len(usable_accents) - 1, remaining, dtype=int)
        usable_accents = [usable_accents[index] for index in indexes]
    return sorted([*usable_downbeats, *usable_accents])


def _should_use_clip_ordered_fill(clip_reuse_mode: str) -> bool:
    return clip_reuse_mode == "multiple"


def _clip_ordered_boundaries(
    audio_map: AudioMap,
    required_count: int,
    style: OrderedStyle,
    variant_index: int,
    *,
    min_clip_duration: float,
) -> list[float]:
    min_slot_duration = clamp(min_clip_duration, 0.2, 2.0)
    max_by_duration = max(required_count, int(audio_map.duration / min_slot_duration))
    if min_slot_duration <= 0.25:
        source_multiplier = 5
    elif min_slot_duration <= 0.55:
        source_multiplier = 3
    else:
        source_multiplier = 2
    max_by_sources = max(required_count, min(112, required_count * source_multiplier + 12))
    max_slots = min(max_by_duration, max_by_sources)
    boundaries = _rhythmic_clip_boundaries(
        audio_map,
        style,
        min_slot_duration=min_slot_duration,
        max_slots=max_slots,
    )
    if len(boundaries) - 1 < required_count:
        musical_tolerance = min(0.06, _estimate_beat_period(audio_map) * 0.12)
        return _ordered_boundaries(
            audio_map,
            required_count,
            style,
            variant_index,
            minimum_duration=max(0.2, min_slot_duration - musical_tolerance),
        )
    return boundaries


def _rhythmic_clip_boundaries(
    audio_map: AudioMap,
    style: OrderedStyle,
    *,
    min_slot_duration: float,
    max_slots: int,
) -> list[float]:
    """Build a coherent style-specific cadence from musical beat units."""
    grid = _build_music_grid(audio_map)
    if audio_map.beat_confidence < 0.45:
        min_slot_duration = max(min_slot_duration, grid.beat_period * 2.0)
    if style.name == "Rhythm":
        return _steady_beat_boundaries(
            grid,
            min_slot_duration=min_slot_duration,
            max_slots=max_slots,
        )
    beat_period = grid.beat_period
    musical_tolerance = min(0.06, beat_period * 0.12)
    musical_minimum = max(0.2, min_slot_duration - musical_tolerance)
    minimum_units = max(1.0, np.ceil(musical_minimum / beat_period - 1e-6))
    events = _rhythm_events(grid, style)
    boundaries = [0.0]
    pattern_index = 0

    pickup = next(
        (
            point
            for point in grid.downbeats
            if max(musical_minimum, beat_period * 1.25) <= point <= min(3.0, grid.duration * 0.24)
        ),
        None,
    )
    if pickup is not None:
        boundaries.append(round(pickup, 4))

    while len(boundaries) - 1 < max_slots:
        current = boundaries[-1]
        remaining = grid.duration - current
        if remaining <= musical_minimum * 1.5:
            break
        progress = current / max(grid.duration, 0.01)
        units = max(minimum_units, _style_rhythm_units(style.name, pattern_index, progress))
        target = current + units * beat_period
        if target > grid.duration - musical_minimum:
            break
        lower = current + musical_minimum
        usable = [
            point
            for point in events
            if lower <= point <= grid.duration - musical_minimum
        ]
        if not usable:
            break
        next_point = min(usable, key=lambda point: abs(point - target))
        boundaries.append(round(next_point, 4))
        pattern_index += 1

    boundaries.append(round(grid.duration, 4))
    short_shot_limit = beat_period * 0.7
    final_shot_limit = beat_period * 0.9
    short_final_shot = len(boundaries) >= 3 and boundaries[-1] - boundaries[-2] < final_shot_limit
    isolated_penultimate_shot = (
        len(boundaries) >= 4
        and boundaries[-2] - boundaries[-3] < short_shot_limit
        and boundaries[-3] - boundaries[-4] >= short_shot_limit
    )
    if short_final_shot:
        merged_short_shot = False
        while len(boundaries) >= 3 and boundaries[-2] - boundaries[-3] < short_shot_limit:
            boundaries.pop(-2)
            merged_short_shot = True
        if not merged_short_shot:
            boundaries.pop(-2)
    elif isolated_penultimate_shot:
        boundaries.pop(-2)
    if style.name == "Ramp":
        _rebalance_ramp_tail(boundaries, events, grid, musical_minimum)
    return boundaries


def _steady_beat_boundaries(
    grid: MusicGrid,
    *,
    min_slot_duration: float,
    max_slots: int,
) -> list[float]:
    """Split Rhythm into the most even sequence of whole detected beats."""
    preferred_minimum = min_slot_duration + min(0.22, grid.beat_period * 0.4)
    beat_units = max(1, int(np.ceil(preferred_minimum / grid.beat_period - 1e-6)))
    ideal_count = grid.duration / (beat_units * grid.beat_period)
    center_count = round(ideal_count)
    edge_minimum = max(min_slot_duration, grid.beat_period * 0.9)
    options: list[tuple[float, list[float]]] = []
    anchors = grid.strong_beats if beat_units >= 2 and len(grid.strong_beats) >= 3 else grid.beats
    phrase_start = next(
        (
            point
            for point in grid.downbeats
            if edge_minimum <= point <= min(3.0, grid.duration * 0.24)
        ),
        None,
    )
    if phrase_start is not None and anchors is grid.strong_beats:
        anchors = [point for point in anchors if point >= phrase_start - 0.0001]

    for item_count in range(
        max(2, center_count - 2),
        min(max_slots, center_count + 2) + 1,
    ):
        boundaries = _even_beat_boundaries(
            anchors,
            grid.duration,
            item_count,
            min_slot_duration,
            edge_minimum,
        )
        if boundaries is None:
            continue
        durations = np.diff(boundaries)
        variation = float(np.std(durations) / max(np.mean(durations), 0.01))
        cadence_penalty = abs(item_count - ideal_count) / max(ideal_count, 1.0)
        options.append((variation + cadence_penalty * 0.08, boundaries))

    if options:
        return min(options, key=lambda item: item[0])[1]

    fallback_count = min(max_slots, max(2, center_count))
    events = [(point, 0.0) for point in anchors]
    while fallback_count >= 2:
        targets = [
            grid.duration * index / fallback_count for index in range(1, fallback_count)
        ]
        selected = _select_musical_boundaries(
            events,
            targets,
            grid.duration,
            edge_minimum,
            grid.beat_period,
            [],
        )
        if selected is not None:
            return [0.0, *selected, round(grid.duration, 4)]
        fallback_count -= 1
    return [0.0, round(grid.duration, 4)]


def _even_beat_boundaries(
    beats: list[float],
    duration: float,
    item_count: int,
    minimum: float,
    edge_minimum: float,
) -> list[float] | None:
    """Choose beat cuts by interval cost so adjacent shots stay even."""
    target = duration / item_count
    usable = [point for point in beats if edge_minimum <= point <= duration - edge_minimum]
    previous: dict[int, tuple[float, tuple[int, ...]]] = {
        index: (((point - target) / target) ** 2, (index,))
        for index, point in enumerate(usable)
    }

    for _cut_number in range(2, item_count):
        layer: dict[int, tuple[float, tuple[int, ...]]] = {}
        for index, point in enumerate(usable):
            choices = [
                (
                    cost + (((point - usable[previous_index]) - target) / target) ** 2,
                    (*path, index),
                )
                for previous_index, (cost, path) in previous.items()
                if point - usable[previous_index] >= minimum
            ]
            if choices:
                layer[index] = min(choices, key=lambda item: item[0])
        if not layer:
            return None
        previous = layer

    complete = [
        (
            cost + (((duration - usable[last_index]) - target) / target) ** 2,
            path,
        )
        for last_index, (cost, path) in previous.items()
        if duration - usable[last_index] >= edge_minimum
    ]
    if not complete:
        return None
    _cost, path = min(complete, key=lambda item: item[0])
    return [0.0, *[round(usable[index], 4) for index in path], round(duration, 4)]


def _rebalance_ramp_tail(
    boundaries: list[float],
    events: list[float],
    grid: MusicGrid,
    min_slot_duration: float,
) -> None:
    """Keep the final ramp cut from slowing down on the leftover music."""
    if len(boundaries) < 4:
        return
    previous_start = boundaries[-3]
    previous_duration = boundaries[-2] - previous_start
    final_duration = boundaries[-1] - boundaries[-2]
    if final_duration <= previous_duration * 1.12:
        return

    minimum_final_duration = min_slot_duration
    if min_slot_duration < grid.beat_period * 0.6:
        minimum_final_duration = grid.beat_period * 0.9
    candidates = [
        point
        for point in events
        if point - previous_start >= min_slot_duration
        and grid.duration - point >= minimum_final_duration
        and grid.duration - point <= (point - previous_start) * 1.08
    ]
    if not candidates:
        return
    fast_target = max(min_slot_duration, grid.beat_period)
    boundaries[-2] = round(
        min(candidates, key=lambda point: abs((grid.duration - point) - fast_target)),
        4,
    )


def _rhythm_events(grid: MusicGrid, style: OrderedStyle) -> list[float]:
    values = [*grid.beats, *grid.downbeats] if style.name in {"Drive", "Cinema"} else grid.beats
    return [point for point in _unique_times(values, grid.duration) if 0.0 < point < grid.duration]


def _style_rhythm_units(style_name: str, pattern_index: int, progress: float) -> float:
    if style_name == "Ramp":
        if progress < 0.35:
            return 3.0
        if progress < 0.78:
            return 2.0
        return 1.0
    patterns = {
        "Rhythm": (1.0,),
        "Drive": (1.0, 1.0, 2.0, 2.0, 2.0),
        "Cinema": (3.0, 1.0),
    }
    pattern = patterns.get(style_name, (2.0,))
    return pattern[pattern_index % len(pattern)]


def _fill_ordered_slots(
    boundaries: list[float],
    audio_map: AudioMap,
    ordered_paths: list[str],
    grouped: dict[str, list[MediaCandidate]],
    style: OrderedStyle,
    rng: random.Random,
    variant_index: int,
    *,
    candidate_usage: dict[str, int],
    hero_first: bool = False,
) -> list[TimelineItem]:
    items: list[TimelineItem] = []
    for item_index, path in enumerate(ordered_paths):
        start, end = boundaries[item_index : item_index + 2]
        duration = end - start
        source_candidates = grouped[path]
        suitable = [
            candidate
            for candidate in source_candidates
            if candidate.kind == "photo" or candidate.duration >= duration + 0.01
        ] or source_candidates
        target_intensity = energy_at(audio_map, (start + end) / 2)
        ranked = _rank_ordered_candidates(
            suitable,
            target_intensity,
            style.visual_mode,
            item_index,
        )
        if hero_first and item_index == 0:
            hero_ranked = sorted(
                ((_hero_candidate_score(candidate), candidate) for candidate in suitable),
                key=lambda pair: pair[0],
                reverse=True,
            )
            score, selected = _choose_ordered_candidate(hero_ranked, candidate_usage, rng)
        else:
            score, selected = _choose_ordered_candidate(ranked, candidate_usage, rng)
        candidate_usage[selected.id] = candidate_usage.get(selected.id, 0) + 1
        if selected.kind == "video":
            source_start, source_end = _video_window(
                selected,
                duration,
                source_candidates,
                rng,
            )
        else:
            source_start = 0.0
            source_end = duration
        items.append(
            TimelineItem(
                timeline_start=start,
                timeline_end=end,
                candidate_id=selected.id,
                path=path,
                kind=selected.kind,
                source_start=round(source_start, 4),
                source_end=round(source_end, 4),
                width=selected.width,
                height=selected.height,
                score=round(score, 4),
                focus_x=selected.focus_x,
                focus_y=selected.focus_y,
                motion=("zoom-in" if (variant_index + item_index) % 2 == 0 else "zoom-out")
                if selected.kind == "photo"
                else "none",
            )
        )
    return items


def _fill_clip_ordered_slots(
    boundaries: list[float],
    audio_map: AudioMap,
    ordered_paths: list[str],
    grouped: dict[str, list[MediaCandidate]],
    style: OrderedStyle,
    rng: random.Random,
    variant_index: int,
    *,
    candidate_usage: dict[str, int],
    hero_first: bool = False,
) -> list[TimelineItem]:
    items: list[TimelineItem] = []
    recent_ids: list[str] = []
    recent_paths: list[str] = []
    used_intervals: dict[str, list[tuple[float, float]]] = {}
    slot_count = max(0, len(boundaries) - 1)
    path_sequence = _clip_path_sequence(
        ordered_paths,
        grouped,
        slot_count,
        interleave=hero_first,
        rng=rng,
    )
    all_entries = [
        (path, candidate)
        for path in ordered_paths
        for candidate in grouped[path]
    ]

    for item_index, (start, end) in enumerate(zip(boundaries, boundaries[1:], strict=False)):
        duration = end - start
        target_intensity = energy_at(audio_map, (start + end) / 2)
        if item_index < len(path_sequence):
            path = path_sequence[item_index]
            source_candidates = grouped[path]
            suitable = [
                candidate
                for candidate in source_candidates
                if candidate.kind == "photo" or candidate.duration >= duration + 0.01
            ] or source_candidates
            if hero_first and item_index == 0:
                ranked = sorted(
                    ((_hero_candidate_score(candidate), candidate) for candidate in suitable),
                    key=lambda pair: pair[0],
                    reverse=True,
                )
            else:
                ranked = _rank_ordered_candidates(
                    suitable,
                    target_intensity,
                    style.visual_mode,
                    item_index,
                )
            score, selected, source_window = _choose_ordered_candidate_window(
                ranked,
                duration,
                source_candidates,
                used_intervals.get(path, []),
                candidate_usage,
                rng,
                minimum_source_start=(
                    items[-1].source_end + SOURCE_SEAM_SECONDS
                    if items and items[-1].kind == "video" and items[-1].path == path
                    else None
                ),
            )
        else:
            score, path, selected, source_window = _choose_clip_candidate(
                all_entries,
                duration,
                target_intensity,
                style,
                recent_ids,
                recent_paths,
                used_intervals,
                candidate_usage,
                rng,
                item_index,
            )
            source_candidates = grouped[path]

        if (
            selected.kind == "video"
            and source_window is None
            and _extend_previous_source_item(items, path, end, score)
        ):
            previous = items[-1]
            used_intervals[path][-1] = (previous.source_start, previous.source_end)
            recent_ids = (recent_ids + [previous.candidate_id])[-8:]
            recent_paths = (recent_paths + [path])[-4:]
            continue
        candidate_usage[selected.id] = candidate_usage.get(selected.id, 0) + 1
        if selected.kind == "video":
            source_start, source_end = source_window or _video_window(
                selected, duration, source_candidates, rng
            )
            used_intervals.setdefault(path, []).append((source_start, source_end))
        else:
            source_start = 0.0
            source_end = duration
        items.append(
            TimelineItem(
                timeline_start=start,
                timeline_end=end,
                candidate_id=selected.id,
                path=path,
                kind=selected.kind,
                source_start=round(source_start, 4),
                source_end=round(source_end, 4),
                width=selected.width,
                height=selected.height,
                score=round(score, 4),
                focus_x=selected.focus_x,
                focus_y=selected.focus_y,
                motion=("zoom-in" if (variant_index + item_index) % 2 == 0 else "zoom-out")
                if selected.kind == "photo"
                else "none",
            )
        )
        recent_ids = (recent_ids + [selected.id])[-8:]
        recent_paths = (recent_paths + [path])[-4:]
    return _coalesce_adjacent_source_items(items)


def _clip_path_sequence(
    ordered_paths: list[str],
    grouped: dict[str, list[MediaCandidate]],
    slot_count: int,
    *,
    interleave: bool = False,
    rng: random.Random | None = None,
) -> list[str]:
    if slot_count <= len(ordered_paths):
        return ordered_paths[:slot_count]
    weights = [_source_clip_weight(grouped[path]) for path in ordered_paths]
    base_count, remainder = divmod(slot_count, len(ordered_paths))
    counts = [base_count for _path in ordered_paths]
    strongest = sorted(range(len(ordered_paths)), key=lambda index: weights[index], reverse=True)
    for index in strongest[:remainder]:
        counts[index] += 1
    if not interleave:
        return [
            path
            for path, count in zip(ordered_paths, counts, strict=False)
            for _index in range(count)
        ]
    rounds: list[list[str]] = []
    for round_index in range(max(counts, default=0)):
        paths = [
            path
            for path, count in zip(ordered_paths, counts, strict=False)
            if round_index < count
        ]
        if round_index % 2 == 1:
            paths.reverse()
        if rounds and paths and rounds[-1][-1] == paths[0]:
            different = next((index for index, path in enumerate(paths) if path != paths[0]), None)
            if different is not None:
                paths[0], paths[different] = paths[different], paths[0]
            elif len(rounds[-1]) > 1:
                previous = rounds[-1]
                different = next(
                    (index for index, path in enumerate(previous[:-1]) if path != paths[0]),
                    None,
                )
                if different is not None:
                    previous[-1], previous[different] = previous[different], previous[-1]
        rounds.append(paths)
    return [path for paths in rounds for path in paths]


def _source_clip_weight(candidates: list[MediaCandidate]) -> float:
    video_candidates = [candidate for candidate in candidates if candidate.kind == "video"]
    ranked = sorted(
        video_candidates or candidates,
        key=lambda candidate: (
            0.52 * candidate.quality
            + 0.24 * _candidate_action(candidate)
            + 0.16 * candidate.stability
            + 0.08 * candidate.face_score
        ),
        reverse=True,
    )
    top_score = sum(
        0.52 * candidate.quality
        + 0.24 * _candidate_action(candidate)
        + 0.16 * candidate.stability
        + 0.08 * candidate.face_score
        for candidate in ranked[:4]
    )
    capacity = max(1, len(video_candidates or candidates))
    return max(0.1, top_score) * min(3.0, capacity)


def _choose_clip_candidate(
    entries: list[tuple[str, MediaCandidate]],
    duration: float,
    target_intensity: float,
    style: OrderedStyle,
    recent_ids: list[str],
    recent_paths: list[str],
    used_intervals: dict[str, list[tuple[float, float]]],
    candidate_usage: dict[str, int],
    rng: random.Random,
    item_index: int,
) -> tuple[float, str, MediaCandidate, tuple[float, float] | None]:
    suitable = [
        (path, candidate)
        for path, candidate in entries
        if candidate.kind == "photo" or candidate.duration >= duration + 0.01
    ] or entries
    ranked = [
        (
            _clip_candidate_score(
                path,
                candidate,
                target_intensity,
                style,
                recent_ids,
                recent_paths,
                used_intervals,
                candidate_usage,
                item_index,
            ),
            path,
            candidate,
        )
        for path, candidate in suitable
    ]
    ranked.sort(key=lambda item: item[0], reverse=True)
    available: list[
        tuple[float, str, MediaCandidate, list[tuple[float, float]]]
    ] = []
    for score, path, candidate in ranked:
        windows = (
            []
            if candidate.kind == "photo"
            else _available_video_windows(
                candidate,
                duration,
                [entry for entry_path, entry in entries if entry_path == path],
                used_intervals.get(path, []),
            )
        )
        if candidate.kind == "photo" or windows:
            available.append((score, path, candidate, windows))
    shortlist = (available or [(score, path, candidate, []) for score, path, candidate in ranked])[
        : min(14, len(ranked))
    ]
    weights = [max(0.02, score) ** 3 for score, _, _, _ in shortlist]
    score, path, selected, windows = rng.choices(shortlist, weights=weights, k=1)[0]
    source_window = (
        _choose_available_window(
            windows,
            duration,
            rng,
            preferred_start=_preferred_video_start(selected, duration),
        )
        if windows
        else None
    )
    return score, path, selected, source_window


def _clip_candidate_score(
    path: str,
    candidate: MediaCandidate,
    target_intensity: float,
    style: OrderedStyle,
    recent_ids: list[str],
    recent_paths: list[str],
    used_intervals: dict[str, list[tuple[float, float]]],
    candidate_usage: dict[str, int],
    item_index: int,
) -> float:
    intensity_match = 1.0 - abs(candidate.intensity - target_intensity)
    beat_fit = _candidate_action(candidate) if target_intensity >= 0.62 else intensity_match
    novelty = 1.0
    if candidate.id in recent_ids:
        novelty -= 0.72
    if path in recent_paths[-1:]:
        novelty -= 0.42
    elif path in recent_paths:
        novelty -= 0.16
    usage_penalty = min(0.34, candidate_usage.get(candidate.id, 0) * 0.07)
    reuse_penalty = min(0.28, len(used_intervals.get(path, [])) * 0.018)
    overlap_penalty = 0.18 * _max_interval_overlap_ratio(
        candidate.start,
        candidate.end,
        used_intervals.get(path, []),
    )
    photo_bias = 0.0
    if candidate.kind == "photo":
        photo_bias = -0.16 if target_intensity >= 0.68 else 0.03
    ordered_score = _ordered_candidate_score(
        candidate,
        target_intensity,
        style.visual_mode,
        item_index,
    )
    return clamp(
        0.34 * ordered_score
        + 0.27 * candidate.quality
        + 0.18 * beat_fit
        + 0.21 * novelty
        + photo_bias
        - usage_penalty
        - reuse_penalty
        - overlap_penalty
    )


def _max_interval_overlap_ratio(
    start: float,
    end: float,
    intervals: list[tuple[float, float]],
) -> float:
    span = max(0.01, end - start)
    if not intervals:
        return 0.0
    return max(
        max(0.0, min(end, used_end) - max(start, used_start)) / span
        for used_start, used_end in intervals
    )


def _rank_ordered_candidates(
    candidates: list[MediaCandidate],
    target_intensity: float,
    mode: str,
    item_index: int,
) -> list[tuple[float, MediaCandidate]]:
    ranked = [
        (
            _ordered_candidate_score(
                candidate,
                target_intensity,
                mode,
                item_index,
            ),
            candidate,
        )
        for candidate in candidates
    ]
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked


def _choose_ordered_candidate(
    ranked: list[tuple[float, MediaCandidate]],
    candidate_usage: dict[str, int],
    _rng: random.Random,
) -> tuple[float, MediaCandidate]:
    adjusted = _adjusted_ordered_candidates(ranked, candidate_usage)
    return max(adjusted, key=lambda item: item[0])


def _choose_ordered_candidate_window(
    ranked: list[tuple[float, MediaCandidate]],
    duration: float,
    source_candidates: list[MediaCandidate],
    used_intervals: list[tuple[float, float]],
    candidate_usage: dict[str, int],
    rng: random.Random,
    *,
    minimum_source_start: float | None = None,
) -> tuple[float, MediaCandidate, tuple[float, float] | None]:
    adjusted = _adjusted_ordered_candidates(ranked, candidate_usage)
    available: list[tuple[float, MediaCandidate, list[tuple[float, float]]]] = []
    for score, candidate in adjusted:
        windows = (
            []
            if candidate.kind == "photo"
            else _available_video_windows(
                candidate,
                duration,
                source_candidates,
                used_intervals,
                minimum_source_start=minimum_source_start,
            )
        )
        if candidate.kind == "photo" or windows:
            available.append((score, candidate, windows))
    if not available and len(adjusted) < len(ranked):
        for rank, (score, candidate) in enumerate(
            ranked[len(adjusted) :],
            start=len(adjusted),
        ):
            adjusted_score = clamp(
                score
                - min(0.42, candidate_usage.get(candidate.id, 0) * 0.14)
                - rank * 0.018,
                0.02,
                1.0,
            )
            windows = (
                []
                if candidate.kind == "photo"
                else _available_video_windows(
                    candidate,
                    duration,
                    source_candidates,
                    used_intervals,
                    minimum_source_start=minimum_source_start,
                )
            )
            if candidate.kind == "photo" or windows:
                available.append((adjusted_score, candidate, windows))
    pool = available or [(score, candidate, []) for score, candidate in adjusted]
    score, selected, windows = max(pool, key=lambda item: item[0])
    source_window = (
        _choose_available_window(
            windows,
            duration,
            rng,
            preferred_start=_preferred_video_start(selected, duration),
        )
        if windows
        else None
    )
    return score, selected, source_window


def _adjusted_ordered_candidates(
    ranked: list[tuple[float, MediaCandidate]],
    candidate_usage: dict[str, int],
) -> list[tuple[float, MediaCandidate]]:
    if len(ranked) == 1:
        return ranked
    pool_size = min(len(ranked), max(3, min(9, round(len(ranked) * 0.45))))
    pool = ranked[:pool_size]
    adjusted: list[tuple[float, MediaCandidate]] = []
    for rank, (score, candidate) in enumerate(pool):
        usage_penalty = min(0.42, candidate_usage.get(candidate.id, 0) * 0.14)
        rank_penalty = rank * 0.018
        adjusted_score = clamp(score - usage_penalty - rank_penalty, 0.02, 1.0)
        adjusted.append((adjusted_score, candidate))
    return adjusted


def _hero_candidate_score(candidate: MediaCandidate) -> float:
    brightness_fit = 1.0 - min(1.0, abs(candidate.brightness - 0.56) / 0.56)
    aspect = candidate.width / max(1, candidate.height)
    vertical_fit = 1.0 - min(1.0, abs(aspect - 9 / 16) / 0.75)
    action = 1.0 - abs(_candidate_action(candidate) - 0.72)
    subject_fit = 0.55 + 0.45 * candidate.face_score
    video_bonus = 0.04 if candidate.kind == "video" else 0.0
    return clamp(
        0.30 * candidate.quality
        + 0.18 * candidate.sharpness
        + 0.18 * action
        + 0.10 * candidate.stability
        + 0.08 * subject_fit
        + 0.08 * brightness_fit
        + 0.04 * vertical_fit
        + video_bonus
    )


def _ordered_candidate_score(
    candidate: MediaCandidate,
    target_intensity: float,
    mode: str,
    item_index: int,
) -> float:
    intensity_match = 1.0 - abs(candidate.intensity - target_intensity)
    action = _candidate_action(candidate)
    if mode == "motion":
        return clamp(
            0.27 * candidate.quality
            + 0.36 * action
            + 0.18 * intensity_match
            + 0.13 * candidate.stability
            + 0.06 * candidate.face_score
        )
    if mode == "energy":
        return clamp(
            0.35 * candidate.quality
            + 0.35 * intensity_match
            + 0.12 * action
            + 0.12 * candidate.stability
            + 0.06 * candidate.face_score
        )
    if mode == "contrast":
        desired = 0.82 if item_index % 2 else 0.22
        return clamp(
            0.40 * candidate.quality
            + 0.34 * (1.0 - abs(action - desired))
            + 0.16 * candidate.stability
            + 0.10 * candidate.face_score
        )
    if mode == "director":
        return clamp(
            0.42 * candidate.quality
            + 0.22 * intensity_match
            + 0.16 * action
            + 0.13 * candidate.stability
            + 0.07 * candidate.face_score
        )
    return clamp(
        0.60 * candidate.quality
        + 0.14 * intensity_match
        + 0.08 * action
        + 0.12 * candidate.stability
        + 0.06 * candidate.face_score
    )


def _candidate_action(candidate: MediaCandidate) -> float:
    """Use the dense action peak while preserving media-v1 compatibility."""
    return max(candidate.intensity, candidate.action_score)


def _build_slots(
    audio_map: AudioMap,
    style: Style,
    *,
    min_slot_duration: float = 0.62,
) -> list[tuple[float, float]]:
    duration = audio_map.duration
    grid = _build_music_grid(audio_map)
    beat_period = grid.beat_period
    bar_duration = clamp(beat_period * 4.0, 1.2, 4.0)

    slots: list[tuple[float, float]] = []
    current = 0.0
    while duration - current > 0.04:
        energy = energy_at(audio_map, current + 0.1)
        energy_factor = 1.0
        if energy >= 0.72:
            energy_factor = 0.58
        elif energy >= 0.52:
            energy_factor = 0.82
        elif energy <= 0.25:
            energy_factor = 1.35
        if style.name == "Ramp":
            energy_factor *= 1.25 - 0.65 * (current / max(duration, 0.01))

        target_length = clamp(
            bar_duration * style.bars_per_shot * energy_factor,
            min_slot_duration,
            4.8,
        )
        target = min(duration, current + target_length)
        next_point = _snap_endpoint(
            current,
            target,
            grid,
            energy,
            min_spacing=min_slot_duration,
        )
        slots.append((round(current, 4), round(next_point, 4)))
        current = next_point
    return _merge_short_slots(slots, min_slot_duration)


def _merge_short_slots(
    slots: list[tuple[float, float]],
    min_slot_duration: float,
) -> list[tuple[float, float]]:
    if len(slots) <= 1:
        return slots
    merged: list[tuple[float, float]] = []
    for start, end in slots:
        if merged and end - start < min_slot_duration:
            previous_start, _previous_end = merged[-1]
            merged[-1] = (previous_start, end)
        else:
            merged.append((start, end))
    if len(merged) > 1 and merged[0][1] - merged[0][0] < min_slot_duration:
        first_start, _first_end = merged.pop(0)
        next_start, next_end = merged[0]
        merged[0] = (min(first_start, next_start), next_end)
    return merged


def _snap_endpoint(
    current: float,
    target: float,
    grid: MusicGrid,
    energy: float,
    *,
    min_spacing: float = 0.5,
) -> float:
    duration = grid.duration
    if duration - target < 0.35:
        return duration
    min_spacing = max(0.18, min_spacing)
    target_length = max(0.01, target - current)
    beat_period = grid.beat_period
    tolerance = _snap_tolerance(target_length, beat_period, min_spacing)
    events = _snap_events_for_slot(grid, target_length, energy)
    lower = current + min_spacing
    upper = min(duration, target + tolerance)
    usable = [
        (point, priority)
        for point, priority in events
        if lower <= point <= upper and abs(point - target) <= tolerance
    ]
    if usable:
        return min(
            usable,
            key=lambda event: abs(event[0] - target) - event[1] * beat_period * 0.2,
        )[0]
    future = [(point, priority) for point, priority in events if lower <= point < duration]
    if future:
        return min(
            future,
            key=lambda event: abs(event[0] - target) - event[1] * beat_period * 0.2,
        )[0]
    return duration


def _snap_tolerance(target_length: float, beat_period: float, min_spacing: float) -> float:
    if target_length <= beat_period * 0.75 or min_spacing <= 0.25:
        return max(0.055, min(0.14, beat_period * 0.36, target_length * 0.62))
    if target_length <= beat_period * 1.75:
        return max(0.08, min(0.22, beat_period * 0.38, target_length * 0.34))
    return max(0.1, min(0.34, beat_period * 0.46, target_length * 0.24))


def _snap_events_for_slot(
    grid: MusicGrid,
    target_length: float,
    energy: float,
) -> list[tuple[float, float]]:
    priorities: dict[float, float] = {}

    def add(values: list[float], priority: float) -> None:
        for value in values:
            key = round(value, 4)
            priorities[key] = max(priorities.get(key, 0.0), priority)

    if target_length <= grid.beat_period * 0.75 or energy >= 0.78:
        add(grid.onsets, 0.34)
        add(grid.half_beats, 0.31)
        add(grid.beats, 0.36)
        add(grid.downbeats, 0.46)
        add(grid.sections, 0.54)
    elif target_length <= grid.beat_period * 1.75:
        add(grid.beats, 0.38)
        add(grid.downbeats, 0.5)
        add(grid.sections, 0.58)
        add(grid.onsets, 0.18)
    else:
        add(grid.downbeats, 0.58)
        add(grid.sections, 0.68)
        add(grid.beats, 0.18)
    priorities.pop(round(0.0, 4), None)
    return sorted(priorities.items())


def _build_music_grid(audio_map: AudioMap) -> MusicGrid:
    duration = audio_map.duration
    beat_period = _estimate_beat_period(audio_map)
    beats = _repair_beats(audio_map, beat_period)
    half_beats = []
    if audio_map.beat_confidence >= 0.45:
        half_beats = _unique_times(
            [
                (left + right) / 2
                for left, right in zip(beats, beats[1:], strict=False)
            ],
            duration,
        )
    downbeats = _quantize_times(audio_map.downbeats, beats, duration) or beats[::4]
    first_downbeat_index = (
        min(range(len(beats)), key=lambda index: abs(beats[index] - downbeats[0]))
        if beats and downbeats
        else 0
    )
    strong_beats = _unique_times(
        [
            beat
            for index, beat in enumerate(beats)
            if (index - first_downbeat_index) % 2 == 0
        ],
        duration,
    )
    onset_anchors = _unique_times([*beats, *half_beats], duration)
    onset_tolerance = max(0.055, min(0.14, beat_period * 0.24))
    onsets = _quantize_times(
        audio_map.onsets,
        onset_anchors,
        duration,
        tolerance=onset_tolerance,
    )
    section_points = [
        point
        for section in audio_map.sections
        for point in (section.start, section.end)
    ]
    sections = _quantize_times(section_points, [*downbeats, *beats], duration)
    return MusicGrid(
        duration=duration,
        beat_period=beat_period,
        beats=beats,
        half_beats=half_beats,
        downbeats=downbeats,
        strong_beats=strong_beats,
        onsets=onsets,
        sections=sections,
    )


def _estimate_beat_period(audio_map: AudioMap) -> float:
    if len(audio_map.beats) >= 2:
        diffs = [
            right - left
            for left, right in zip(audio_map.beats, audio_map.beats[1:], strict=False)
            if 0.18 <= right - left <= 1.5
        ]
        if diffs:
            return float(clamp(float(np.median(diffs)), 0.18, 1.5))
    if audio_map.bpm > 0:
        return clamp(60.0 / audio_map.bpm, 0.18, 1.5)
    return 0.5


def _repair_beats(audio_map: AudioMap, beat_period: float) -> list[float]:
    """Keep detected beat phase and interpolate only clearly missing beats."""
    detected = _unique_times(audio_map.beats, audio_map.duration)
    if len(detected) < 2:
        return _fallback_beats(audio_map, beat_period)
    repaired: list[float] = [detected[0]]
    for left, right in zip(detected, detected[1:], strict=False):
        gap = right - left
        intervals = max(1, round(gap / beat_period))
        if intervals > 1 and gap >= beat_period * 1.65:
            repaired.extend(left + gap * index / intervals for index in range(1, intervals))
        repaired.append(right)
    return _unique_times(repaired, audio_map.duration)


def _fallback_beats(audio_map: AudioMap, beat_period: float) -> list[float]:
    duration = audio_map.duration
    if beat_period <= 0:
        return []
    origin = audio_map.beats[0] if audio_map.beats else 0.0
    while origin - beat_period >= 0:
        origin -= beat_period
    values: list[float] = []
    current = origin
    while current <= duration + 0.001:
        values.append(current)
        current += beat_period
    return values


def _quantize_times(
    values: list[float],
    anchors: list[float],
    duration: float,
    *,
    tolerance: float | None = None,
) -> list[float]:
    if not anchors:
        return []
    quantized: list[float] = []
    for value in values:
        nearest = min(anchors, key=lambda anchor: abs(anchor - value))
        if tolerance is None or abs(nearest - value) <= tolerance:
            quantized.append(nearest)
    return _unique_times(quantized, duration)


def _unique_times(values: list[float], duration: float) -> list[float]:
    rounded = {
        round(clamp(float(value), 0.0, duration), 4)
        for value in values
        if -0.001 <= float(value) <= duration + 0.001
    }
    return sorted(rounded)


def _fill_slots(
    slots: list[tuple[float, float]],
    audio_map: AudioMap,
    candidates: list[MediaCandidate],
    style: Style,
    rng: random.Random,
    *,
    allow_source_reuse: bool = True,
) -> list[TimelineItem]:
    items: list[TimelineItem] = []
    recent_ids: list[str] = []
    recent_paths: list[str] = []
    used_intervals: dict[str, list[tuple[float, float]]] = {}

    for slot_index, (start, end) in enumerate(slots):
        duration = end - start
        target_intensity = clamp(energy_at(audio_map, (start + end) / 2) + style.intensity_bias)
        suitable = [
            candidate
            for candidate in candidates
            if candidate.kind == "photo" or candidate.duration >= duration + 0.03
        ]
        if not suitable:
            suitable = candidates
        if not allow_source_reuse:
            unused = [candidate for candidate in suitable if candidate.path not in used_intervals]
            if unused:
                suitable = unused

        ranked = []
        for candidate in suitable:
            score = _candidate_score(
                candidate,
                target_intensity,
                style,
                recent_ids,
                recent_paths,
                used_intervals,
                slot_index,
            )
            ranked.append((score, candidate))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        available: list[tuple[float, MediaCandidate, list[tuple[float, float]]]] = []
        for score, candidate in ranked:
            same_source = [entry for entry in candidates if entry.path == candidate.path]
            windows = (
                []
                if candidate.kind == "photo"
                else _available_video_windows(
                    candidate,
                    duration,
                    same_source,
                    used_intervals.get(candidate.path, []),
                )
            )
            if candidate.kind == "photo" or windows:
                available.append((score, candidate, windows))
        if not available and items and items[-1].kind == "video":
            previous = items[-1]
            if _extend_previous_source_item(items, previous.path, end, previous.score):
                used_intervals[previous.path][-1] = (
                    previous.source_start,
                    previous.source_end,
                )
                recent_ids = (recent_ids + [previous.candidate_id])[-8:]
                recent_paths = (recent_paths + [previous.path])[-3:]
                continue
        shortlist = (
            available or [(score, candidate, []) for score, candidate in ranked]
        )[: min(12, len(ranked))]
        weights = [max(0.02, score) ** 3 for score, _, _ in shortlist]
        selected_score, selected, windows = rng.choices(shortlist, weights=weights, k=1)[0]

        if selected.kind == "video":
            same_source = [candidate for candidate in candidates if candidate.path == selected.path]
            source_start, source_end = (
                _choose_available_window(
                    windows,
                    duration,
                    rng,
                    preferred_start=_preferred_video_start(selected, duration),
                )
                if windows
                else _video_window(selected, duration, same_source, rng)
            )
            used_intervals.setdefault(selected.path, []).append((source_start, source_end))
        else:
            source_start = 0.0
            source_end = duration
            used_intervals.setdefault(selected.path, []).append((source_start, source_end))

        items.append(
            TimelineItem(
                timeline_start=start,
                timeline_end=end,
                candidate_id=selected.id,
                path=selected.path,
                kind=selected.kind,
                source_start=round(source_start, 4),
                source_end=round(source_end, 4),
                width=selected.width,
                height=selected.height,
                score=round(selected_score, 4),
                focus_x=selected.focus_x,
                focus_y=selected.focus_y,
                motion=("zoom-in" if slot_index % 2 == 0 else "zoom-out")
                if selected.kind == "photo"
                else "none",
            )
        )
        recent_ids = (recent_ids + [selected.id])[-8:]
        recent_paths = (recent_paths + [selected.path])[-3:]
    return _coalesce_adjacent_source_items(items)


def _candidate_score(
    candidate: MediaCandidate,
    target_intensity: float,
    style: Style,
    recent_ids: list[str],
    recent_paths: list[str],
    used_intervals: dict[str, list[tuple[float, float]]],
    slot_index: int,
) -> float:
    intensity_match = 1.0 - abs(candidate.intensity - target_intensity)
    novelty = 1.0
    if candidate.id in recent_ids:
        novelty -= 0.65
    if candidate.path in recent_paths[-1:]:
        novelty -= 0.32
    elif candidate.path in recent_paths:
        novelty -= 0.12
    overlap_count = len(used_intervals.get(candidate.path, []))
    reuse_penalty = min(0.35, overlap_count * 0.015)
    photo_bonus = style.photo_bias if candidate.kind == "photo" else 0.0
    if candidate.kind == "photo" and target_intensity > 0.72:
        photo_bonus -= 0.18
    contrast_bonus = 0.0
    if style.name == "Contrast":
        desired = 0.75 if slot_index % 2 == 0 else 0.25
        contrast_bonus = 0.12 * (1.0 - abs(candidate.intensity - desired))
    return clamp(
        0.40 * candidate.quality
        + 0.27 * intensity_match
        + 0.15 * novelty
        + 0.10 * candidate.stability
        + 0.05 * candidate.face_score
        + 0.03 * _candidate_action(candidate)
        + photo_bonus
        + contrast_bonus
        - reuse_penalty
    )


def _video_window(
    selected: MediaCandidate,
    duration: float,
    source_candidates: list[MediaCandidate],
    rng: random.Random,
) -> tuple[float, float]:
    """Fit a montage slot around a scored window without overrunning its source."""
    windows = _available_video_windows(selected, duration, source_candidates, [])
    if windows:
        return _choose_available_window(
            windows,
            duration,
            rng,
            preferred_start=_preferred_video_start(selected, duration),
        )
    source_start = min(candidate.start for candidate in source_candidates)
    source_end = max(candidate.end for candidate in source_candidates)
    if source_end - source_start < duration:
        return source_start, source_start + duration
    lower = max(source_start, selected.end - duration)
    upper = min(selected.start, source_end - duration)
    if upper < lower:
        lower = max(source_start, min(selected.start, source_end - duration))
        upper = lower
    start = lower + rng.random() * (upper - lower)
    return start, start + duration


def _available_video_windows(
    selected: MediaCandidate,
    duration: float,
    source_candidates: list[MediaCandidate],
    used_intervals: list[tuple[float, float]],
    *,
    minimum_source_start: float | None = None,
) -> list[tuple[float, float]]:
    source_start = min(candidate.start for candidate in source_candidates)
    source_end = max(candidate.end for candidate in source_candidates)
    if source_end - source_start < duration:
        return []
    if selected.duration >= duration:
        lower = max(source_start, selected.start)
        upper = min(selected.end - duration, source_end - duration)
    else:
        lower = max(source_start, selected.end - duration)
        upper = min(selected.start, source_end - duration)
    if upper < lower:
        lower = max(source_start, min(selected.start, source_end - duration))
        upper = lower
    if minimum_source_start is not None:
        lower = max(lower, minimum_source_start)
        if upper < lower:
            return []
    windows = [(lower, upper)]
    for used_start, used_end in used_intervals:
        forbidden_start = used_start - duration - SOURCE_SEAM_SECONDS
        forbidden_end = used_end + SOURCE_SEAM_SECONDS
        remaining: list[tuple[float, float]] = []
        for window_start, window_end in windows:
            if window_end < forbidden_start or window_start > forbidden_end:
                remaining.append((window_start, window_end))
                continue
            left_end = min(window_end, forbidden_start)
            right_start = max(window_start, forbidden_end)
            if left_end >= window_start:
                remaining.append((window_start, left_end))
            if right_start <= window_end:
                remaining.append((right_start, window_end))
        windows = remaining
        if not windows:
            break
    return windows


def _choose_available_window(
    windows: list[tuple[float, float]],
    duration: float,
    rng: random.Random,
    *,
    preferred_start: float | None = None,
) -> tuple[float, float]:
    if preferred_start is not None:
        lower, upper = min(
            windows,
            key=lambda window: abs(clamp(preferred_start, window[0], window[1]) - preferred_start),
        )
        source_start = clamp(preferred_start, lower, upper)
        return source_start, source_start + duration
    weights = [max(0.02, end - start) for start, end in windows]
    lower, upper = rng.choices(windows, weights=weights, k=1)[0]
    source_start = lower + rng.random() * max(0.0, upper - lower)
    return source_start, source_start + duration


def _preferred_video_start(candidate: MediaCandidate, duration: float) -> float | None:
    """Place a scored visual peak slightly before the middle of the montage shot."""
    if candidate.peak_time is None:
        return None
    return candidate.peak_time - duration * 0.42


def _coalesce_adjacent_source_items(items: list[TimelineItem]) -> list[TimelineItem]:
    if len(items) < 2:
        return items
    merged: list[TimelineItem] = []
    for item in items:
        previous = merged[-1] if merged else None
        if previous and _should_coalesce_source_items(previous, item):
            previous_duration = previous.duration
            item_duration = item.duration
            combined_duration = previous_duration + item_duration
            previous.timeline_end = item.timeline_end
            previous.source_end = round(previous.source_start + combined_duration, 4)
            previous.score = round(
                (previous.score * previous_duration + item.score * item_duration)
                / max(0.01, combined_duration),
                4,
            )
            continue
        merged.append(item)
    return merged


def _extend_previous_source_item(
    items: list[TimelineItem],
    path: str,
    timeline_end: float,
    score: float,
) -> bool:
    if not items or items[-1].kind != "video" or items[-1].path != path:
        return False
    previous = items[-1]
    previous_duration = previous.duration
    added_duration = timeline_end - previous.timeline_end
    if added_duration <= 0:
        return False
    combined_duration = previous_duration + added_duration
    previous.timeline_end = timeline_end
    previous.source_end = round(previous.source_start + combined_duration, 4)
    previous.score = round(
        (previous.score * previous_duration + score * added_duration)
        / max(0.01, combined_duration),
        4,
    )
    return True


def _should_coalesce_source_items(previous: TimelineItem, current: TimelineItem) -> bool:
    if previous.kind != "video" or current.kind != "video" or previous.path != current.path:
        return False
    if abs(previous.timeline_end - current.timeline_start) > 0.01:
        return False
    source_gap = current.source_start - previous.source_end
    if source_gap < -SOURCE_SEAM_SECONDS:
        return False
    combined_duration = previous.duration + current.duration
    maximum_gap = min(SOURCE_COALESCE_GAP_SECONDS, combined_duration * 0.4)
    return source_gap <= maximum_gap


def _variant_score(items: list[TimelineItem]) -> float:
    if not items:
        return 0.0
    average = float(np.mean([item.score for item in items]))
    unique_ratio = len({item.candidate_id for item in items}) / len(items)
    source_changes = sum(
        previous.path != current.path for previous, current in zip(items, items[1:], strict=False)
    ) / max(1, len(items) - 1)
    return clamp(0.72 * average + 0.20 * unique_ratio + 0.08 * source_changes)

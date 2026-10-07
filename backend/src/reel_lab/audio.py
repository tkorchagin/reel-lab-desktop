"""Automatic music markup with lightweight and optional ML analyzers."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import librosa
import numpy as np

from reel_lab.models import AudioMap, AudioSection, EnergyPoint
from reel_lab.system import clamp, read_json, run_command

SAMPLE_RATE = 22_050
HOP_LENGTH = 512
LOCAL_BEAT_PADDING = 8.0
LOCAL_BEAT_CONFIDENCE_FALLBACK = 0.45
OCTAVE_CANDIDATE_PENALTY = 0.16


@dataclass(frozen=True)
class LocalBeatAnalysis:
    """Beat events measured around one reel-sized excerpt."""

    bpm: float
    beats: tuple[float, ...]
    downbeats: tuple[float, ...]
    onsets: tuple[float, ...]
    confidence: float


def analyze_track(source: Path, work_dir: Path, *, analyzer: str = "auto") -> AudioMap:
    """Normalize and annotate an audio or video file.

    The automatic mode always computes dependable local features, then enriches
    them with All-In-One or Beat This when either executable is available.
    """
    source = source.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)

    work_dir.mkdir(parents=True, exist_ok=True)
    normalized = work_dir / "track.wav"
    _normalize_audio(source, normalized)
    audio_map = _analyze_librosa(source, normalized)

    selected = analyzer.lower()
    if selected not in {"auto", "librosa", "allin1", "beat-this"}:
        raise ValueError(f"Unknown analyzer: {analyzer}")
    if selected == "librosa":
        return audio_map

    attempts = [selected] if selected != "auto" else ["allin1", "beat-this"]
    for candidate in attempts:
        executable = "allin1" if candidate == "allin1" else "beat_this"
        if shutil.which(executable) is None:
            if selected != "auto":
                raise RuntimeError(f"Analyzer {executable} is not installed")
            continue
        try:
            if candidate == "allin1":
                return _enrich_with_allin1(audio_map, normalized, work_dir / "allin1")
            return _enrich_with_beat_this(audio_map, normalized, work_dir / "beat-this")
        except RuntimeError as error:
            if selected != "auto":
                raise
            audio_map.notes.append(f"{candidate} unavailable: {error}")

    audio_map.notes.append(
        "Used local librosa analysis; All-In-One and Beat This are not installed."
    )
    return audio_map


def energy_at(audio_map: AudioMap, time: float) -> float:
    """Return interpolated normalized energy at a timeline position."""
    if not audio_map.energy:
        return 0.5
    times = np.asarray([point.time for point in audio_map.energy])
    values = np.asarray([point.value for point in audio_map.energy])
    return float(np.interp(time, times, values))


def select_highlights(
    audio_map: AudioMap,
    durations: tuple[float, ...] = (15, 30, 60),
) -> dict[str, dict[str, float | str]]:
    """Choose a musically complete window for each requested reel duration."""
    highlights: dict[str, dict[str, float | str]] = {}
    for requested in durations:
        duration = min(float(requested), audio_map.duration)
        if duration <= 0:
            continue
        limit = max(0.0, audio_map.duration - duration)
        starts = [0.0, limit]
        starts.extend(min(limit, max(0.0, value)) for value in audio_map.downbeats)
        starts.extend(min(limit, max(0.0, section.start)) for section in audio_map.sections)
        candidates = _deduplicate_times(starts, minimum_gap=0.35)
        best_score, best_start = max(
            ((_highlight_score(audio_map, start, duration), start) for start in candidates),
            key=lambda item: item[0],
        )
        highlights[_highlight_key(requested)] = {
            "start": round(float(best_start), 3),
            "end": round(float(best_start + duration), 3),
            "duration": round(duration, 3),
            "score": round(float(best_score), 4),
            "label": "Best segment",
        }
    return highlights


def _highlight_key(duration: float) -> str:
    value = float(duration)
    return str(int(value)) if value.is_integer() else str(round(value, 1))


def slice_audio_map(audio_map: AudioMap, start: float, duration: float) -> AudioMap:
    """Return a timeline-local view of one highlighted part of a full track."""
    start = clamp(start, 0.0, audio_map.duration)
    end = clamp(start + duration, start, audio_map.duration)

    def shifted(values: list[float]) -> list[float]:
        return [round(value - start, 4) for value in values if start <= value <= end]

    sections = []
    for section in audio_map.sections:
        if section.end <= start or section.start >= end:
            continue
        sections.append(
            AudioSection(
                start=round(max(start, section.start) - start, 3),
                end=round(min(end, section.end) - start, 3),
                label=section.label,
                energy=section.energy,
            )
        )
    energy = [
        EnergyPoint(time=round(point.time - start, 3), value=point.value)
        for point in audio_map.energy
        if start <= point.time <= end
    ]
    local_duration = round(end - start, 4)
    bpm = audio_map.bpm
    beats = shifted(audio_map.beats)
    downbeats = shifted(audio_map.downbeats)
    onsets = shifted(audio_map.onsets)
    confidence = audio_map.beat_confidence
    notes = list(audio_map.notes)
    local_grid_used = False
    absolute_start = audio_map.source_start + start
    try:
        local_analysis = _local_beat_analysis(
            audio_map.normalized_path,
            absolute_start,
            local_duration,
        )
    except (OSError, RuntimeError, ValueError) as error:
        notes.append(f"Local model unavailable: {error}")
        confidence = 0.0
    else:
        if local_analysis is not None:
            bpm = local_analysis.bpm
            beats = list(local_analysis.beats)
            downbeats = list(local_analysis.downbeats)
            onsets = list(local_analysis.onsets)
            confidence = local_analysis.confidence
            local_grid_used = True
            notes.append(
                f"Local model: {bpm:.2f} BPM, confidence {confidence:.2f}."
            )
    edit_points = (
        _build_edit_points(local_duration, beats, downbeats, sections)
        if local_grid_used
        else shifted(audio_map.edit_points)
    )
    return AudioMap(
        source_path=audio_map.source_path,
        normalized_path=audio_map.normalized_path,
        analyzer=audio_map.analyzer,
        duration=local_duration,
        bpm=bpm,
        beats=beats,
        downbeats=downbeats,
        onsets=onsets,
        sections=sections,
        energy=energy,
        edit_points=edit_points,
        notes=notes,
        source_start=round(audio_map.source_start + start, 4),
        beat_confidence=confidence,
    )


def _local_beat_analysis(
    normalized_path: str,
    start: float,
    duration: float,
) -> LocalBeatAnalysis | None:
    """Load a padded excerpt and return a cached local beat grid."""
    path = Path(normalized_path).expanduser()
    if duration < 2.0 or not path.is_file():
        return None
    resolved = path.resolve()
    return _cached_local_beat_analysis(
        str(resolved),
        resolved.stat().st_mtime_ns,
        round(max(0.0, start), 3),
        round(duration, 3),
    )


@lru_cache(maxsize=256)
def _cached_local_beat_analysis(
    normalized_path: str,
    modified_ns: int,
    start: float,
    duration: float,
) -> LocalBeatAnalysis:
    """Analyze one excerpt; file mtime in the key invalidates stale results."""
    del modified_ns
    window_start = max(0.0, start - LOCAL_BEAT_PADDING)
    target_offset = start - window_start
    window_duration = duration + target_offset + LOCAL_BEAT_PADDING
    signal, sample_rate = librosa.load(
        normalized_path,
        sr=SAMPLE_RATE,
        mono=True,
        offset=window_start,
        duration=window_duration,
    )
    if len(signal) < sample_rate:
        raise RuntimeError("selected audio segment is too short")

    _harmonic, percussive = librosa.effects.hpss(signal)
    onset_envelope = librosa.onset.onset_strength(
        y=percussive,
        sr=sample_rate,
        hop_length=HOP_LENGTH,
        aggregate=np.median,
    )
    low_onset_envelope = librosa.onset.onset_strength(
        y=percussive,
        sr=sample_rate,
        hop_length=HOP_LENGTH,
        aggregate=np.median,
        fmax=1800,
        n_mels=64,
    )
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_envelope,
        sr=sample_rate,
        hop_length=HOP_LENGTH,
        units="frames",
        backtrack=False,
    )
    tempo_value, automatic_frames = librosa.beat.beat_track(
        onset_envelope=onset_envelope,
        sr=sample_rate,
        hop_length=HOP_LENGTH,
        trim=False,
    )
    automatic_bpm = (
        float(np.asarray(tempo_value).reshape(-1)[0]) if np.size(tempo_value) else 0.0
    )
    candidates = [(automatic_bpm, automatic_frames, 0.0)]
    for candidate_bpm in (automatic_bpm / 2.0, automatic_bpm * 2.0):
        if not 45.0 <= candidate_bpm <= 220.0:
            continue
        _tempo, candidate_frames = librosa.beat.beat_track(
            onset_envelope=onset_envelope,
            sr=sample_rate,
            hop_length=HOP_LENGTH,
            bpm=candidate_bpm,
            trim=False,
        )
        candidates.append((candidate_bpm, candidate_frames, OCTAVE_CANDIDATE_PENALTY))

    target_start_frame = int(librosa.time_to_frames(target_offset, sr=sample_rate))
    target_end_frame = int(librosa.time_to_frames(target_offset + duration, sr=sample_rate))
    ranked = []
    for bpm, frames, octave_penalty in candidates:
        if len(frames) < 2:
            continue
        aligned_frames = _align_beat_phase(
            frames,
            low_onset_envelope,
            target_start_frame,
            target_end_frame,
        )
        quality = _beat_candidate_quality(
            onset_envelope,
            aligned_frames,
            onset_frames,
            target_start_frame,
            target_end_frame,
            sample_rate,
        )
        ranked.append((quality - octave_penalty, bpm, aligned_frames))
    if not ranked:
        raise RuntimeError("no musical beats detected")
    confidence, _candidate_bpm, beat_frames = max(ranked, key=lambda item: item[0])
    confidence = clamp(confidence)

    local_beat_frames = beat_frames[
        (beat_frames >= target_start_frame) & (beat_frames <= target_end_frame)
    ]
    if confidence < LOCAL_BEAT_CONFIDENCE_FALLBACK:
        strong_onsets = _strong_local_onsets(
            onset_envelope,
            onset_frames,
            target_start_frame,
            target_end_frame,
        )
        if len(strong_onsets) >= 2:
            local_beat_frames = strong_onsets

    local_times = (
        librosa.frames_to_time(local_beat_frames, sr=sample_rate, hop_length=HOP_LENGTH)
        - target_offset
    )
    beats = _rounded(local_times)
    beat_diffs = np.diff(local_times)
    beat_diffs = beat_diffs[(beat_diffs >= 0.18) & (beat_diffs <= 1.5)]
    bpm = 60.0 / float(np.median(beat_diffs)) if len(beat_diffs) else automatic_bpm

    all_downbeats = _estimate_downbeats(local_beat_frames, onset_envelope, sample_rate)
    downbeats = _rounded(
        [
            value - target_offset
            for value in all_downbeats
            if target_offset <= value <= target_offset + duration
        ]
    )
    onset_times = (
        librosa.frames_to_time(onset_frames, sr=sample_rate, hop_length=HOP_LENGTH)
        - target_offset
    )
    onsets = _rounded([value for value in onset_times if 0.0 <= value <= duration])
    return LocalBeatAnalysis(
        bpm=round(float(bpm), 2),
        beats=tuple(beats),
        downbeats=tuple(downbeats),
        onsets=tuple(onsets),
        confidence=round(float(confidence), 4),
    )


def _beat_candidate_quality(
    onset_envelope: np.ndarray,
    beat_frames: np.ndarray,
    onset_frames: np.ndarray,
    target_start: int,
    target_end: int,
    sample_rate: int,
) -> float:
    """Score onset alignment, coverage, regularity, and excerpt coverage."""
    beats = beat_frames[(beat_frames >= target_start) & (beat_frames <= target_end)]
    if len(beats) < 2 or not len(onset_envelope):
        return 0.0
    strengths = onset_envelope[np.clip(beats, 0, len(onset_envelope) - 1)]
    reference = max(float(np.quantile(onset_envelope, 0.9)), 1e-8)
    strength_score = clamp(float(np.mean(strengths)) / reference)

    local_onsets = onset_frames[(onset_frames >= target_start) & (onset_frames <= target_end)]
    if len(local_onsets):
        onset_strengths = onset_envelope[np.clip(local_onsets, 0, len(onset_envelope) - 1)]
        strong = local_onsets[onset_strengths >= np.median(onset_strengths)]
        tolerance = max(1, round(0.09 * sample_rate / HOP_LENGTH))
        coverage = float(np.mean([np.min(np.abs(beats - frame)) <= tolerance for frame in strong]))
    else:
        coverage = 0.0

    intervals = np.diff(beats).astype(float)
    regularity = 1.0 - min(1.0, float(np.std(intervals)) / max(float(np.mean(intervals)), 1.0))
    target_seconds = max(0.0, (target_end - target_start) * HOP_LENGTH / sample_rate)
    completeness = min(1.0, len(beats) / max(2.0, target_seconds / 1.5))
    return 0.35 * strength_score + 0.30 * coverage + 0.25 * regularity + 0.10 * completeness


def _align_beat_phase(
    beat_frames: np.ndarray,
    low_onset_envelope: np.ndarray,
    target_start: int,
    target_end: int,
) -> np.ndarray:
    """Choose the beat or offbeat phase that best follows low-frequency attacks."""
    local = beat_frames[(beat_frames >= target_start) & (beat_frames <= target_end)]
    if len(local) < 3 or not len(low_onset_envelope):
        return beat_frames
    half_period = max(1, round(float(np.median(np.diff(local))) / 2.0))
    options = [beat_frames, beat_frames - half_period, beat_frames + half_period]

    def salience(frames: np.ndarray) -> float:
        usable = frames[(frames >= target_start) & (frames <= target_end)]
        if not len(usable):
            return 0.0
        values = [
            float(np.max(low_onset_envelope[max(0, frame - 1) : frame + 2]))
            for frame in usable
            if frame < len(low_onset_envelope)
        ]
        return float(np.mean(values)) if values else 0.0

    return max(options, key=salience)


def _strong_local_onsets(
    onset_envelope: np.ndarray,
    onset_frames: np.ndarray,
    target_start: int,
    target_end: int,
) -> np.ndarray:
    """Return separated strong attacks for low-confidence rhythm fallback."""
    local = onset_frames[(onset_frames >= target_start) & (onset_frames <= target_end)]
    if not len(local):
        return local
    strengths = onset_envelope[np.clip(local, 0, len(onset_envelope) - 1)]
    threshold = float(np.quantile(strengths, 0.6))
    ranked = sorted(
        (
            (float(onset_envelope[frame]), int(frame))
            for frame in local
            if onset_envelope[frame] >= threshold
        ),
        reverse=True,
    )
    minimum_gap = max(1, round(0.32 * SAMPLE_RATE / HOP_LENGTH))
    selected: list[int] = []
    for _strength, frame in ranked:
        if all(abs(frame - current) >= minimum_gap for current in selected):
            selected.append(frame)
    return np.asarray(sorted(selected), dtype=int)


def _highlight_score(audio_map: AudioMap, start: float, duration: float) -> float:
    end = start + duration
    values = [point.value for point in audio_map.energy if start <= point.time <= end]
    if not values:
        values = [0.5]
    split = max(1, len(values) // 3)
    early = float(np.mean(values[:split]))
    late = float(np.mean(values[-split:]))
    mean = float(np.mean(values))
    low, high = np.quantile(values, [0.1, 0.9])
    contrast = float(high - low)
    rise = clamp((late - early + 0.3) / 0.6)
    pre_energy = _mean_energy(audio_map.energy, max(0.0, start - 7.0), max(0.0, start - 1.0))
    post_energy = _mean_energy(audio_map.energy, start + 1.0, min(end, start + 5.0))
    start_lift = clamp((post_energy - pre_energy + 0.22) / 0.44)
    section_lift = _section_lift(audio_map.sections, start)
    start_fit = _boundary_fit(
        start,
        [section.start for section in audio_map.sections[1:]],
        tolerance=1.2,
    )
    peak_position = float(np.argmax(values)) / max(1, len(values) - 1)
    peak_shape = 1.0 - min(1.0, abs(peak_position - 0.68) / 0.68)
    boundaries = [section.start for section in audio_map.sections]
    boundaries.extend(section.end for section in audio_map.sections)
    end_fit = _boundary_fit(end, boundaries, tolerance=2.0)
    return (
        0.24 * start_lift
        + 0.18 * section_lift
        + 0.16 * contrast
        + 0.14 * rise
        + 0.12 * mean
        + 0.07 * peak_shape
        + 0.06 * start_fit
        + 0.03 * end_fit
    )


def _section_lift(sections: list[AudioSection], start: float) -> float:
    for index, section in enumerate(sections[1:], start=1):
        if abs(section.start - start) > 1.2:
            continue
        previous = sections[index - 1]
        return clamp((section.energy - previous.energy + 0.22) / 0.44)
    return 0.5


def _boundary_fit(time: float, boundaries: list[float], *, tolerance: float) -> float:
    distance = min((abs(time - value) for value in boundaries), default=tolerance)
    return 1.0 - min(1.0, distance / tolerance)


def _normalize_audio(source: Path, output: Path) -> None:
    run_command(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )


def _analyze_librosa(source: Path, normalized: Path) -> AudioMap:
    y, sample_rate = librosa.load(normalized, sr=SAMPLE_RATE, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sample_rate))
    if duration <= 0:
        raise RuntimeError("Audio track is empty")

    harmonic, percussive = librosa.effects.hpss(y)
    onset_envelope = librosa.onset.onset_strength(
        y=percussive,
        sr=sample_rate,
        hop_length=HOP_LENGTH,
        aggregate=np.median,
    )
    tempo_value, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_envelope,
        sr=sample_rate,
        hop_length=HOP_LENGTH,
        trim=False,
    )
    tempo = float(np.asarray(tempo_value).reshape(-1)[0]) if np.size(tempo_value) else 0.0
    beats = _rounded(librosa.frames_to_time(beat_frames, sr=sample_rate, hop_length=HOP_LENGTH))
    onset_times = librosa.onset.onset_detect(
        onset_envelope=onset_envelope,
        sr=sample_rate,
        hop_length=HOP_LENGTH,
        units="time",
        backtrack=False,
    )
    onsets = _rounded(onset_times)
    energy = _energy_curve(y, sample_rate)
    downbeats = _estimate_downbeats(beat_frames, onset_envelope, sample_rate)
    sections = _estimate_sections(
        harmonic,
        sample_rate,
        duration,
        beat_frames,
        beats,
        energy,
    )
    edit_points = _build_edit_points(duration, beats, downbeats, sections)

    return AudioMap(
        source_path=str(source),
        normalized_path=str(normalized.resolve()),
        analyzer="librosa",
        duration=round(duration, 4),
        bpm=round(tempo, 2),
        beats=beats,
        downbeats=downbeats,
        onsets=onsets,
        sections=sections,
        energy=energy,
        edit_points=edit_points,
    )


def _energy_curve(y: np.ndarray, sample_rate: int) -> list[EnergyPoint]:
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP_LENGTH)[0]
    times = librosa.times_like(rms, sr=sample_rate, hop_length=HOP_LENGTH)
    low, high = np.quantile(rms, [0.05, 0.95])
    spread = max(float(high - low), 1e-8)
    normalized = np.clip((rms - low) / spread, 0.0, 1.0)
    sample_count = min(320, len(times))
    indexes = np.linspace(0, len(times) - 1, sample_count, dtype=int)
    return [
        EnergyPoint(time=round(float(times[index]), 3), value=round(float(normalized[index]), 4))
        for index in indexes
    ]


def _estimate_downbeats(
    beat_frames: np.ndarray,
    onset_envelope: np.ndarray,
    sample_rate: int,
) -> list[float]:
    if len(beat_frames) < 4:
        return []
    strengths = onset_envelope[np.clip(beat_frames, 0, len(onset_envelope) - 1)]
    phase = max(range(4), key=lambda offset: float(np.mean(strengths[offset::4])))
    frames = beat_frames[phase::4]
    return _rounded(librosa.frames_to_time(frames, sr=sample_rate, hop_length=HOP_LENGTH))


def _estimate_sections(
    harmonic: np.ndarray,
    sample_rate: int,
    duration: float,
    beat_frames: np.ndarray,
    beats: list[float],
    energy: list[EnergyPoint],
) -> list[AudioSection]:
    if len(beat_frames) < 8 or duration < 8:
        middle = duration / 2
        boundaries = [0.0, middle, duration]
        return _sections_from_boundaries(boundaries, duration, energy)

    try:
        chroma = librosa.feature.chroma_cens(
            y=harmonic,
            sr=sample_rate,
            hop_length=HOP_LENGTH,
        )
        mfcc = librosa.feature.mfcc(
            y=harmonic,
            sr=sample_rate,
            n_mfcc=8,
            hop_length=HOP_LENGTH,
        )
        features = np.vstack((librosa.util.normalize(chroma), librosa.util.normalize(mfcc)))
        frame_boundaries = librosa.util.fix_frames(
            beat_frames,
            x_min=0,
            x_max=features.shape[1] - 1,
        )
        synchronized = librosa.util.sync(features, frame_boundaries, aggregate=np.median)
        section_count = min(max(2, round(duration / 22)), 12, synchronized.shape[1])
        segment_indexes = librosa.segment.agglomerative(synchronized, k=section_count)
        boundaries = [0.0]
        for index in segment_indexes:
            if 0 < index < len(beats):
                boundaries.append(beats[int(index)])
        boundaries.append(duration)
        boundaries = _deduplicate_times(boundaries, minimum_gap=2.0)
    except (ValueError, np.linalg.LinAlgError):
        boundaries = list(np.arange(0.0, duration, max(8.0, duration / 6)))
        boundaries.append(duration)
    return _sections_from_boundaries(boundaries, duration, energy)


def _sections_from_boundaries(
    boundaries: list[float],
    duration: float,
    energy: list[EnergyPoint],
) -> list[AudioSection]:
    boundaries = sorted({clamp(float(value), 0.0, duration) for value in boundaries})
    if not boundaries or boundaries[0] > 0:
        boundaries.insert(0, 0.0)
    if boundaries[-1] < duration:
        boundaries.append(duration)

    raw_energies = [
        _mean_energy(energy, start, end)
        for start, end in zip(boundaries, boundaries[1:], strict=False)
    ]
    median = float(np.median(raw_energies)) if raw_energies else 0.5
    sections: list[AudioSection] = []
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:], strict=False)):
        value = raw_energies[index]
        if index == 0:
            label = "intro"
        elif index == len(raw_energies) - 1:
            label = "outro"
        elif value >= max(0.68, median + 0.12):
            label = "high_energy"
        elif value <= min(0.32, median - 0.12):
            label = "low_energy"
        else:
            label = "body"
        sections.append(
            AudioSection(
                start=round(start, 3),
                end=round(end, 3),
                label=label,
                energy=round(value, 4),
            )
        )
    return sections


def _mean_energy(points: list[EnergyPoint], start: float, end: float) -> float:
    values = [point.value for point in points if start <= point.time < end]
    return float(np.mean(values)) if values else 0.5


def _build_edit_points(
    duration: float,
    beats: list[float],
    downbeats: list[float],
    sections: list[AudioSection],
) -> list[float]:
    points = [0.0, duration]
    points.extend(downbeats or beats[::4])
    points.extend(section.start for section in sections)
    points.extend(section.end for section in sections)
    points = _deduplicate_times(points, minimum_gap=0.25)
    if len(points) < 3:
        points = list(np.arange(0.0, duration, 2.0)) + [duration]
    return [round(value, 3) for value in points]


def _enrich_with_allin1(audio_map: AudioMap, track: Path, output_dir: Path) -> AudioMap:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_command(["allin1", str(track), "-o", str(output_dir), "-d", "cpu"])
    result_path = output_dir / f"{track.stem}.json"
    result = read_json(result_path)
    audio_map.analyzer = "allin1"
    audio_map.bpm = round(float(result.get("bpm", audio_map.bpm)), 2)
    audio_map.beats = _rounded(result.get("beats", audio_map.beats))
    audio_map.downbeats = _rounded(result.get("downbeats", audio_map.downbeats))
    parsed_sections = []
    for section in result.get("segments", []):
        start = float(section["start"])
        end = float(section["end"])
        parsed_sections.append(
            AudioSection(
                start=round(start, 3),
                end=round(end, 3),
                label=str(section.get("label", "section")),
                energy=round(_mean_energy(audio_map.energy, start, end), 4),
            )
        )
    if parsed_sections:
        audio_map.sections = parsed_sections
    audio_map.edit_points = _build_edit_points(
        audio_map.duration,
        audio_map.beats,
        audio_map.downbeats,
        audio_map.sections,
    )
    audio_map.notes.append("Song structure enriched with the All-In-One model.")
    return audio_map


def _enrich_with_beat_this(audio_map: AudioMap, track: Path, output_dir: Path) -> AudioMap:
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / f"{track.stem}.beats"
    run_command(
        [
            "beat_this",
            str(track),
            "-o",
            str(result_path),
            "--model",
            "small0",
            "--gpu",
            "-1",
        ]
    )
    beats: list[float] = []
    downbeats: list[float] = []
    for line in result_path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if not fields:
            continue
        timestamp = float(fields[0])
        beats.append(timestamp)
        if len(fields) > 1 and fields[1] == "1":
            downbeats.append(timestamp)
    audio_map.analyzer = "beat-this"
    audio_map.beats = _rounded(beats)
    audio_map.downbeats = _rounded(downbeats)
    if len(audio_map.beats) > 1:
        intervals = np.diff(audio_map.beats)
        plausible = intervals[(intervals >= 0.2) & (intervals <= 1.5)]
        if len(plausible):
            audio_map.bpm = round(60.0 / float(np.median(plausible)), 2)
    audio_map.edit_points = _build_edit_points(
        audio_map.duration,
        audio_map.beats,
        audio_map.downbeats,
        audio_map.sections,
    )
    audio_map.notes.append("Beats and downbeats refined with the Beat This model.")
    return audio_map


def _rounded(values: object) -> list[float]:
    array = np.asarray(values, dtype=float).reshape(-1)
    return [round(float(value), 4) for value in array if np.isfinite(value) and value >= 0]


def _deduplicate_times(values: list[float], *, minimum_gap: float) -> list[float]:
    result: list[float] = []
    for value in sorted(float(item) for item in values):
        if not result or value - result[-1] >= minimum_gap:
            result.append(value)
        elif value > result[-1]:
            result[-1] = value
    return result

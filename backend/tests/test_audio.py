import librosa
import numpy as np
import soundfile as sf

from reel_lab.audio import SAMPLE_RATE, _align_beat_phase, select_highlights, slice_audio_map
from reel_lab.models import AudioMap, AudioSection, EnergyPoint


def test_highlights_choose_a_complete_energetic_window() -> None:
    audio_map = AudioMap(
        source_path="track.mp3",
        normalized_path="track.wav",
        analyzer="test",
        duration=90.0,
        bpm=120.0,
        beats=[float(value) for value in range(0, 91, 1)],
        downbeats=[0.0, 16.0, 32.0, 48.0, 64.0, 80.0],
        onsets=[],
        sections=[
            AudioSection(0.0, 32.0, "intro", 0.2),
            AudioSection(32.0, 64.0, "high_energy", 0.9),
            AudioSection(64.0, 90.0, "outro", 0.4),
        ],
        energy=[
            EnergyPoint(float(value), 0.9 if 32 <= value <= 64 else 0.2) for value in range(91)
        ],
        edit_points=[0.0, 32.0, 64.0, 90.0],
    )

    highlights = select_highlights(audio_map)

    assert set(highlights) == {"15", "30", "60"}
    assert 30.0 <= highlights["15"]["start"] <= 49.0
    assert highlights["30"]["duration"] == 30.0
    assert highlights["60"]["end"] <= audio_map.duration


def test_highlights_prefer_energy_lift_at_section_start() -> None:
    audio_map = AudioMap(
        source_path="track.mp3",
        normalized_path="track.wav",
        analyzer="test",
        duration=90.0,
        bpm=120.0,
        beats=[float(value) for value in range(0, 91, 1)],
        downbeats=[0.0, 8.0, 16.0, 24.0, 32.0, 40.0, 48.0, 56.0, 64.0, 72.0],
        onsets=[],
        sections=[
            AudioSection(0.0, 24.0, "intro", 0.15),
            AudioSection(24.0, 50.0, "high_energy", 0.85),
            AudioSection(50.0, 75.0, "body", 0.85),
            AudioSection(75.0, 90.0, "outro", 0.3),
        ],
        energy=[
            EnergyPoint(float(value), 0.85 if 24 <= value < 75 else 0.15)
            for value in range(91)
        ],
        edit_points=[0.0, 24.0, 50.0, 75.0, 90.0],
    )

    highlights = select_highlights(audio_map, durations=(15,))

    assert highlights["15"]["start"] == 24.0


def test_slice_audio_map_preserves_source_offset() -> None:
    audio_map = AudioMap(
        source_path="track.mp3",
        normalized_path="track.wav",
        analyzer="test",
        duration=60.0,
        bpm=100.0,
        beats=[10.0, 11.0, 12.0],
        downbeats=[10.0],
        onsets=[11.0],
        sections=[AudioSection(0.0, 60.0, "body", 0.5)],
        energy=[EnergyPoint(10.0, 0.7)],
        edit_points=[10.0, 20.0],
        source_start=4.0,
    )

    sliced = slice_audio_map(audio_map, 10.0, 15.0)

    assert sliced.duration == 15.0
    assert sliced.source_start == 14.0
    assert sliced.beats == [0.0, 1.0, 2.0]
    assert sliced.edit_points == [0.0, 10.0]


def test_slice_audio_map_retracks_beats_around_selected_excerpt(tmp_path) -> None:
    duration = 28.0
    click_times = np.arange(0.25, duration, 0.5)
    samples = librosa.clicks(
        times=click_times,
        sr=SAMPLE_RATE,
        length=round(duration * SAMPLE_RATE),
    )
    normalized = tmp_path / "click-track.wav"
    sf.write(normalized, samples, SAMPLE_RATE)
    audio_map = AudioMap(
        source_path=str(normalized),
        normalized_path=str(normalized),
        analyzer="librosa",
        duration=duration,
        bpm=73.83,
        beats=[index * 0.81 for index in range(35)],
        downbeats=[index * 3.24 for index in range(9)],
        onsets=[],
        sections=[AudioSection(0.0, duration, "body", 0.5)],
        energy=[EnergyPoint(0.0, 0.5), EnergyPoint(duration, 0.5)],
        edit_points=[0.0, duration],
    )

    sliced = slice_audio_map(audio_map, 6.0, 15.0)

    expected = np.arange(0.25, 15.0, 0.5)
    distances = [min(abs(beat - value) for value in expected) for beat in sliced.beats]
    assert 115.0 <= sliced.bpm <= 125.0
    assert sliced.beat_confidence >= 0.65
    assert float(np.mean(distances)) <= 0.06


def test_beat_phase_prefers_low_frequency_pulse() -> None:
    beats = np.asarray([10, 30, 50, 70])
    low_onsets = np.zeros(100)
    low_onsets[[20, 40, 60, 80]] = 1.0

    aligned = _align_beat_phase(beats, low_onsets, 0, 90)

    assert aligned.tolist() == [20, 40, 60, 80]

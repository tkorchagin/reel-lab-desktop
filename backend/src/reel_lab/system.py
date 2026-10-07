"""Small wrappers around external media tools and JSON files."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def require_media_tools() -> None:
    """Raise a useful error when FFmpeg is unavailable."""
    missing = [name for name in ("ffmpeg", "ffprobe") if media_binary(name) is None]
    if missing:
        names = ", ".join(missing)
        raise RuntimeError(f"Required system tools not found: {names}")


def run_command(command: list[str], *, quiet: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command and include useful stderr in failures."""
    executable = (
        media_binary(command[0]) if command and command[0] in {"ffmpeg", "ffprobe"} else None
    )
    if executable:
        command = [executable, *command[1:]]
    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE if quiet else None,
        stderr=subprocess.PIPE if quiet else None,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        tail = "\n".join(stderr.splitlines()[-20:])
        raise RuntimeError(f"Command failed: {' '.join(command)}\n{tail}")
    return result


def media_binary(name: str) -> str | None:
    """Prefer the color-managed FFmpeg build without changing the user's PATH."""
    configured = os.environ.get(f"REEL_LAB_{name.upper()}")
    if configured and Path(configured).is_file():
        return configured
    full_binary = Path("/opt/homebrew/opt/ffmpeg-full/bin") / name
    if full_binary.is_file():
        return str(full_binary)
    return shutil.which(name)


def probe_media(path: Path) -> dict[str, Any]:
    """Read streams and container metadata using ffprobe."""
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(result.stdout)


def write_json(path: Path, value: Any) -> None:
    """Write stable, readable UTF-8 JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_json(path: Path) -> Any:
    """Read UTF-8 JSON."""
    return json.loads(path.read_text(encoding="utf-8"))


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    """Clamp a number to a range."""
    return max(minimum, min(maximum, value))

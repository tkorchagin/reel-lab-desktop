# Install and run Reel Lab

This file is for a coding agent helping someone run the desktop application locally. Follow the existing lockfiles, keep the user's data, and verify the actual server response before calling the setup complete.

## The target

Build the React frontend, start the FastAPI backend on `127.0.0.1:8765`, and open that address in the user's browser. A fresh local music library should receive the bundled CC0 starter track automatically.

This is a source installation with a browser interface, not a native desktop installer. macOS is the verified development platform; Linux and Windows need platform-specific prerequisite setup and have not been verified for this preview.

## Before installing

1. Locate this repository root: it contains `frontend/package-lock.json` and `backend/uv.lock`.
2. Check the OS and architecture. Inspect `uv --version`, `node --version`, `npm --version`, and available FFmpeg/ffprobe binaries.
3. Use Python 3.11 or newer. Node must satisfy the locked Vite package's engine requirement: `^20.19.0 || >=22.12.0`; a compatible Node LTS is a good choice.
4. Check whether port 8765 is already occupied. Do not kill an unrelated service. Choose another free localhost port and tell the user if necessary.
5. Keep `backend/data/` intact if it already exists. It contains private originals, music, projects, and exports. Do not reset or replace the database to make installation easier.

## System prerequisites

On macOS, if Homebrew is already installed, install only the missing tools from:

```bash
brew install uv node ffmpeg-full
```

Do not reinstall working prerequisites. If Homebrew is missing, use the [official uv installation guide](https://docs.astral.sh/uv/getting-started/installation/), [Node.js downloads](https://nodejs.org/en/download), and [FFmpeg downloads](https://ffmpeg.org/download.html), or explain the required system setup before proceeding.

FFmpeg must include `zscale` and `tonemap` for HDR-to-SDR export, plus H.264 encoding and AAC audio. On Apple Silicon, the backend prefers `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` and its ffprobe automatically. On other layouts, export `REEL_LAB_FFMPEG` and `REEL_LAB_FFPROBE` as absolute binary paths, or provide the correct binaries on PATH.

When using Homebrew, inspect the actual full build:

```bash
"$(brew --prefix ffmpeg-full)/bin/ffmpeg" -hide_banner -filters
"$(brew --prefix ffmpeg-full)/bin/ffprobe" -version
```

Confirm `zscale` and `tonemap` are in the filter list. Do not silently use a different `ffmpeg` on PATH.

## Install from the lockfiles

From the repository root:

```bash
cd frontend
npm ci
npm run build

cd ../backend
uv sync --locked
uv run alembic upgrade head
```

Use the project's dependencies. Do not replace the lockfiles or add optional analyzers just to start the app. There is no required API key, external account, music subscription, or large model download. YuNet weights and the starter soundtrack are included.

The server also applies migrations at startup. The explicit migration step above makes setup errors visible before launching the UI.

## Start and verify

From `backend/`:

```bash
uv run uvicorn reel_lab.app:app --host 127.0.0.1 --port 8765
```

Keep that process alive in a terminal or a managed session. Bind to localhost. Then use a second terminal:

```bash
curl --fail http://127.0.0.1:8765/api/health
curl --fail http://127.0.0.1:8765/api/music
```

The health response must be `{"status":"ok"}`. Music should include **Dance field** on a fresh installation; it may initially be queued or processing. Analysis can take a little longer on its first run. Poll until the track reports `ready`, or inspect its actual error and the server log if it fails.

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) in the user's browser and confirm the English Projects screen loads. Do not report success from a build alone. Do not upload or export the user's personal footage without their instruction; tell them the app is ready for their first project.

Report:

- the editor URL;
- the actual health response;
- whether the starter track is ready;
- where the running server is and how to stop it;
- any remaining limitation or failure.

## Later launches

From `backend/`, run the same `uv run uvicorn ...` command. Keep the terminal open while using Reel Lab. `Ctrl+C` stops the server. Rebuild the frontend only after frontend changes.

## Add more music

The starter track is approximately 54.9 seconds long. Use 15 or 30 seconds for the first reel. For a 60-second reel, add a track of at least that length.

Copy supported audio files into `backend/data/audio-library/`, then restart the server. MP3, M4A, AAC, WAV, FLAC, and OGG are supported; subfolders work. Files can be discovered without a catalog.

For titles, artist credit, ordering, and stable IDs, place a `catalog.json` beside the files:

```json
{
  "tracks": [
    {
      "id": "my-stable-track-id",
      "file": "my-track.mp3",
      "title": "My track",
      "artist": "Artist name",
      "order": 10
    }
  ]
}
```

Keep the same `id` if moving that file within the library; update its `file` value. The database UUID is preserved for project references. Users are responsible for choosing music they are allowed to use.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| UI says the frontend is not built | Run `npm ci` and `npm run build` from `frontend/`, then restart the backend. |
| Port is already in use | Identify the process; keep unrelated apps running and select another localhost port. |
| Audio stays queued or fails | Inspect the track's API error and backend log; verify the real FFmpeg binary and audio format. |
| HDR export fails on a missing filter | Verify `zscale` and `tonemap` in the configured FFmpeg full build. |
| Browser cannot connect | Confirm the server is still running and use its actual localhost port. |
| Old projects disappeared after clearing cookies | A new local identity was created. Files remain on disk; do not delete the database or storage. |
| Server was stopped during a job | Jobs run in-process and automatic restart recovery is not implemented. Explain the interruption rather than reporting a completed job. |

## Development only

For hot reload, run the backend on port 8000 and `npm run dev` in `frontend/`. Vite serves port 5173 and proxies `/api` to port 8000.

Existing checks:

```bash
cd backend
uv run ruff check .
uv run pytest

cd ../frontend
npm run lint
npm run build
```

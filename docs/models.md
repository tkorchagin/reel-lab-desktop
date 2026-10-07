# Models and optional analyzers

The ordinary desktop installation does not require a generative video model, a hosted inference API, or a GPU service.

The core visual pipeline uses OpenCV, PySceneDetect, Pillow, and measured image features. The core audio pipeline uses librosa. One small face detector helps choose a portrait crop.

## YuNet face detector

The existing application includes this file:

```text
backend/src/reel_lab/assets/face_detection_yunet_2026may.onnx
```

It comes from the official **[OpenCV Zoo YuNet model directory](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)**.

Verified local model:

```text
Size:    229738 bytes
SHA-256: ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0
```

This checksum matches the official repository's Git LFS pointer for the same model filename. Retaining the weights in the release makes a manual model download unnecessary.

If you need to restore the file, get that exact filename from the official model directory. Use GitHub's actual download for the weights rather than saving the small Git LFS pointer as an ONNX file. Check the size and checksum above before placing it at the expected path.

The detector identifies faces for crop anchoring. It does not identify people. If face detection is unavailable, the visual saliency fallback still provides a crop target.

YuNet has an **[upstream MIT license](https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/LICENSE)** with copyright attributed to Shiqi Yu. The full notice is preserved alongside the weights as [YUNET-LICENSE](../backend/src/reel_lab/assets/YUNET-LICENSE). Timofey's project license does not replace it.

## Audio: the regular installation

The standard dependency installation gives Reel Lab a local librosa pipeline for tempo, onsets, beats, energy, and track segmentation. No audio model download is required for that path.

Selected-excerpt beat checks are part of the local application. The best music excerpt is scored with energy, section changes, and musical boundaries; it should not be described as a model that understands or labels the chorus.

## Optional audio analyzers

The backend also contains adapters for **[Beat This!](https://github.com/CPJKU/beat_this)** and **[All-In-One](https://github.com/mir-aidj/all-in-one)**.

These are optional. The normal `uv sync --locked` command does not install them as standard project dependencies. The existing `advanced` extra lists Beat This, but that extra is not part of the first-run instructions.

An important detail: the current music service uses `analyzer="auto"`. It first computes the librosa features and can try an optional analyzer if its executable is already on PATH. Installing one changes the environment the automatic path sees. Optional tools may have their own weight downloads and runtime requirements.

For a first reel, use the regular installation. Follow each analyzer's official repository for its installation, checkpoint downloads, and licenses if you intentionally experiment with it. Optional analyzer installation and integration are not verified by this documentation preview.

## Other third-party components

FFmpeg is installed separately and invoked as an external executable. See its **[official licensing page](https://ffmpeg.org/legal.html)** and the license of the particular build you install. The macOS quick-start recommends **[Homebrew ffmpeg-full](https://formulae.brew.sh/formula/ffmpeg-full)** for its filter support.

Python and JavaScript dependencies retain their own licenses. The project license applies to the application's own code; music, source footage, screenshots containing personal media, model weights, and optional checkpoints have separate provenance.

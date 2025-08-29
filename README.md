# 2mpeg-dash

**Batch encoder + packager** that turns a folder of `.mp4` sources into **MPEG‑DASH (CMAF‑style)** outputs with **two video codecs**: **H.264 (AVC)** and **AV1**. It builds a multi‑bitrate ladder per source and emits a single `manifest.mpd` per input.

> This repo now ships a **Python orchestration script** (`2mpeg_dash.py`). It uses **FFmpeg** for encoding and **Shaka Packager** or **GPAC/MP4Box** for DASH packaging.

## Features

- **Multi‑codec**: H.264 (fallback‑friendly) + **AV1** (bandwidth‑efficient). AV1 is **optional**—if no AV1 encoder is available, the run continues with H.264 only.
- **Multi‑bitrate ladder** per source with automatic capping to the source height and optional `--max-height`.
- **Single pass per codec** with shared scaling graph → efficient.
- **CMAF‑style segmentation**: `init.mp4` + `seg_*.m4s` per Representation.
- Works on **macOS / Linux / Windows**.

## Requirements

- **Python** 3.8+
- **FFmpeg** + **ffprobe** in `$PATH`

  - For AV1: either **libsvtav1** (recommended on Apple Silicon) or **libaom‑av1** compiled in FFmpeg.

- One of the packagers:

  - **Shaka Packager** (`packager`) _or_
  - **GPAC / MP4Box** (`MP4Box`)

> The script auto‑detects encoders and packagers. If `packager` is present, it’s used; otherwise it falls back to `MP4Box`.

## Installation (quick)

### macOS (Homebrew)

```bash
brew update
brew install ffmpeg gpac               # MP4Box
# optional (alternative packager): install Shaka Packager from releases and add to PATH
```

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y ffmpeg gpac
# optional: install Shaka Packager from official releases and add to PATH
```

### Windows

- With **winget**:

  ```powershell
  winget install Gyan.FFmpeg GPAC.GPAC
  ```

- With **Chocolatey**:

  ```powershell
  choco install ffmpeg gpac -y
  ```

Verify:

```bash
ffmpeg -version
ffprobe -version
MP4Box -version   # or: packager -version
ffmpeg -encoders | grep -E "libsvtav1|libaom-av1"
```

## Usage

Put your sources in `./videos/`.

```bash
python3 main.py                                 # reads ./videos, writes ./out
python3 main.py --max-height 1440               # drop 2160p from the ladder
python3 main.py --seg 4 --audio-bitrate 192k
python3 main.py --av1-encoder svt --cpu-used 8  # faster AV1 on Apple Silicon
```

### CLI options

```
--input, -i       Input folder (default: ./videos)
--out, -o         Output root (default: ./out)
--work, -w        Work root for intermediate files (default: ./work)
--seg             Segment duration seconds (default: 4)
--audio-bitrate   AAC bitrate, e.g. 192k (default: 192k)
--preset264       x264 preset, e.g. slow|medium|veryfast (default: slow)
--av1-encoder     auto|aom|svt (default: auto)
--cpu-used        libaom speed 0..8 (default: 6; ignored for SVT‑AV1)
--max-height      Cap ladder, e.g. 1440 to exclude 2160p (default: 0 = no cap)
```

### What gets produced

For each input `videos/<name>.mp4`:

```
out/<name>/dash/
  ├── h264_1080/ (init.mp4, seg_1.m4s, ...)
  ├── h264_720/  (init.mp4, seg_1.m4s, ...)
  ├── h264_480/  (init.mp4, seg_1.m4s, ...)
  ├── av1_1080/  (init.mp4, seg_1.m4s, ...)   # if AV1 encoder present
  ├── av1_720/   (init.mp4, seg_1.m4s, ...)
  ├── av1_480/   (init.mp4, seg_1.m4s, ...)
  ├── audio/     (init.mp4, seg_1.m4s, ...)
  └── manifest.mpd
```

> Server MIME types you’ll need: `application/dash+xml` for `.mpd`, `video/iso.segment` for `.m4s`.

## Quick playback check (dash.js)

Create `test.html` next to the `out/` folder and serve statically:

```html
<!DOCTYPE html><meta charset="utf-8" />
<video id="v" controls playsinline style="width:100%;max-width:960px"></video>
<script src="https://cdn.dashjs.org/latest/dash.all.min.js"></script>
<script>
  const url = "out/CLIP_NAME/dash/manifest.mpd";
  const p = dashjs.MediaPlayer().create();
  p.initialize(document.getElementById("v"), url, true);
</script>
```

Serve:

```bash
python3 -m http.server 8080
# open http://localhost:8080/test.html
```

## How it works

- **Encoding**

  - **H.264**: single FFmpeg run per source, producing multiple resolutions; VBV is set via `br/maxrate/bufsize`; GOP \~2s (`-g` = `2*fps`).
  - **AV1**: single run per source using **SVT‑AV1** (if available) or **libaom‑av1**. CRF values are height‑aware.

- **Packaging**

  - **Shaka Packager**: generates `manifest.mpd` and CMAF‑style segments per Representation.
  - **MP4Box**: uses `-profile onDemand` and `-segment-name $RepresentationID$/seg_$Number$` to produce a similar layout.

## Tips & performance

- On **Apple Silicon**, prefer **SVT‑AV1**: `--av1-encoder svt --cpu-used 8` is a good starting point.
- For quick dry‑runs: `--preset264 veryfast` and `--av1-encoder svt --cpu-used 8`.
- To trim the ladder (e.g., skip 4K): `--max-height 1440`.

## Troubleshooting

- **AV1 files show up as H.264**

  - Ensure your FFmpeg actually has AV1 encoders: `ffmpeg -encoders | grep -E 'libsvtav1|libaom-av1'`.
  - This script sets `-c:v` individually for each output to avoid per‑stream index issues.

- **MP4Box errors about profile**

  - We use `-profile onDemand` (not `cmaf:onDemand`). The script already applies the correct option.

- **No audio in inputs**

  - The script will skip audio extraction if none is present.

- **iOS playback**

  - iOS prefers **HLS**; DASH isn’t natively supported in the system player. Use a JS player that supports DASH or generate an HLS variant as well (future enhancement).

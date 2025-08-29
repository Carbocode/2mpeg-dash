#!/usr/bin/env python3

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# -------------------- Config defaults --------------------
DEFAULT_LADDER = [2160, 1440, 1080, 720, 480]
# H.264 targets (VBV) and CRF per height
H264_PARAMS = {
    2160: dict(br="12000k", maxrate="12840k", bufsize="24000k", crf=19),
    1440: dict(br="7000k",  maxrate="7490k",  bufsize="14000k", crf=20),
    1080: dict(br="5000k",  maxrate="5350k",  bufsize="10000k", crf=20),
    720:  dict(br="2800k",  maxrate="2996k",  bufsize="5600k",  crf=21),
    480:  dict(br="1400k",  maxrate="1498k",  bufsize="2800k",  crf=22),
}
# AV1 CRF per height (libaom-av1/libsvtav1)
AV1_CRF = {2160:30, 1440:31, 1080:32, 720:33, 480:34}

# -------------------- Utils --------------------
def have(tool: str) -> bool:
    return shutil.which(tool) is not None

def run(cmd: List[str], cwd: Optional[Path]=None) -> None:
    print(">>", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=cwd)

def ffprobe_value(src: Path, entries: List[str], stream_sel: str) -> List[str]:
    cmd = [
        "ffprobe","-v","error",
        "-select_streams",stream_sel,
        "-show_entries",",".join(entries),
        "-of","default=nw=1:nk=1",
        str(src)
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip().splitlines()
    return [x.strip() for x in out if x.strip()]

def get_src_height(src: Path) -> int:
    try:
        vals = ffprobe_value(src, ["stream=height"], "v:0")
        return int(vals[0])
    except Exception:
        return 1080

def get_avg_fps(src: Path) -> float:
    try:
        vals = ffprobe_value(src, ["stream=avg_frame_rate"], "v:0")
        frac = vals[0]
        if "/" in frac:
            num, den = frac.split("/")
            den = float(den) if float(den) != 0 else 1.0
            return float(num)/den
        return float(frac)
    except Exception:
        return 25.0

def has_audio(src: Path) -> bool:
    try:
        vals = ffprobe_value(src, ["stream=index"], "a")
        return len(vals) > 0
    except Exception:
        return False

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

# -------------------- Encoding --------------------
def build_filter(heights: List[int], prefix: str) -> Tuple[str, List[str]]:
    """Return filter_complex and output labels for scaled streams."""
    n = len(heights)
    split_labels = [f"{prefix}{i}" for i in range(n)]
    split = "[0:v]split=" + str(n) + "".join([f"[{l}]" for l in split_labels]) + ";"
    scales = []
    out_labels = []
    for i, h in enumerate(heights):
        out_label = f"{prefix}{h}"
        scales.append(f"[{split_labels[i]}]scale=-2:{h}:flags=bicubic[{out_label}]")
        out_labels.append(out_label)
    filter_complex = split + "".join(s + ";" for s in scales)
    return filter_complex, out_labels

def encode_h264(src: Path, outdir: Path, heights: List[int], gop: int, preset: str) -> None:
    ensure_dir(outdir)
    filt, out_labels = build_filter(heights, "s")
    args = ["ffmpeg","-y","-i",str(src),"-filter_complex",filt]
    for h, label in zip(heights, out_labels):
        p = H264_PARAMS.get(h, dict(br="2500k", maxrate="2680k", bufsize="5000k", crf=21))
        args += [
            "-map", f"[{label}]",
            "-c:v", "libx264", "-preset", preset, "-pix_fmt", "yuv420p",
            "-crf", str(p["crf"]), "-profile:v", "high",
            "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
            "-b:v", p["br"], "-maxrate", p["maxrate"], "-bufsize", p["bufsize"],
            "-movflags", "+faststart",
            str(outdir / f"h264_{h}.mp4")
        ]
    run(args)

def encode_av1(src: Path, outdir: Path, heights: List[int], gop: int, encoder: str, cpu_used: int) -> None:
    ensure_dir(outdir)
    filt, out_labels = build_filter(heights, "t")
    args = ["ffmpeg","-y","-i",str(src),"-filter_complex",filt]
    for h, label in zip(heights, out_labels):
        crf = AV1_CRF.get(h, 32)
        if encoder == "svt":
            args += [
                "-map", f"[{label}]",
                "-c:v", "libsvtav1", "-pix_fmt", "yuv420p",
                "-crf", str(crf), "-g", str(gop),
                "-preset", "8",
                "-movflags", "+faststart",
                str(outdir / f"av1_{h}.mp4")
            ]
        else:
            args += [
                "-map", f"[{label}]",
                "-c:v", "libaom-av1", "-pix_fmt", "yuv420p",
                "-crf", str(crf), "-b:v", "0",
                "-g", str(gop), "-row-mt", "1", "-cpu-used", str(cpu_used),
                "-tile-columns", "1", "-tile-rows", "1",
                "-movflags", "+faststart",
                str(outdir / f"av1_{h}.mp4")
            ]
    run(args)

def extract_audio(src: Path, out_m4a: Path, aac_bitrate: str) -> Optional[Path]:
    if not has_audio(src):
        return None
    ensure_dir(out_m4a.parent)
    run(["ffmpeg","-y","-i",str(src),"-vn","-c:a","aac","-b:a",aac_bitrate,"-ac","2",str(out_m4a)])
    return out_m4a

# -------------------- Packaging --------------------
def package_shaka(outdash: Path, seg_dur: int,
                  v264_files: List[Tuple[int,Path]],
                  vav1_files: List[Tuple[int,Path]],
                  audio_file: Optional[Path]) -> None:
    ensure_dir(outdash)
    args = []
    for h,p in v264_files:
        segdir = outdash/f"h264_{h}"
        ensure_dir(segdir)
        args += [f"in={p},stream=video,init_segment={segdir/'init.mp4'},segment_template={segdir/'seg_$Number$.m4s'}"]
    for h,p in vav1_files:
        segdir = outdash/f"av1_{h}"
        ensure_dir(segdir)
        args += [f"in={p},stream=video,init_segment={segdir/'init.mp4'},segment_template={segdir/'seg_$Number$.m4s'}"]
    if audio_file and audio_file.exists():
        asegdir = outdash/"audio"
        ensure_dir(asegdir)
        args += [f"in={audio_file},stream=audio,lang=und,init_segment={asegdir/'init.mp4'},segment_template={asegdir/'seg_$Number$.m4s'}"]
    run(["packager", *args,
         "--segment_duration", str(seg_dur),
         "--generate_static_mpd",
         "--mpd_output", str(outdash/"manifest.mpd")])

def package_mp4box(outdash: Path, seg_dur: int,
                   v264_files: List[Tuple[int,Path]],
                   vav1_files: List[Tuple[int,Path]],
                   audio_file: Optional[Path]) -> None:
    ensure_dir(outdash)
    args = []
    # assegna RepresentationID espliciti per naming pulito
    for h,p in v264_files:
        args += [f"{p}#video:id=h264_{h}"]
    for h,p in vav1_files:
        args += [f"{p}#video:id=av1_{h}"]
    if audio_file and audio_file.exists():
        args += [f"{audio_file}#audio:id=audio"]

    run([
        "MP4Box",
        "-dash", str(seg_dur*1000),
        "-rap", "-frag", str(seg_dur*1000),
        "-profile", "live",
        "-segment-name", "$RepresentationID$/",
        "-segment-ext", "m4s",
        "-init-segment-ext", "mp4",
        "-no-frags-default",
        "-out", str(outdash/"manifest.mpd"),
        *args
    ])

# -------------------- Main --------------------
def main():
    ap = argparse.ArgumentParser(description="Encode H.264+AV1 ladders for MP4 files and package to MPEG-DASH (CMAF).")
    ap.add_argument("--input","-i",default="videos", help="Input folder (default: ./videos)")
    ap.add_argument("--out","-o",default="out", help="Output root (default: ./out)")
    ap.add_argument("--work","-w",default="temp", help="Work root for intermediate MP4s (default: ./temp)")
    ap.add_argument("--seg",type=int,default=4, help="Segment duration seconds (default: 4)")
    ap.add_argument("--audio-bitrate",default="192k", help="AAC bitrate (default: 192k)")
    ap.add_argument("--preset264",default="slow", help="x264 preset (default: slow)")
    ap.add_argument("--av1-encoder",choices=["auto","aom","svt"],default="auto", help="AV1 encoder (auto/aom/svt)")
    ap.add_argument("--cpu-used",type=int,default=6, help="AV1 libaom cpu-used (0 best .. 8 fastest)")
    ap.add_argument("--max-height",type=int,default=0, help="Cap ladder to this height (e.g., 1440 to drop 2160p). 0 = no cap")
    args = ap.parse_args()

    input_dir = Path(args.input)
    out_root  = Path(args.out)
    work_root = Path(args.work)

    if not have("ffmpeg") or not have("ffprobe"):
        sys.exit("Error: ffmpeg and ffprobe are required in PATH.")

    # Pick packager
    packager = "shaka" if have("packager") else ("mp4box" if have("MP4Box") else "")
    if not packager:
        sys.exit("Error: need Shaka Packager ('packager') or GPAC ('MP4Box') in PATH.")

    # Pick AV1 encoder
    if args.av1_encoder == "auto":
        encs = subprocess.run(["ffmpeg","-hide_banner","-encoders"], capture_output=True, text=True).stdout
        if "libsvtav1" in encs:
            av1_enc = "svt"
        elif "libaom-av1" in encs:
            av1_enc = "aom"
        else:
            av1_enc = "none"
    else:
        av1_enc = args.av1_encoder

    if av1_enc == "none":
        print("!! AV1 encoder not found in ffmpeg (libsvtav1/libaom-av1). Proceeding with H.264 only.")

    # Collect files
    files = sorted([p for p in input_dir.glob("*.mp4") if p.is_file()])
    if not files:
        print(f"No .mp4 files found in: {input_dir}")
        return

    print(f"Found {len(files)} file(s) in: {input_dir}")
    for src in files:
        base = src.stem
        gop = max(1, round(get_avg_fps(src)*2))
        h_src = get_src_height(src)

        # choose ladder entries
        ladder = [h for h in DEFAULT_LADDER if h <= h_src and (args.max_height == 0 or h <= args.max_height)]
        if not ladder:
            ladder = [h_src]

        work_dir = work_root/base
        v264_dir = work_dir/"h264"
        vav1_dir = work_dir/"av1"
        aud_dir  = work_dir/"audio"
        outdash  = out_root/base/"dash"
        ensure_dir(v264_dir); ensure_dir(vav1_dir); ensure_dir(aud_dir); ensure_dir(outdash)

        print(f"=== [{base}] src={h_src}p GOP={gop} seg={args.seg}s ladder={ladder} ===")

        # Encode H.264 ladder (one ffmpeg run)
        encode_h264(src, v264_dir, ladder, gop, args.preset264)
        # Encode AV1 ladder if available
        if av1_enc != "none":
            encode_av1(src, vav1_dir, ladder, gop, av1_enc, args.cpu_used)

        # Audio (optional)
        audio_path = extract_audio(src, aud_dir/"audio.m4a", args.audio_bitrate)

        # Prepare lists for packaging
        v264_files = [(h, v264_dir/f"h264_{h}.mp4") for h in ladder]
        vav1_files = [(h, vav1_dir/f"av1_{h}.mp4") for h in ladder if (vav1_dir/f"av1_{h}.mp4").exists()]

        # Package
        if packager == "shaka":
            package_shaka(outdash, args.seg, v264_files, vav1_files, audio_path)
        else:
            package_mp4box(outdash, args.seg, v264_files, vav1_files, audio_path)

        print(f"✔ Done: {outdash/'manifest.mpd'}")

    print("\nAll set! Server MIME: .mpd=application/dash+xml  .m4s=video/iso.segment")

if __name__ == "__main__":
    main()

"""Optional ffprobe/ffmpeg adapter. No shell and no synthesized constant FPS."""

from fractions import Fraction
from pathlib import Path
import shutil
import subprocess

from .input import Frame
from .util import file_hash, loads, digest, write_json


def decode_video(source, directory, context_seconds, max_frames, timeout):
    ffprobe, ffmpeg = shutil.which("ffprobe"), shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg:
        raise ValueError("MEDIA_TOOLS_MISSING: install ffprobe and ffmpeg or supply frames")
    video = (source.base / source.data["video_path"]).resolve()
    video_hash = file_hash(video)
    # Probe the selected stream's actual integer presentation timestamps.
    probe = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_frames", "-show_streams", "-show_entries",
            "stream=time_base:frame=pts", "-of", "json", str(video)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    if probe.returncode:
        raise ValueError("FFPROBE_FAILED")
    data = loads(probe.stdout.decode("utf-8"))
    time_base = Fraction(data["streams"][0]["time_base"])
    records = data.get("frames", [])
    pts = []
    for row in records:
        if "pts" not in row:
            raise ValueError("NATIVE_FRAME_PTS_MISSING")
        # Round source tick PTS once to input contract's microseconds, not seconds.
        pts.append(round(int(row["pts"]) * time_base * 1_000_000))
    if not pts or any(b <= a for a, b in zip(pts, pts[1:])):
        raise ValueError("NATIVE_PTS_NOT_STRICTLY_INCREASING")
    lower = source.start_pts_us - round(context_seconds * 1_000_000)
    upper = source.start_pts_us + round((source.duration + context_seconds) * 1_000_000)
    indices = [i for i, value in enumerate(pts) if lower <= value <= upper]
    if not indices:
        raise ValueError("NO_VIDEO_FRAMES_IN_WINDOW")
    if len(indices) > max_frames:
        raise ValueError("NATIVE_FRAME_LIMIT_EXCEEDED")
    first, last = indices[0], indices[-1]
    directory = Path(directory)
    directory.mkdir(parents=True)
    selection = "select=between(n\\,%d\\,%d),scale=320:-1" % (first, last)
    decoded = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-copyts", "-i", str(video),
            "-map", "0:v:0", "-vf", selection, "-vsync", "0", "-start_number", "0",
            str(directory / "frame_%08d.ppm")], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout, check=False)
    if decoded.returncode:
        raise ValueError("FFMPEG_DECODE_FAILED")
    paths = sorted(directory.glob("frame_*.ppm"))
    if len(paths) != len(indices):
        raise ValueError("NATIVE_FRAME_COUNT_MISMATCH")
    if file_hash(video) != video_hash:
        raise ValueError("VIDEO_CHANGED_DURING_DECODE")
    sequence = digest({"video_sha256": video_hash, "stream": 0,
                       "time_base": str(time_base), "adapter": "ffmpeg-native-v1"})
    source.frames = []
    for index, path in zip(indices, paths):
        actual = file_hash(path)
        frame = Frame("native_%08d" % index, pts[index],
                      (pts[index] - source.start_pts_us) / 1_000_000,
                      path, actual, index, sequence,
                      int(records[index]["pts"]), str(time_base))
        source.frames.append(frame)
        source.asset_hashes.append({"kind": "decoded_frame", **frame.ref()})
    source.asset_hashes.append({"kind": "video", "sha256": video_hash,
                               "time_base": str(time_base), "decoded_first_index": first,
                               "decoded_last_index": last})
    write_json(directory / "pts_index.json", {"schema": "adseg-native-pts-v1", "video_sha256": video_hash,
               "time_base": str(time_base), "stream": 0, "native_sequence": sequence,
               "frames": [frame.ref() for frame in source.frames],
               "pts_us_rounding": "nearest integer microsecond from exact rational ticks; original ticks retained"})

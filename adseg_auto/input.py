"""Manifest ingestion; source identity is validated before any run is created."""

from dataclasses import dataclass, field
from pathlib import Path

from .util import load, number, text, file_hash


@dataclass
class Frame:
    frame_id: str
    pts_us: int
    time: float
    path: Path
    sha256: str
    native_index: object = None
    native_sequence: object = None
    native_pts_ticks: object = None
    native_time_base: object = None

    def ref(self):
        return {"frame_id": self.frame_id, "pts_us": self.pts_us,
                "time": self.time, "sha256": self.sha256,
                "native_index": self.native_index,
                "native_sequence": self.native_sequence,
                "native_pts_ticks": self.native_pts_ticks,
                "native_time_base": self.native_time_base}


@dataclass
class Source:
    source_id: str
    channel: str
    duration: float
    start_pts_us: int
    data: dict
    base: Path
    frames: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    asset_hashes: list = field(default_factory=list)
    asr: list = field(default_factory=list)
    coarse: list = field(default_factory=list)


def read_manifest(path):
    path = Path(path).resolve()
    data = load(path)
    records = data.get("sources") if isinstance(data, dict) else data
    if not isinstance(records, list) or not records:
        raise ValueError("manifest must contain a nonempty sources list")
    sources, ids = [], set()
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("source must be an object")
        sid = text(item.get("source_id"), "source_id")
        if sid in ids:
            raise ValueError("duplicate source_id: " + sid)
        ids.add(sid)
        channel = text(item.get("channel"), "channel")
        duration = number(item.get("duration"), "duration", positive=True)
        start = item.get("window_start_pts_us", 0)
        # A broken evidence coordinate is a source failure, not a lost source.
        source = Source(sid, channel, duration, start, item, path.parent)
        if type(start) is not int:
            source.errors.append({"code": "INVALID_PTS_ORIGIN"})
        sources.append(source)
    synthetic = isinstance(data, dict) and data.get("synthetic") is True
    return sources, synthetic


def ingest(source):
    """Use only explicitly named evidence, never discover adjacent files."""
    if source.errors:
        return
    try:
        frames = source.data.get("frames", [])
        context = source.data.get("context", {})
        if not isinstance(context, dict):
            raise ValueError("context must be an object")
        records = frames + context.get("frames", [])
        seen, pts_seen = set(), set()
        for item in records:
            fid = text(item.get("frame_id"), "frame_id")
            pts = item.get("pts_us")
            if type(pts) is not int or fid in seen or pts in pts_seen:
                raise ValueError("frame IDs and integer PTS must be unique")
            seen.add(fid)
            pts_seen.add(pts)
            path = (source.base / text(item.get("path"), "frame path")).resolve()
            try:
                actual = file_hash(path)
                expected = text(item.get("sha256"), "frame sha256")
                if expected.lower() != actual:
                    raise ValueError("hash mismatch")
            except (OSError, ValueError):
                source.errors.append({"code": "FRAME_UNAVAILABLE_OR_HASH_MISMATCH", "frame_id": fid})
                continue
            # The only conversion of pre-extracted PTS into window seconds.
            time = (pts - source.start_pts_us) / 1_000_000
            number(time, "relative frame time")
            source.frames.append(Frame(fid, pts, time, path, actual))
            source.asset_hashes.append({"kind": "frame", "frame_id": fid, "sha256": actual})
        source.frames.sort(key=lambda frame: frame.pts_us)
        for kind, target in (("asr", source.asr), ("coarse_segments", source.coarse)):
            records = list(source.data.get(kind, []))
            if kind == "asr":
                records += context.get("asr", [])
            for item in records:
                start = number(item.get("start"), kind + ".start")
                end = number(item.get("end"), kind + ".end")
                if end <= start:
                    raise ValueError("evidence interval must have positive length")
                if kind == "asr":
                    target.append({"start": start, "end": end, "text": text(item.get("text"), "ASR text")})
                else:
                    # These labels are suggestions, never classification authority.
                    target.append({"start": start, "end": end,
                                   "segment_id": text(item.get("segment_id"), "coarse segment_id"),
                                   "type": text(item.get("type"), "coarse type")})
            target.sort(key=lambda row: row["start"])
    except (ValueError, TypeError, AttributeError, KeyError, OSError, OverflowError):
        source.errors.append({"code": "MALFORMED_SOURCE_EVIDENCE"})

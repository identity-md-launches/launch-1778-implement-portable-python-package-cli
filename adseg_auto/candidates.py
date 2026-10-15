"""High-recall proposals. No cue in this module decides identity or class."""

from dataclasses import dataclass
import math

from .images import descriptor, difference
from .util import sample


@dataclass
class Candidate:
    candidate_id: str
    time: float
    cues: list


def generate(source, config):
    proposals = {}

    def add(time, cue):
        if 0 < time < source.duration:
            proposals.setdefault(time, set()).add(cue)

    # Integer multiples avoid accumulated float drift. No minimum unit length.
    quotient = source.duration / config.probe_interval
    if not math.isfinite(quotient) or quotient > config.max_candidates + 1:
        raise ValueError("candidate limit exceeded by regular probes")
    steps = int(quotient)
    for index in range(1, steps + 1):
        add(index * config.probe_interval, "regular_probe")
    for segment in source.coarse:
        add(segment["start"], "coarse_edge")
        add(segment["end"], "coarse_edge")
    for row in source.asr:
        add(row["start"], "asr_edge")
        add(row["end"], "asr_edge")
    previous, previous_desc = None, None
    for frame in source.frames:
        try:
            desc = descriptor(frame.path)
        except (ValueError, OSError, IndexError, OverflowError):
            desc = None
        if previous is not None:
            if desc is not None and previous_desc is not None:
                change = difference(previous_desc, desc)
                if change >= config.image_change_threshold:
                    add(frame.time, "image_change")
            elif frame.sha256 != previous.sha256:
                # Compressed-byte difference is deliberately just a proposal.
                add(frame.time, "image_hash_change_fallback")
        previous, previous_desc = frame, desc
    if len(proposals) > config.max_candidates:
        raise ValueError("candidate limit exceeded; evidence was not silently pruned")
    return [Candidate("c%06d" % index, time, sorted(proposals[time]))
            for index, time in enumerate(sorted(proposals), 1)]


def evidence(source, start, end, focus, radius, max_frames):
    """Ordered before/during/after plus sampled whole-unit context.

    All displayed frames have actual evidence references. Local neighbors survive
    sampling so a short unit isn't removed simply by context downsampling.
    """
    lo, hi = start - radius, end + radius
    available = [f for f in source.frames if lo <= f.time <= hi]
    before = [f for f in available if f.time < start]
    during = [f for f in available if start <= f.time < end]
    after = [f for f in available if f.time >= end]
    neighbors_before = [f for f in available if f.time < focus][-3:]
    neighbors_after = [f for f in available if f.time >= focus][:3]
    mandatory = {f.frame_id: f for f in neighbors_before + neighbors_after}
    if during:
        mandatory[during[0].frame_id] = during[0]
        mandatory[during[-1].frame_id] = during[-1]
    for f in sample(available, max(2, max_frames - len(mandatory))):
        mandatory[f.frame_id] = f
    selected = sorted(mandatory.values(), key=lambda f: f.pts_us)
    selected_ids = {f.frame_id for f in selected}
    def refs(items):
        return [f.ref() for f in items if f.frame_id in selected_ids]
    return {"context_interval": [lo, hi],
            "before": refs(before), "during": refs(during), "after": refs(after),
            "ordered_frames": [f.ref() for f in selected],
            "asr": [r for r in source.asr if r["end"] > lo and r["start"] < hi],
            "coarse_hints": [r for r in source.coarse if r["end"] > lo and r["start"] < hi],
            "outside_window_note": "Outside evidence informs identity only; scored duration is unchanged.",
            "available_frame_count": len(available), "selected_frame_count": len(selected),
            "sampling": "ordered endpoint/uniform samples plus focus neighbors"}, selected

"""Joint release matcher.

Implements the two-stage matcher from joint_matching_spec.md:

* Stage 1: pick the best release(s) from the pooled AcoustID candidates by
  summing each file's best per-track contribution within that release.
* Stage 2: assign files to tracks within each chosen release via maximum-weight
  bipartite matching (scipy.optimize.linear_sum_assignment).

Pure functions; no I/O. The orchestrator in `service.py` handles the AcoustID
fan-out and the per-release MusicBrainz fetch.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

from .matcher import (
    FILE_COMPARISON_WEIGHTS,
    score_file_track_on_release,
)
from .models import AudioMetadata, ReleaseTrack

# A score of 0 in the "no candidate" cell of the cost matrix should never beat a
# real match; using a large negative cost (= large positive distance from min)
# keeps linear_sum_assignment from picking those cells when avoidable.
_NO_MATCH_SCORE = -1.0


@dataclass(frozen=True, slots=True)
class FileCandidates:
    source_id: str
    metadata: AudioMetadata | None
    recordings: tuple[dict, ...]


@dataclass(frozen=True, slots=True)
class Thresholds:
    min_per_file_score: float = 0.5
    min_coverage: float = 0.6
    split_margin: float = 0.15


@dataclass(frozen=True, slots=True)
class ReleaseSelection:
    release_id: str
    joint_score: float
    file_ids: tuple[str, ...]
    per_file_best: dict[str, float]


@dataclass(frozen=True, slots=True)
class FileTrackAssignment:
    source_id: str
    track_id: str
    recording_id: str
    score: float


@dataclass(frozen=True, slots=True)
class ReleaseAssignment:
    release_id: str
    joint_score: float
    assignments: tuple[FileTrackAssignment, ...]
    unmatched_file_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FallbackAssignment:
    source_id: str
    release_id: str | None
    track_id: str | None
    recording_id: str | None
    score: float
    reason: str | None


@dataclass(frozen=True, slots=True)
class StageOneResult:
    selections: tuple[ReleaseSelection, ...]
    unmatched_file_ids: tuple[str, ...]
    candidate_releases_considered: int
    split_count: int


@dataclass
class _ReleaseCandidate:
    release_id: str
    snapshot: dict
    country_rank: int
    format_rank: int
    date: str
    per_file_score: dict[str, float] = field(default_factory=dict)


def _country_rank(release: dict, preferred_countries: Sequence[str]) -> int:
    """Lower is better. Preferred entries rank by their position; absent = +inf."""
    country = release.get("country", "")
    if not country:
        return len(preferred_countries) + 2
    try:
        return list(preferred_countries).index(country)
    except ValueError:
        return len(preferred_countries) + 1


def _format_rank(release: dict) -> int:
    """Stable, deterministic rank for media format (lower = better preference).

    Preference order mirrors the typical Picard default. Anything else falls
    after the named formats but before "no media" data.
    """
    media = release.get("media") or []
    formats = [str(m.get("format") or "") for m in media if m.get("format")]
    if not formats:
        return 99
    preferred = ["CD", "Digital Media", "12\" Vinyl", "Vinyl", "Cassette", "DVD"]
    best = 99
    for fmt in formats:
        rank = preferred.index(fmt) if fmt in preferred else len(preferred)
        best = min(best, rank)
    return best


def _enumerate_release_candidates(
    files: Sequence[FileCandidates],
    preferred_countries: Sequence[str],
    weights: dict[str, int],
) -> tuple[dict[str, _ReleaseCandidate], dict[str, dict[str, dict | None]]]:
    """For each release id seen on any file's recordings, record the best
    per-file score and a snapshot of the release dict.

    Returns (release_candidates_by_id, file_id -> {release_id -> recording_dict_used}).
    """
    by_id: dict[str, _ReleaseCandidate] = {}
    used_recording_per_file: dict[str, dict[str, dict | None]] = {
        f.source_id: {} for f in files
    }

    for file in files:
        if file.metadata is None:
            continue
        for recording in file.recordings:
            for release in recording.get("releases") or []:
                rid = release.get("id")
                if not rid:
                    continue
                score = score_file_track_on_release(
                    file.metadata,
                    recording,
                    release,
                    weights,
                    list(preferred_countries) if preferred_countries else None,
                )
                cand = by_id.get(rid)
                if cand is None:
                    cand = _ReleaseCandidate(
                        release_id=rid,
                        snapshot=release,
                        country_rank=_country_rank(release, preferred_countries),
                        format_rank=_format_rank(release),
                        date=str(release.get("date") or ""),
                    )
                    by_id[rid] = cand
                else:
                    new_country_rank = _country_rank(release, preferred_countries)
                    if new_country_rank < cand.country_rank:
                        cand.country_rank = new_country_rank
                        cand.snapshot = release

                previous = cand.per_file_score.get(file.source_id, 0.0)
                if score > previous:
                    cand.per_file_score[file.source_id] = score
                    used_recording_per_file[file.source_id][rid] = recording
                elif file.source_id not in cand.per_file_score:
                    cand.per_file_score[file.source_id] = 0.0

    return by_id, used_recording_per_file


def _pick_primary(
    candidates: dict[str, _ReleaseCandidate],
    file_ids: Sequence[str],
) -> _ReleaseCandidate | None:
    if not candidates:
        return None
    file_id_set = set(file_ids)

    def joint(c: _ReleaseCandidate) -> float:
        return sum(s for sid, s in c.per_file_score.items() if sid in file_id_set)

    def sort_key(c: _ReleaseCandidate) -> tuple[float, int, int, str, str]:
        # Highest joint score first; deterministic tie-break.
        return (-joint(c), c.country_rank, c.format_rank, c.date, c.release_id)

    return min(candidates.values(), key=sort_key)


def select_releases(
    files: Sequence[FileCandidates],
    preferred_countries: Sequence[str],
    thresholds: Thresholds,
    weights: dict[str, int] = FILE_COMPARISON_WEIGHTS,
    max_split_depth: int = 4,
) -> StageOneResult:
    """Stage 1: pick release(s) by joint score with optional split + coverage rules."""
    files = sorted(files, key=lambda f: f.source_id)
    file_ids = tuple(f.source_id for f in files)
    candidates, _used = _enumerate_release_candidates(
        files, preferred_countries, weights
    )

    selections: list[ReleaseSelection] = []
    routed: set[str] = set()
    split_count = 0

    remaining_file_ids = list(file_ids)
    depth = 0
    while remaining_file_ids and depth < max_split_depth:
        primary = _pick_primary(candidates, remaining_file_ids)
        if primary is None:
            break
        joint_score = sum(
            primary.per_file_score.get(sid, 0.0) for sid in remaining_file_ids
        )
        if joint_score <= 0.0:
            break

        # Split rule: files in remaining whose best-on-primary is below
        # min_per_file_score AND who score meaningfully better elsewhere are
        # held back for the next iteration.
        kept: list[str] = []
        held: list[str] = []
        for sid in remaining_file_ids:
            on_primary = primary.per_file_score.get(sid, 0.0)
            best_other = 0.0
            for rid, cand in candidates.items():
                if rid == primary.release_id:
                    continue
                best_other = max(best_other, cand.per_file_score.get(sid, 0.0))
            if (
                on_primary < thresholds.min_per_file_score
                and best_other - on_primary >= thresholds.split_margin
                and best_other >= thresholds.min_per_file_score
            ):
                held.append(sid)
            else:
                kept.append(sid)

        if not kept:
            # Everyone wants to go elsewhere; treat the held set as the new
            # remaining and recurse without recording this primary.
            remaining_file_ids = held
            depth += 1
            continue

        selections.append(
            ReleaseSelection(
                release_id=primary.release_id,
                joint_score=sum(primary.per_file_score.get(sid, 0.0) for sid in kept),
                file_ids=tuple(sorted(kept)),
                per_file_best={sid: primary.per_file_score.get(sid, 0.0) for sid in kept},
            )
        )
        routed.update(kept)
        if held:
            split_count += 1
        remaining_file_ids = held
        depth += 1

    # Apply min_coverage: a selection is only kept if at least min_coverage of
    # its routed files cleared min_per_file_score on it. Otherwise drop the
    # selection and route those files into the unmatched bucket.
    accepted: list[ReleaseSelection] = []
    coverage_drops: set[str] = set()
    for sel in selections:
        passing = sum(
            1
            for sid in sel.file_ids
            if sel.per_file_best.get(sid, 0.0) >= thresholds.min_per_file_score
        )
        if not sel.file_ids:
            continue
        coverage = passing / len(sel.file_ids)
        if coverage >= thresholds.min_coverage:
            accepted.append(sel)
        else:
            coverage_drops.update(sel.file_ids)

    unmatched = tuple(
        sorted(
            set(file_ids)
            - {sid for sel in accepted for sid in sel.file_ids}
            | coverage_drops
        )
    )

    return StageOneResult(
        selections=tuple(accepted),
        unmatched_file_ids=unmatched,
        candidate_releases_considered=len(candidates),
        split_count=split_count,
    )


def _track_sort_key(track: ReleaseTrack) -> tuple[int, int, str]:
    md = track.metadata
    try:
        disc = int(md.discnumber) if md.discnumber else 0
    except ValueError:
        disc = 0
    try:
        pos = int(md.tracknumber) if md.tracknumber else 0
    except ValueError:
        pos = 0
    return (disc, pos, track.track_id)


ScoreFn = Callable[[FileCandidates, ReleaseTrack], float]


def assign_files_to_tracks(
    files: Sequence[FileCandidates],
    selection: ReleaseSelection,
    release_tracks: Sequence[ReleaseTrack],
    score_fn: ScoreFn,
    min_per_file_score: float,
) -> ReleaseAssignment:
    """Stage 2: maximum-weight assignment of files to tracks via Hungarian.

    `score_fn(file, release_track) -> float` produces the bipartite edge weight.
    Edges with score < min_per_file_score are excluded post-hoc; corresponding
    files land in `unmatched_file_ids`.
    """
    files_in_release = sorted(
        [f for f in files if f.source_id in set(selection.file_ids)],
        key=lambda f: f.source_id,
    )
    tracks = sorted(release_tracks, key=_track_sort_key)

    if not files_in_release or not tracks:
        return ReleaseAssignment(
            release_id=selection.release_id,
            joint_score=selection.joint_score,
            assignments=(),
            unmatched_file_ids=tuple(f.source_id for f in files_in_release),
        )

    n = len(files_in_release)
    m = len(tracks)
    # Pad to a square matrix so linear_sum_assignment works when n != m.
    size = max(n, m)
    cost = np.full((size, size), -_NO_MATCH_SCORE, dtype=float)
    for i, f in enumerate(files_in_release):
        for j, t in enumerate(tracks):
            score = score_fn(f, t)
            cost[i, j] = -score  # minimise

    row_ind, col_ind = linear_sum_assignment(cost)

    assignments: list[FileTrackAssignment] = []
    unmatched: list[str] = []
    for i in range(n):
        j = int(col_ind[i])
        if j >= m:
            unmatched.append(files_in_release[i].source_id)
            continue
        score = -float(cost[i, j])
        if score < min_per_file_score:
            unmatched.append(files_in_release[i].source_id)
            continue
        track = tracks[j]
        assignments.append(
            FileTrackAssignment(
                source_id=files_in_release[i].source_id,
                track_id=track.track_id,
                recording_id=track.recording_id,
                score=round(score, 6),
            )
        )

    assignments.sort(key=lambda a: a.source_id)
    return ReleaseAssignment(
        release_id=selection.release_id,
        joint_score=round(selection.joint_score, 6),
        assignments=tuple(assignments),
        unmatched_file_ids=tuple(sorted(unmatched)),
    )

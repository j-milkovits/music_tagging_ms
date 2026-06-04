"""Lookup orchestrator.

Single entry point: `lookup(items, joint, ...)` fans out AcoustID lookups,
runs the joint matcher when joint=True, and materialises tags via
build_release_tracks. In per-file mode, picks the best-release per file
without joint scoring.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from .acoustid import AcoustIdClient, AcoustIdLookupResult
from .joint_matcher import (
    FileCandidates,
    ReleaseAssignment,
    ScoreFn,
    Thresholds,
    _country_rank,
    assign_files_to_tracks,
    select_releases,
)
from .matcher import (
    FILE_COMPARISON_WEIGHTS,
    build_release_tracks,
    compare_release_parts,
    get_search_score,
    score_file_track_on_release,
    score_track_only_parts,
    split_release_track_tags,
)
from .models import (
    ArtistCredit,
    AudioMetadata,
    ReleaseCredits,
    ReleaseTrack,
    TrackCredits,
)
from .musicbrainz import MusicBrainzClient, artist_credit_name
from .similarity import linear_combination_of_weights


@dataclass(frozen=True, slots=True)
class LookupItem:
    source_id: str
    fingerprint: str
    duration: int
    metadata: AudioMetadata | None = None


@dataclass(frozen=True, slots=True)
class MaterialisedTrack:
    source_id: str
    track_id: str
    recording_id: str
    acoustid_id: str | None
    score: float
    applied_track_tags: dict[str, str]
    artists: tuple[ArtistCredit, ...]
    credits: TrackCredits


@dataclass(frozen=True, slots=True)
class MaterialisedRelease:
    release_id: str
    score: float
    applied_release_tags: dict[str, str]
    release_artists: tuple[ArtistCredit, ...]
    release_credits: ReleaseCredits
    tracks: tuple[MaterialisedTrack, ...]


@dataclass(frozen=True, slots=True)
class BestGuess:
    release_id: str | None
    recording_id: str | None
    acoustid_id: str | None
    score: float


@dataclass(frozen=True, slots=True)
class Unmatched:
    source_id: str
    reason: str
    best_guess: BestGuess | None


@dataclass(frozen=True, slots=True)
class LookupResult:
    mode: str
    assignments: tuple[MaterialisedRelease, ...]
    unmatched: tuple[Unmatched, ...]
    diagnostics: dict[str, int]


# ----- CD-ripping (DiscID/TOC) result models -----
# No fingerprint, no AcoustID, no scoring — the disc match is authoritative, so
# tracks carry metadata only (no source_id/score/acoustid_id).


@dataclass(frozen=True, slots=True)
class DiscCandidate:
    release_id: str
    title: str
    artist: str
    country: str
    date: str
    barcode: str
    track_count: int


@dataclass(frozen=True, slots=True)
class DiscTrack:
    track_id: str
    recording_id: str
    applied_track_tags: dict[str, str]
    artists: tuple[ArtistCredit, ...]
    credits: TrackCredits


@dataclass(frozen=True, slots=True)
class DiscRelease:
    release_id: str
    applied_release_tags: dict[str, str]
    release_artists: tuple[ArtistCredit, ...]
    release_credits: ReleaseCredits
    tracks: tuple[DiscTrack, ...]


@dataclass(frozen=True, slots=True)
class DiscLookupResult:
    release: DiscRelease | None
    candidates: tuple[DiscCandidate, ...]
    reason: str | None = None


class StandaloneTaggingService:
    def __init__(
        self,
        client: MusicBrainzClient | None = None,
        acoustid_client: AcoustIdClient | None = None,
    ) -> None:
        self.client = client or MusicBrainzClient()
        self.acoustid_client = acoustid_client or AcoustIdClient(
            client_key=os.getenv("TAGGING_MS_ACOUSTID_API_KEY", ""),
            musicbrainz_client=self.client,
        )

    # ----- public API -----

    def lookup(
        self,
        items: Sequence[LookupItem],
        joint: bool,
        preferred_countries: Sequence[str] | None,
        thresholds: Thresholds,
        search_limit: int,
    ) -> LookupResult:
        countries = list(preferred_countries) if preferred_countries else []
        # Fan out AcoustID lookups (sequential — ratecontrol enforces 333ms min).
        files: list[FileCandidates] = []
        per_file_lookup: dict[str, AcoustIdLookupResult] = {}
        for item in items:
            lookup = self.acoustid_client.lookup_by_fingerprint(
                item.fingerprint, item.duration, limit=search_limit
            )
            per_file_lookup[item.source_id] = lookup
            files.append(
                FileCandidates(
                    source_id=item.source_id,
                    metadata=item.metadata,
                    recordings=tuple(lookup.recordings),
                )
            )

        if joint:
            return self._lookup_joint(files, per_file_lookup, countries, thresholds)
        return self._lookup_per_file(
            files, per_file_lookup, countries, thresholds.min_per_file_score
        )

    # ----- CD-ripping (DiscID/TOC) mode -----

    def lookup_disc(
        self,
        discid: str,
        toc: str,
        preferred_countries: Sequence[str] | None,
        metadata: AudioMetadata | None,
    ) -> DiscLookupResult:
        """Resolve a CD by DiscID/TOC to MusicBrainz release metadata.

        A disc usually matches several releases (pressings). We return the
        single best release fully materialised, plus a lightweight `candidates`
        list of every match so the caller can pick a different pressing.
        """
        countries = list(preferred_countries) if preferred_countries else []
        payload = self.client.get_release_by_discid(discid, toc)
        releases = payload.get("releases") or []
        if not releases:
            return DiscLookupResult(
                release=None,
                candidates=(),
                reason="No MusicBrainz release found for this disc",
            )

        candidates = tuple(self._disc_candidate(r) for r in releases)
        best = self._rank_disc_releases(releases, countries, metadata)

        release_full = self.client.get_release(best["id"])
        cover_art = self.client.get_release_cover_art(best["id"])
        release_tracks = build_release_tracks(release_full, countries, cover_art)
        disc_release = self._materialise_disc_release(best["id"], release_tracks)
        return DiscLookupResult(
            release=disc_release, candidates=candidates, reason=None
        )

    @staticmethod
    def _disc_candidate(release: dict) -> DiscCandidate:
        media = release.get("media") or []
        track_count = sum(int(m.get("track-count") or 0) for m in media)
        return DiscCandidate(
            release_id=release.get("id", ""),
            title=release.get("title", ""),
            artist=artist_credit_name(release.get("artist-credit", [])),
            country=release.get("country", "") or "",
            date=release.get("date", "") or "",
            barcode=release.get("barcode") or "",
            track_count=track_count,
        )

    @staticmethod
    def _rank_disc_releases(
        releases: Sequence[dict],
        countries: Sequence[str],
        metadata: AudioMetadata | None,
    ) -> dict:
        """Pick the best candidate release dict.

        With caller metadata (re-tagging), rank by the existing release scorer
        (title/date/country/type/track-count); otherwise prefer the
        preferred-country pressing, then the earliest date, deterministically.
        """
        country_list = list(countries) if countries else None
        if metadata is not None:
            def meta_key(release: dict) -> tuple:
                score = linear_combination_of_weights(
                    compare_release_parts(
                        metadata, release, FILE_COMPARISON_WEIGHTS, country_list
                    )
                )
                return (
                    -score,
                    _country_rank(release, countries),
                    release.get("date") or "9999",
                    release.get("id", ""),
                )

            return min(releases, key=meta_key)

        return min(
            releases,
            key=lambda release: (
                _country_rank(release, countries),
                release.get("date") or "9999",
                release.get("id", ""),
            ),
        )

    @staticmethod
    def _materialise_disc_release(
        release_id: str, release_tracks: Sequence[ReleaseTrack]
    ) -> DiscRelease:
        tracks: list[DiscTrack] = []
        release_tags: dict[str, str] = {}
        release_artists: tuple[ArtistCredit, ...] = ()
        release_credits: ReleaseCredits = ReleaseCredits()
        for rt in release_tracks:
            release_part, track_part = split_release_track_tags(rt)
            if not release_tags:
                release_tags = release_part
                release_artists = rt.release_artists
                release_credits = rt.release_credits
            tracks.append(
                DiscTrack(
                    track_id=rt.track_id,
                    recording_id=rt.recording_id,
                    applied_track_tags=track_part,
                    artists=rt.artists,
                    credits=rt.track_credits,
                )
            )
        return DiscRelease(
            release_id=release_id,
            applied_release_tags=release_tags,
            release_artists=release_artists,
            release_credits=release_credits,
            tracks=tuple(tracks),
        )

    # ----- joint mode -----

    def _lookup_joint(
        self,
        files: Sequence[FileCandidates],
        per_file_lookup: dict[str, AcoustIdLookupResult],
        preferred_countries: Sequence[str],
        thresholds: Thresholds,
    ) -> LookupResult:
        # Use synthetic metadata for files that didn't supply any, so the
        # joint scorer can still rank releases on country/format/track-count
        # alone.
        normalised: list[FileCandidates] = []
        for f in files:
            md = f.metadata or AudioMetadata()
            normalised.append(
                FileCandidates(
                    source_id=f.source_id, metadata=md, recordings=f.recordings
                )
            )

        stage1 = select_releases(normalised, preferred_countries, thresholds)

        # Per-file AcoustID recording-id -> confidence maps — invariant across
        # releases, so compute once. Used by stage-2 to dominate any metadata
        # heuristic when the file's fingerprint already pinpointed a specific
        # recording; the confidence (not a hardcoded 1.0) becomes the score.
        file_recording_scores: dict[str, dict[str, float]] = {
            f.source_id: {
                rec["id"]: get_search_score(rec)
                for rec in f.recordings
                if rec.get("id")
            }
            for f in normalised
        }
        score_fn = _make_stage2_score_fn(file_recording_scores)

        joint_releases: list[MaterialisedRelease] = []
        rescue_targets: set[str] = set(stage1.unmatched_file_ids)

        for selection in stage1.selections:
            release_full = self.client.get_release(selection.release_id)
            cover_art = self.client.get_release_cover_art(selection.release_id)
            release_tracks = build_release_tracks(
                release_full, list(preferred_countries), cover_art
            )
            release_assignment = assign_files_to_tracks(
                normalised,
                selection,
                release_tracks,
                score_fn,
                thresholds.min_per_file_score,
            )
            joint_releases.append(
                _materialise_release(
                    release_assignment,
                    release_tracks,
                    {f.source_id: per_file_lookup[f.source_id].acoustid_id for f in files},
                )
            )
            rescue_targets.update(release_assignment.unmatched_file_ids)

        # Per-file rescue for everything Stage 1 didn't route + Stage 2 stranded.
        rescue_assignments: list[MaterialisedRelease] = []
        unmatched: list[Unmatched] = []
        for sid in sorted(rescue_targets):
            outcome = self._best_per_file(
                source_id=sid,
                files=files,
                lookup=per_file_lookup[sid],
                preferred_countries=preferred_countries,
                min_per_file_score=thresholds.min_per_file_score,
            )
            if isinstance(outcome, MaterialisedRelease):
                rescue_assignments.append(outcome)
            else:
                unmatched.append(outcome)

        all_assignments = tuple(joint_releases + rescue_assignments)
        files_matched = sum(len(a.tracks) for a in all_assignments)

        return LookupResult(
            mode="joint",
            assignments=all_assignments,
            unmatched=tuple(unmatched),
            diagnostics={
                "candidate_releases_considered": stage1.candidate_releases_considered,
                "split_count": stage1.split_count,
                "files_in": len(files),
                "files_matched": files_matched,
            },
        )

    # ----- per-file mode -----

    def _lookup_per_file(
        self,
        files: Sequence[FileCandidates],
        per_file_lookup: dict[str, AcoustIdLookupResult],
        preferred_countries: Sequence[str],
        min_per_file_score: float,
    ) -> LookupResult:
        per_release: dict[str, list[MaterialisedRelease]] = {}
        unmatched: list[Unmatched] = []
        for f in files:
            outcome = self._best_per_file(
                source_id=f.source_id,
                files=files,
                lookup=per_file_lookup[f.source_id],
                preferred_countries=preferred_countries,
                min_per_file_score=min_per_file_score,
            )
            if isinstance(outcome, MaterialisedRelease):
                per_release.setdefault(outcome.release_id, []).append(outcome)
            else:
                unmatched.append(outcome)

        # Per-file scoring is independent, but display groups files that
        # resolved to the same release into one assignment. Tracks inside
        # each group are sorted by source_id for determinism.
        assignments: list[MaterialisedRelease] = []
        for release_id in sorted(per_release):
            group = per_release[release_id]
            merged_tracks = tuple(
                sorted(
                    (mt for rel in group for mt in rel.tracks),
                    key=lambda mt: mt.source_id,
                )
            )
            mean_score = sum(mt.score for mt in merged_tracks) / len(merged_tracks)
            assignments.append(
                MaterialisedRelease(
                    release_id=release_id,
                    score=round(mean_score, 6),
                    applied_release_tags=group[0].applied_release_tags,
                    release_artists=group[0].release_artists,
                    release_credits=group[0].release_credits,
                    tracks=merged_tracks,
                )
            )

        files_matched = sum(len(a.tracks) for a in assignments)
        return LookupResult(
            mode="per-file",
            assignments=tuple(assignments),
            unmatched=tuple(unmatched),
            diagnostics={
                "candidate_releases_considered": 0,
                "split_count": 0,
                "files_in": len(files),
                "files_matched": files_matched,
            },
        )

    def _best_per_file(
        self,
        source_id: str,
        files: Sequence[FileCandidates],
        lookup: AcoustIdLookupResult,
        preferred_countries: Sequence[str],
        min_per_file_score: float,
    ) -> MaterialisedRelease | Unmatched:
        file = next(f for f in files if f.source_id == source_id)
        file_md = file.metadata or AudioMetadata()

        if not lookup.recordings:
            fallback_recordings = self._search_recordings_by_metadata(file_md)
            if not fallback_recordings:
                return Unmatched(
                    source_id=source_id,
                    reason="AcoustID lookup returned no recordings",
                    best_guess=None,
                )
            return self._select_best_release(
                source_id=source_id,
                file_md=file_md,
                recordings=fallback_recordings,
                acoustid_id=None,
                preferred_countries=preferred_countries,
                min_per_file_score=min_per_file_score,
            )

        return self._select_best_release(
            source_id=source_id,
            file_md=file_md,
            recordings=list(lookup.recordings),
            acoustid_id=lookup.acoustid_id,
            preferred_countries=preferred_countries,
            min_per_file_score=min_per_file_score,
        )

    def _search_recordings_by_metadata(self, file_md: AudioMetadata) -> list[dict]:
        """Fallback when AcoustID returned nothing: search MusicBrainz by the
        client-supplied metadata. Returns [] when the metadata is too sparse
        to form a query."""
        if not (
            file_md.title
            or file_md.artist
            or file_md.release
            or file_md.tracknumber
            or file_md.totaltracks
            or file_md.isrc
            or file_md.length_ms
        ):
            return []
        try:
            return self.client.find_tracks(file_md)
        except ValueError:
            return []

    def _select_best_release(
        self,
        source_id: str,
        file_md: AudioMetadata,
        recordings: list[dict],
        acoustid_id: str | None,
        preferred_countries: Sequence[str],
        min_per_file_score: float,
    ) -> MaterialisedRelease | Unmatched:
        below_threshold_reason = (
            "No AcoustID match above threshold"
            if acoustid_id is not None
            else "MusicBrainz search match below threshold"
        )

        best_score = -1.0
        best_recording: dict | None = None
        best_release: dict | None = None
        for recording in recordings:
            for release in recording.get("releases") or []:
                score = score_file_track_on_release(
                    file_md,
                    recording,
                    release,
                    FILE_COMPARISON_WEIGHTS,
                    list(preferred_countries) if preferred_countries else None,
                )
                if score > best_score:
                    best_score = score
                    best_recording = recording
                    best_release = release

        if best_recording is None:
            best_recording = max(
                recordings, key=lambda r: float(r.get("score", 0.0))
            )
            best_score = float(best_recording.get("score", 0.0)) / 100.0
            return Unmatched(
                source_id=source_id,
                reason="Matched recording had no release attached",
                best_guess=BestGuess(
                    release_id=None,
                    recording_id=best_recording.get("id"),
                    acoustid_id=acoustid_id,
                    score=round(max(best_score, 0.0), 6),
                ),
            )

        if best_score < min_per_file_score:
            return Unmatched(
                source_id=source_id,
                reason=below_threshold_reason,
                best_guess=BestGuess(
                    release_id=(best_release or {}).get("id"),
                    recording_id=best_recording.get("id"),
                    acoustid_id=acoustid_id,
                    score=round(max(best_score, 0.0), 6),
                ),
            )

        assert best_release is not None
        release_full = self.client.get_release(best_release["id"])
        cover_art = self.client.get_release_cover_art(best_release["id"])
        release_tracks = build_release_tracks(
            release_full, list(preferred_countries), cover_art
        )
        matched_track = next(
            (
                rt
                for rt in release_tracks
                if rt.recording_id == best_recording.get("id")
            ),
            None,
        )
        if matched_track is None:
            return Unmatched(
                source_id=source_id,
                reason="Matched recording was not found on the loaded release",
                best_guess=BestGuess(
                    release_id=best_release.get("id"),
                    recording_id=best_recording.get("id"),
                    acoustid_id=acoustid_id,
                    score=round(max(best_score, 0.0), 6),
                ),
            )

        release_tags, track_tags = split_release_track_tags(matched_track)
        score = round(max(best_score, 0.0), 6)
        return MaterialisedRelease(
            release_id=matched_track.release_id,
            score=score,
            applied_release_tags=release_tags,
            release_artists=matched_track.release_artists,
            release_credits=matched_track.release_credits,
            tracks=(
                MaterialisedTrack(
                    source_id=source_id,
                    track_id=matched_track.track_id,
                    recording_id=matched_track.recording_id,
                    acoustid_id=acoustid_id,
                    score=score,
                    applied_track_tags=track_tags,
                    artists=matched_track.artists,
                    credits=matched_track.track_credits,
                ),
            ),
        )


def _make_stage2_score_fn(
    file_recording_scores: dict[str, dict[str, float]],
) -> ScoreFn:
    """Build a score function for stage-2 bipartite matching.

    Strong rule: a file whose AcoustID candidate set includes the track's
    recording id wins that track outright, scored at that recording's AcoustID
    fingerprint confidence (not a hardcoded 1.0). Otherwise fall back to the
    existing metadata similarity over title/artist/length. Files that supplied
    no metadata return 0 so they don't accidentally outscore a real
    recording-id match elsewhere.
    """
    def score_fn(file: FileCandidates, track: ReleaseTrack) -> float:
        recording_scores = file_recording_scores.get(file.source_id, {})
        if track.recording_id in recording_scores:
            return recording_scores[track.recording_id]
        file_md = file.metadata or AudioMetadata()
        if not (file_md.title or file_md.artist or file_md.length_ms):
            return 0.0
        synthetic_track = {
            "title": track.metadata.title,
            "artist-credit": [{"name": track.metadata.artist}],
            "length": track.metadata.length_ms,
        }
        parts = score_track_only_parts(
            file_md, synthetic_track, FILE_COMPARISON_WEIGHTS
        )
        return linear_combination_of_weights(parts)
    return score_fn


def _materialise_release(
    release_assignment: ReleaseAssignment,
    release_tracks: Sequence[ReleaseTrack],
    acoustid_id_by_source: dict[str, str | None],
) -> MaterialisedRelease:
    track_by_id = {rt.track_id: rt for rt in release_tracks}
    matched_tracks: list[MaterialisedTrack] = []
    release_tags: dict[str, str] = {}
    release_artists: tuple[ArtistCredit, ...] = ()
    release_credits: ReleaseCredits = ReleaseCredits()
    for fa in release_assignment.assignments:
        track = track_by_id.get(fa.track_id)
        if track is None:
            continue
        release_part, track_part = split_release_track_tags(track)
        if not release_tags:
            release_tags = release_part
            release_artists = track.release_artists
            release_credits = track.release_credits
        matched_tracks.append(
            MaterialisedTrack(
                source_id=fa.source_id,
                track_id=fa.track_id,
                recording_id=fa.recording_id,
                acoustid_id=acoustid_id_by_source.get(fa.source_id),
                score=fa.score,
                applied_track_tags=track_part,
                artists=track.artists,
                credits=track.track_credits,
            )
        )

    mean_score = (
        sum(t.score for t in matched_tracks) / len(matched_tracks)
        if matched_tracks
        else 0.0
    )
    return MaterialisedRelease(
        release_id=release_assignment.release_id,
        score=round(mean_score, 6),
        applied_release_tags=release_tags,
        release_artists=release_artists,
        release_credits=release_credits,
        tracks=tuple(matched_tracks),
    )


__all__ = [
    "BestGuess",
    "LookupItem",
    "LookupResult",
    "MaterialisedTrack",
    "MaterialisedRelease",
    "StandaloneTaggingService",
    "Unmatched",
]

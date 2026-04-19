from __future__ import annotations

import os

from .acoustid import AcoustIdClient
from .matcher import (CLUSTER_COMPARISON_WEIGHTS, FILE_COMPARISON_WEIGHTS,
                      aggregate_cluster_metadata, assign_files_to_release,
                      best_match, build_release_tracks, compare_release,
                      compare_track, get_search_score,
                      select_release_by_country,
                      tagged_metadata_for_assignment)
from .models import (AudioMetadata, ClusterMatch, FileAssignment, InputFile,
                     MatchCandidate)
from .musicbrainz import MusicBrainzClient, artist_credit_name
from .tagger import load_input_files, write_audio_metadata


class StandaloneTaggingService:
    def __init__(
        self,
        client: MusicBrainzClient | None = None,
        acoustid_client: AcoustIdClient | None = None,
    ) -> None:
        self.client = client or MusicBrainzClient()
        self.acoustid_client = acoustid_client or AcoustIdClient(
            client_key=os.getenv("TAGGING_MS_ACOUSTID_API_KEY", ""),
            user_agent="picard-micro-service/0.1",
            musicbrainz_client=self.client,
        )

    def load_files(self, file_paths: list[str]) -> list[InputFile]:
        return load_input_files(file_paths)

    def autotag_files(
        self,
        file_paths: list[str],
        output_dir: str | None = None,
        track_match_threshold: float = 0.4,
        write_tags: bool = True,
        search_limit: int = 10,
    ) -> list[FileAssignment]:
        files = self.load_files(file_paths)
        return self.autotag_inputs(
            files,
            output_dir=output_dir,
            track_match_threshold=track_match_threshold,
            write_tags=write_tags,
            search_limit=search_limit,
        )

    def autotag_metadata(
        self,
        metadata: AudioMetadata | InputFile,
        source_id: str = "input",
        track_match_threshold: float = 0.4,
        search_limit: int = 10,
        preferred_countries: list[str] | None = None,
    ) -> FileAssignment:
        if isinstance(metadata, InputFile):
            item = metadata
        else:
            item = InputFile(path=source_id, metadata=metadata)
        return self.autotag_inputs(
            [item],
            output_dir=None,
            track_match_threshold=track_match_threshold,
            write_tags=False,
            search_limit=search_limit,
            preferred_countries=preferred_countries,
        )[0]

    def autotag_acoustid_file(
        self,
        fingerprint: str,
        duration: int,
        source_id: str = "input",
        track_match_threshold: float = 0.0,
        search_limit: int = 10,
        preferred_countries: list[str] | None = None,
    ) -> FileAssignment:
        return self.autotag_acoustid_tracks(
            [(source_id, fingerprint, duration)],
            track_match_threshold=track_match_threshold,
            search_limit=search_limit,
            preferred_countries=preferred_countries,
        )[0]

    def autotag_acoustid_tracks(
        self,
        items: list[tuple[str, str, int]],
        track_match_threshold: float = 0.0,
        search_limit: int = 10,
        preferred_countries: list[str] | None = None,
    ) -> list[FileAssignment]:
        results: list[FileAssignment] = []
        for source_id, fingerprint, duration in items:
            lookup = self.acoustid_client.lookup_by_fingerprint(
                fingerprint, duration, limit=search_limit
            )
            results.append(
                self._resolve_acoustid_match(
                    source_path=source_id,
                    lookup=lookup,
                    candidate=best_match(
                        MatchCandidate(
                            similarity=get_search_score(recording),
                            payload=recording,
                        )
                        for recording in lookup.recordings
                    ),
                    threshold=track_match_threshold,
                    no_match_reason="No AcoustID track match above threshold",
                    preferred_countries=preferred_countries,
                )
            )
        return results

    def autotag_hybrid_file(
        self,
        metadata: AudioMetadata | InputFile,
        fingerprint: str,
        duration: int,
        source_id: str = "input",
        track_match_threshold: float = 0.0,
        search_limit: int = 10,
        preferred_countries: list[str] | None = None,
    ) -> FileAssignment:
        if isinstance(metadata, InputFile):
            item = metadata
        else:
            item = InputFile(path=source_id, metadata=metadata)
        return self.autotag_hybrid_inputs(
            [(item, fingerprint, duration)],
            track_match_threshold=track_match_threshold,
            search_limit=search_limit,
            preferred_countries=preferred_countries,
        )[0]

    def autotag_hybrid_inputs(
        self,
        items: list[tuple[InputFile, str, int]],
        track_match_threshold: float = 0.0,
        search_limit: int = 10,
        preferred_countries: list[str] | None = None,
    ) -> list[FileAssignment]:
        results: list[FileAssignment] = []
        for file, fingerprint, duration in items:
            lookup = self.acoustid_client.lookup_by_fingerprint(
                fingerprint, duration, limit=search_limit
            )
            candidate = best_match(
                MatchCandidate(
                    similarity=compare_track(
                        file.metadata,
                        recording,
                        FILE_COMPARISON_WEIGHTS,
                        preferred_countries,
                    ),
                    payload=recording,
                )
                for recording in lookup.recordings
            )
            results.append(
                self._resolve_acoustid_match(
                    source_path=file.path,
                    lookup=lookup,
                    candidate=candidate,
                    threshold=track_match_threshold,
                    no_match_reason="No hybrid track match above threshold",
                    no_release_reason="Matched hybrid recording had no release attached",
                    missing_track_reason="Matched hybrid recording was not found on the loaded release",
                    add_acoustid_tag=True,
                    preferred_countries=preferred_countries,
                )
            )
        return results

    def _resolve_acoustid_match(
        self,
        source_path: str,
        lookup,
        candidate: MatchCandidate | None,
        threshold: float,
        no_match_reason: str,
        no_release_reason: str = "Matched AcoustID recording had no release attached",
        missing_track_reason: str = "Matched AcoustID recording was not found on the loaded release",
        add_acoustid_tag: bool = False,
        preferred_countries: list[str] | None = None,
    ) -> FileAssignment:
        if not lookup.recordings:
            return FileAssignment(
                source_path=source_path,
                matched=False,
                similarity=0.0,
                acoustid_id=lookup.acoustid_id,
                reason="AcoustID lookup returned no recordings",
            )

        if (
            candidate is None
            or candidate.payload is None
            or candidate.similarity < threshold
        ):
            return FileAssignment(
                source_path=source_path,
                matched=False,
                similarity=candidate.similarity if candidate else 0.0,
                acoustid_id=lookup.acoustid_id,
                reason=no_match_reason,
            )

        recording = candidate.payload
        releases = recording.get("releases") or []
        selected_release = select_release_by_country(releases, preferred_countries)
        release_id = selected_release["id"] if selected_release else None
        if not release_id:
            return FileAssignment(
                source_path=source_path,
                matched=False,
                similarity=candidate.similarity,
                acoustid_id=lookup.acoustid_id,
                recording_id=recording.get("id"),
                reason=no_release_reason,
            )

        release = self.client.get_release(release_id)
        release_tracks = build_release_tracks(release, preferred_countries)
        matched_track = next(
            (
                release_track
                for release_track in release_tracks
                if release_track.recording_id == recording.get("id")
            ),
            None,
        )
        if matched_track is None:
            return FileAssignment(
                source_path=source_path,
                matched=False,
                similarity=candidate.similarity,
                acoustid_id=lookup.acoustid_id,
                release_id=release_id,
                recording_id=recording.get("id"),
                reason=missing_track_reason,
            )

        applied_tags = tagged_metadata_for_assignment(matched_track)
        if add_acoustid_tag and lookup.acoustid_id:
            applied_tags["acoustid_id"] = lookup.acoustid_id

        return FileAssignment(
            source_path=source_path,
            matched=True,
            similarity=candidate.similarity,
            acoustid_id=lookup.acoustid_id,
            release_id=matched_track.album_id,
            track_id=matched_track.track_id,
            recording_id=matched_track.recording_id,
            applied_tags=applied_tags,
        )

    def autotag_inputs(
        self,
        files: list[InputFile],
        output_dir: str | None = None,
        track_match_threshold: float = 0.4,
        write_tags: bool = True,
        search_limit: int = 10,
        preferred_countries: list[str] | None = None,
    ) -> list[FileAssignment]:
        results: list[FileAssignment] = []
        for file in files:
            recordings = self.client.find_tracks(file.metadata, limit=search_limit)
            candidate = best_match(
                MatchCandidate(
                    similarity=compare_track(
                        file.metadata,
                        recording,
                        FILE_COMPARISON_WEIGHTS,
                        preferred_countries,
                    ),
                    payload=recording,
                )
                for recording in recordings
            )
            if (
                candidate is None
                or candidate.payload is None
                or candidate.similarity < track_match_threshold
            ):
                results.append(
                    FileAssignment(
                        source_path=file.path,
                        matched=False,
                        similarity=candidate.similarity if candidate else 0.0,
                        reason="No track match above threshold",
                    )
                )
                continue

            recording = candidate.payload
            releases = recording.get("releases") or []
            selected_release = select_release_by_country(releases, preferred_countries)
            release_id = selected_release["id"] if selected_release else None
            if not release_id:
                results.append(
                    FileAssignment(
                        source_path=file.path,
                        matched=False,
                        similarity=candidate.similarity,
                        reason="Matched recording had no release attached",
                    )
                )
                continue

            release = self.client.get_release(release_id)
            release_tracks = build_release_tracks(release, preferred_countries)
            assignments = assign_files_to_release(
                [file], release_tracks, threshold=track_match_threshold
            )
            results.extend(
                self._materialize_assignments(
                    assignments, output_dir=output_dir, write_tags=write_tags
                )
            )
        return results

    def autotag_cluster(
        self,
        file_paths: list[str],
        output_dir: str | None = None,
        cluster_match_threshold: float = 0.5,
        track_match_threshold: float = 0.4,
        write_tags: bool = True,
        search_limit: int = 10,
    ) -> ClusterMatch:
        files = self.load_files(file_paths)
        return self.autotag_cluster_inputs(
            files,
            output_dir=output_dir,
            cluster_match_threshold=cluster_match_threshold,
            track_match_threshold=track_match_threshold,
            write_tags=write_tags,
            search_limit=search_limit,
        )

    def autotag_cluster_metadata(
        self,
        items: list[InputFile],
        cluster_match_threshold: float = 0.5,
        track_match_threshold: float = 0.4,
        search_limit: int = 10,
        preferred_countries: list[str] | None = None,
    ) -> ClusterMatch:
        return self.autotag_cluster_inputs(
            items,
            output_dir=None,
            cluster_match_threshold=cluster_match_threshold,
            track_match_threshold=track_match_threshold,
            write_tags=False,
            search_limit=search_limit,
            preferred_countries=preferred_countries,
        )

    def autotag_cluster_inputs(
        self,
        files: list[InputFile],
        output_dir: str | None = None,
        cluster_match_threshold: float = 0.5,
        track_match_threshold: float = 0.4,
        write_tags: bool = True,
        search_limit: int = 10,
        preferred_countries: list[str] | None = None,
    ) -> ClusterMatch:
        cluster_metadata = aggregate_cluster_metadata(files)
        releases = self.client.find_releases(cluster_metadata, limit=search_limit)
        candidate = best_match(
            MatchCandidate(
                similarity=compare_release(
                    cluster_metadata,
                    release,
                    CLUSTER_COMPARISON_WEIGHTS,
                    preferred_countries,
                ),
                payload=release,
            )
            for release in releases
        )
        if (
            candidate is None
            or candidate.payload is None
            or candidate.similarity < cluster_match_threshold
        ):
            raise ValueError("No release match above threshold")

        release = self.client.get_release(candidate.payload["id"])
        release_tracks = build_release_tracks(release, preferred_countries)
        assignments = assign_files_to_release(
            files, release_tracks, threshold=track_match_threshold
        )
        materialized = self._materialize_assignments(
            assignments, output_dir=output_dir, write_tags=write_tags
        )
        return ClusterMatch(
            release_id=release["id"],
            similarity=candidate.similarity,
            release_title=release.get("title", ""),
            release_artist=artist_credit_name(release.get("artist-credit", [])),
            assignments=materialized,
        )

    def _materialize_assignments(
        self,
        assignments: list[tuple[InputFile, object | None, float]],
        output_dir: str | None,
        write_tags: bool,
        lookup_info_by_path: dict[str, dict[str, str | None]] | None = None,
    ) -> list[FileAssignment]:
        results: list[FileAssignment] = []
        for file, release_track, similarity in assignments:
            lookup_info = (lookup_info_by_path or {}).get(file.path, {})
            if release_track is None:
                results.append(
                    FileAssignment(
                        source_path=file.path,
                        matched=False,
                        similarity=similarity,
                        acoustid_id=lookup_info.get("acoustid_id"),
                        reason="No album track assignment above threshold",
                    )
                )
                continue

            tags = tagged_metadata_for_assignment(release_track)
            target_path = None
            if write_tags:
                target_path = write_audio_metadata(
                    file.path, tags, output_dir=output_dir
                )
            results.append(
                FileAssignment(
                    source_path=file.path,
                    matched=True,
                    similarity=similarity,
                    acoustid_id=lookup_info.get("acoustid_id"),
                    target_path=target_path,
                    release_id=release_track.album_id,
                    track_id=release_track.track_id,
                    recording_id=release_track.recording_id,
                    applied_tags=tags,
                )
            )
        return results

# Tagging Microservice

Standalone MusicBrainz tagging microservice extracted from Picard.

It covers:

- matching a single file against MusicBrainz recordings
- matching a cluster of files against MusicBrainz releases
- identifying a single file via AcoustID only
- identifying multiple files via AcoustID only in a batched API call
- identifying a single file via AcoustID plus metadata
- identifying multiple files via AcoustID plus metadata in a batched API call
- assigning files to release tracks
- writing matched tags back with `mutagen`
- exposing lookup workflows through a small FastAPI app

## Package layout

The Python package lives under `src/tagging_ms` to match the `uv` project's `src` layout.

Interactive API documentation is available at `/docs` (Swagger UI) and `/redoc` (ReDoc) when the service is running.

The FastAPI surface exposes three lookup families:

- metadata: query MusicBrainz directly from provided metadata
- acoustid: query AcoustID from a client-generated fingerprint plus duration and rank by AcoustID score only
- hybrid: query AcoustID from a client-generated fingerprint plus duration and rank returned candidates against provided metadata

All endpoints accept an optional `preferred_release_countries` list (ordered ISO-3166-1 codes) to bias release selection toward a preferred market. MusicBrainz pseudo-codes `XE` (Europe) and `XW` (worldwide) are supported alongside standard country codes.

The backend does not need filesystem access. Metadata, fingerprint, and duration are provided by the client.

### Tag coverage

Every successful response includes an `applied_tags` object with a rich tag set sourced from MusicBrainz:

- Core track tags: `title`, `artist`, `tracknumber`, `totaltracks`, `discnumber`, `totaldiscs`, `isrc`, `length_ms`
- Album tags: `album`, `albumartist`, `date`, `originaldate`, `releasetype`, `releasecountry`, `media`, `label`, `catalognumber`, `barcode`, `script`
- MusicBrainz IDs: `musicbrainz_albumid`, `musicbrainz_trackid`, `musicbrainz_recordingid`, `musicbrainz_releasegroupid`, `musicbrainz_artistid`, `musicbrainz_albumartistid`, `musicbrainz_workid`, `acoustid_id`
- Relationship tags (from MusicBrainz Advanced Relationships): `work`, `composer`, `lyricist`, `writer`, `arranger`, `producer`, `engineer`, `mixer`, `conductor`, `performers`

Relationship tags are populated in a single release lookup using `recording-level-rels` and `work-level-rels`. The two-hop chain recording → work → composer/lyricist/writer is resolved without additional requests. Fields are empty strings when no relevant relationship exists in MusicBrainz.

Multiple values within a single field (e.g. two composers) are joined with `; `.

See `/docs` or `/redoc` for the full field reference.

## Run

```bash
cp .env_example .env
uv sync
uv run tagging-ms
```

For development with reload:

```bash
uv run uvicorn tagging_ms.api:app --reload
```

To test `autotag_files()` directly without FastAPI:

```bash
uv run tagging-ms-test-autotag-files /path/to/song.flac --no-write-tags
```

## Endpoints

`POST /api/lookup/metadata/file`

Single-file metadata lookup.
Computes a MusicBrainz recording search from the provided metadata, ranks candidates by metadata similarity, then loads the best release and assigns tags.

```json
{
  "source_id": "song1.flac",
  "metadata": {
    "title": "Song 1",
    "artist": "Artist 1",
    "album": "Album 1",
    "length_ms": 190000,
    "format_name": "MP3"
  },
  "preferred_release_countries": ["DE", "XE", "XW"]
}
```

`POST /api/lookup/metadata/cluster`

Album-style metadata lookup for multiple files.
Aggregates cluster metadata, searches MusicBrainz releases, picks the best release, then assigns all files to release tracks.

```json
{
  "items": [
    {
      "source_id": "01.flac",
      "metadata": {
        "title": "Track 1",
        "artist": "Artist 1",
        "album": "Album 1",
        "length_ms": 190000
      }
    },
    {
      "source_id": "02.flac",
      "metadata": {
        "title": "Track 2",
        "artist": "Artist 1",
        "album": "Album 1",
        "length_ms": 210000
      }
    }
  ],
  "track_match_threshold": 0.4,
  "cluster_match_threshold": 0.5,
  "preferred_release_countries": ["DE", "XE", "XW"]
}
```

`POST /api/lookup/acoustid/file`

Single-file AcoustID-only lookup.
Queries AcoustID from the provided `fingerprint` and `duration`, ranks returned recordings by AcoustID score only, then loads the matched release and returns track tags.

```json
{
  "source_id": "song1.mp3",
  "fingerprint": "AQAAO0mUaEkSZSoAAAAAAAAA",
  "duration": 190,
  "preferred_release_countries": ["DE", "XE", "XW"]
}
```

`POST /api/lookup/acoustid/files`

Batched AcoustID-only lookup.
Runs the same AcoustID-only logic independently for each item and returns one result per input item.

```json
{
  "items": [
    {
      "source_id": "01.mp3",
      "fingerprint": "AQAAO0mUaEkSZSoAAAAAAAAA",
      "duration": 190
    },
    {
      "source_id": "02.mp3",
      "fingerprint": "AQAAjV2JZEoSZSoAAAAAAAAA",
      "duration": 210
    }
  ],
  "preferred_release_countries": ["DE", "XE", "XW"]
}
```

`POST /api/lookup/hybrid/file`

Single-file hybrid lookup.
Queries AcoustID from the provided `fingerprint` and `duration`, then ranks the returned recordings against the provided metadata, similar to Picard's AcoustID flow.

```json
{
  "source_id": "song1.mp3",
  "fingerprint": "AQAAO0mUaEkSZSoAAAAAAAAA",
  "duration": 190,
  "metadata": {
    "title": "Song 1",
    "artist": "Artist 1",
    "album": "Album 1",
    "length_ms": 190000
  },
  "preferred_release_countries": ["DE", "XE", "XW"]
}
```

`POST /api/lookup/hybrid/files`

Batched hybrid lookup.
Runs the same hybrid logic independently for each item and returns one result per input item.

```json
{
  "items": [
    {
      "source_id": "01.mp3",
      "fingerprint": "AQAAO0mUaEkSZSoAAAAAAAAA",
      "duration": 190,
      "metadata": {
        "title": "Track 1",
        "artist": "Artist 1",
        "album": "Album 1",
        "length_ms": 190000
      }
    },
    {
      "source_id": "02.mp3",
      "fingerprint": "AQAAjV2JZEoSZSoAAAAAAAAA",
      "duration": 210,
      "metadata": {
        "title": "Track 2",
        "artist": "Artist 1",
        "album": "Album 1",
        "length_ms": 210000
      }
    }
  ],
  "preferred_release_countries": ["DE", "XE", "XW"]
}
```

## License

This project is licensed under the **GNU General Public License v2.0** (GPL-2.0), the same license as [MusicBrainz Picard](https://picard.musicbrainz.org/), from which matching and tagging logic is derived. See [LICENSE](LICENSE) for the full license text.

## Notes

- The API does not write files. It returns the matched MusicBrainz tags for a client to apply locally.
- The local CLI helpers can still read and write files directly when you want single-machine testing.
- The tag writer is generic `mutagen` logic, so it is simpler than Picard's format handlers.
- Runtime settings are loaded from `tagging_ms/.env` via `python-dotenv`.
- AcoustID lookups require a configured `TAGGING_MS_ACOUSTID_API_KEY`.

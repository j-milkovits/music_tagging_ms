# Tagging Microservice

HTTP service for joint AcoustID/MusicBrainz album matching, derived from the
matching logic in [MusicBrainz Picard](https://picard.musicbrainz.org/).

## Overview

A single HTTP endpoint accepts one or more files (each: a Chromaprint
fingerprint plus duration). It looks each file up against AcoustID and resolves
the result against MusicBrainz, returning the tags that should be written
client-side. The service does not read or write audio files.

Two modes:

- **Joint** (`joint: true`, default): pool the AcoustID candidates across all
  submitted files and pick the release(s) that maximise the joint score, then
  assign files to tracks within each chosen release. Implements
  [`joint_matching_spec.md`](./joint_matching_spec.md).
- **Per-file** (`joint: false`): pick the best release independently for each
  file. Equivalent to the legacy AcoustID-only flow.

## Run

```bash
cp .env_example .env       # set TAGGING_MS_API_KEY and TAGGING_MS_ACOUSTID_API_KEY
make install               # uv sync
uv run tagging-ms          # binds to TAGGING_MS_HOST:TAGGING_MS_PORT
```

For development with auto-reload:

```bash
uv run uvicorn tagging_ms.api:app --reload
```

Container:

```bash
make build                 # passes git sha as build arg
make run
```

## Endpoints

Interactive docs at `/docs` (Swagger) and `/redoc`.

| Method | Path           | Auth     | Purpose                                  |
| ------ | -------------- | -------- | ---------------------------------------- |
| GET    | `/api/health`  | none     | Liveness probe                           |
| GET    | `/api/version` | none     | Service name, version, baked git sha     |
| POST   | `/api/lookup`  | Bearer   | Joint or per-file AcoustID lookup        |

### Auth

`/api/lookup` requires `Authorization: Bearer $TAGGING_MS_API_KEY`. Tokens are
compared via `hmac.compare_digest`. Missing or invalid tokens get `401`. If
`TAGGING_MS_API_KEY` is not configured the service returns `500` from any
authenticated endpoint.

### `POST /api/lookup`

Request:

```jsonc
{
  "items": [
    {
      "source_id": "01.wav",
      "fingerprint": "AQAD...",
      "duration": 287,
      "metadata": {                          // optional, refines joint scoring
        "title": "Track 1",
        "artist": "Artist 1",
        "release": "Album 1",
        "length_ms": 287000
      }
    }
  ],
  "joint": true,
  "preferred_release_countries": ["DE", "XE", "XW"],
  "thresholds": {
    "min_per_file_score": 0.5,
    "min_coverage": 0.6,
    "split_margin": 0.15
  },
  "search_limit": 10
}
```

Response:

Matched files are grouped by release under `assignments`. Each assignment
carries release-level `metadata` plus a `tracks` array, and each matched track
carries its own `metadata`. Per-file mode (`joint: false`) produces the same
shape — just one track per assignment. Files no release could claim land in
`unmatched`.

```jsonc
{
  "mode": "joint",
  "assignments": [
    {
      "release_id": "b9608c76-ae35-372d-9e05-af2c5e1caec3",
      "score": 0.97,                          // mean of tracks[].score
      "metadata": {
        "title": "Carmen-Fantasie",
        "date": "1993",
        "originaldate": "1993",
        "country": "DE",
        "type": "album",
        "musicbrainz_id": "b9608c76-ae35-372d-9e05-af2c5e1caec3",
        "musicbrainz_release_group_id": "...",
        "label": "Deutsche Grammophon",
        "catalognumber": "...",
        "barcode": "028943754422",
        "script": "Latn",
        // front cover validated against the Cover Art Archive,
        // omitted entirely when the release has no art:
        "cover_art_url": "http://coverartarchive.org/release/b9608c76-.../front.jpg",
        "cover_art_thumb_url": "http://coverartarchive.org/release/b9608c76-.../front-250.jpg",
        // structured release-artist credits (reconstruct the display
        // string by joining `name`s; collect `musicbrainz_artistid` for IDs):
        "artists": [
          { "name": "Anne-Sophie Mutter", "sort_name": "Mutter, Anne-Sophie",
            "musicbrainz_artistid": "...", "type": "Person", "disambiguation": "" }
        ],
        // release-level relationship credits (each an array of credits):
        "producers": [], "engineers": [], "mixers": [],
        "conductors": [], "arrangers": [], "performers": []
      },
      "tracks": [
        {
          "source_id": "01.wav",
          "track_id": "...",
          "recording_id": "...",
          "acoustid_id": "...",
          "score": 0.97,                       // real AcoustID/metadata confidence
          "metadata": {
            "title": "Carmen-Fantasie, Op. 25",
            "tracknumber": "1", "totaltracks": "12",
            "discnumber": "1", "totaldiscs": "1",
            "isrc": "...", "length_ms": 287000, "media": "CD",
            "musicbrainz_trackid": "...", "musicbrainz_recordingid": "...",
            "genre": "Classical",
            "artists": [ { "name": "Anne-Sophie Mutter", "...": "..." } ],
            // track-level relationship credits + works:
            "composers": [], "lyricists": [], "writers": [], "arrangers": [],
            "producers": [], "engineers": [], "mixers": [], "conductors": [],
            "performers": [],
            "works": [ { "title": "...", "musicbrainz_id": "..." } ]
          }
        }
      ]
    }
  ],
  "unmatched": [
    {
      "source_id": "07.wav",
      "reason": "No AcoustID match above threshold",
      "best_guess": {
        "release_id": "...", "recording_id": "...",
        "acoustid_id": "...", "score": 0.22
      }
    }
  ],
  "diagnostics": {
    "candidate_releases_considered": 14,
    "split_count": 1,
    "files_in": 8,
    "files_matched": 7
  }
}
```

### Metadata fields

Both `metadata` blocks are derived from the MusicBrainz release fetch
(`recording-level-rels`, `work-level-rels`, `work-rels`, `artist-rels`,
`release-rels`, `genres`).

Release `metadata` (flat tags + structured credit arrays):

- Flat tags: `title`, `date`, `originaldate`, `country`, `type`, `label`,
  `catalognumber`, `barcode`, `script`, `cover_art_url`, `cover_art_thumb_url`,
  `musicbrainz_id`, `musicbrainz_release_group_id`
- Credit arrays: `artists`, `producers`, `engineers`, `mixers`, `conductors`,
  `arrangers`, `performers`

Track `metadata` (flat tags + structured credit arrays):

- Flat tags: `title`, `tracknumber`, `totaltracks`, `discnumber`, `totaldiscs`,
  `isrc`, `length_ms`, `media`, `genre`, `musicbrainz_trackid`,
  `musicbrainz_recordingid`
- Credit arrays: `artists`, `composers`, `lyricists`, `writers`, `arrangers`,
  `producers`, `engineers`, `mixers`, `conductors`, `performers`, `works`

Notes:

- The release/track artist is intentionally **not** emitted as a flat string —
  reconstruct it from the `artists` array (join `name`s for display, collect
  `musicbrainz_artistid` for the IDs).
- Each credit entry has `name`, `sort_name`, `musicbrainz_artistid`, `type`,
  `disambiguation` (`performers` also carry `attributes`); `works` entries have
  `title` and `musicbrainz_id`.
- `genre` is the top-N MB genres aggregated across recording, release, and
  release-group, joined with `; `.
- Empty flat tags are omitted from the response (rather than emitted as `null`
  or empty string); credit arrays are always present and may be empty.

## Make targets

| Target          | What it does                                |
| --------------- | ------------------------------------------- |
| `make install`  | `uv sync`                                   |
| `make lint`     | `ruff check`                                |
| `make format`   | `black` + `isort`                           |
| `make typecheck`| `mypy`                                      |
| `make test`     | `pytest` (cassette replay, no live calls)   |
| `make check`    | lint + typecheck + test                     |
| `make build`    | Docker build (passes `GIT_SHA` build arg)   |
| `make run`      | Docker run with `.env`                      |
| `make stop`     | Stop the container                          |
| `make clean`    | Remove caches and `.venv`                   |

## Tests

`pytest` + `pytest-recording` (VCR cassettes). The default mode is
`--record-mode=none` — cassettes replay only, no live API calls.

Layout:

```
tests/
  unit/                       # pure-function tests, no I/O
  integration/                # FastAPI TestClient + cassette replay
    cassettes/                # committed VCR cassettes
  fixtures/
    fingerprints.json         # Chromaprint fingerprints for test album
    sample_release_*.json     # MB release snapshots
    audio/                    # gitignored; provide your own .wav/.flac
```

Re-recording a cassette: delete the `*.yaml` under `tests/integration/cassettes/`
and run:

```bash
TAGGING_MS_ACOUSTID_API_KEY=<key> \
  uv run pytest tests/integration -k <name> --record-mode=once
```

VCR redacts `Authorization` and the AcoustID `client` POST/query parameter so
cassettes can be committed safely.

## Notes

- The service does not write files. It returns the resolved MusicBrainz tags
  for the client to apply locally.
- AcoustID lookups require `TAGGING_MS_ACOUSTID_API_KEY` in the environment.
- Release cover art is fetched and validated against the
  [Cover Art Archive](https://coverartarchive.org/); releases without art simply
  omit the `cover_art_*` fields.
- Per-host rate limiting (1 s for MusicBrainz, 333 ms for AcoustID, 1 s for the
  Cover Art Archive) is enforced by `tagging_ms.ratecontrol`.

## License

GPL-2.0, matching the upstream MusicBrainz Picard project from which the
matching and tag-extraction logic is derived. See [LICENSE](LICENSE).

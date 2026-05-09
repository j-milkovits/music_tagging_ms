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
        "album": "Album 1",
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

```jsonc
{
  "mode": "joint",
  "assignments": [
    {
      "release_id": "...",
      "joint_score": 17.42,
      "files": [
        {
          "source_id": "01.wav",
          "track_id": "...", "recording_id": "...",
          "score": 0.93,
          "applied_tags": { /* full tag set, see below */ }
        }
      ],
      "unmatched_files": []
    }
  ],
  "fallback_per_file": [],
  "diagnostics": {
    "candidate_releases_considered": 14,
    "split_count": 1,
    "files_in": 8,
    "files_matched": 8
  }
}
```

`assignments` is empty in `per-file` mode; everything lands in `fallback_per_file`.

### Applied tags

Each match payload includes a rich tag set derived from the MusicBrainz release
fetch (`recording-level-rels`, `work-level-rels`, `work-rels`, `artist-rels`,
`release-rels`, `genres`):

- Track tags: `title`, `artist`, `tracknumber`, `totaltracks`, `discnumber`,
  `totaldiscs`, `isrc`, `length_ms`, `media`
- Album tags: `album`, `albumartist`, `date`, `originaldate`, `releasetype`,
  `releasecountry`, `label`, `catalognumber`, `barcode`, `script`
- MusicBrainz IDs: `musicbrainz_albumid`, `musicbrainz_trackid`,
  `musicbrainz_recordingid`, `musicbrainz_releasegroupid`,
  `musicbrainz_artistid`, `musicbrainz_albumartistid`, `musicbrainz_workid`,
  `acoustid_id`
- Relationships: `work`, `composer`, `lyricist`, `writer`, `arranger`,
  `producer`, `engineer`, `mixer`, `conductor`, `performers`
- `genre` (top-N MB genres aggregated across recording, release, release-group)

Multi-valued fields are joined with `; `. Empty fields are omitted from the
response (rather than emitted as `null` or empty string).

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
- Per-host rate limiting (1 s for MusicBrainz, 333 ms for AcoustID) is enforced
  by `tagging_ms.ratecontrol`.

## License

GPL-2.0, matching the upstream MusicBrainz Picard project from which the
matching and tag-extraction logic is derived. See [LICENSE](LICENSE).

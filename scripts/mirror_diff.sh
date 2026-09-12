#!/usr/bin/env bash
# Diff ws/2 release responses between the public MusicBrainz API and a mirror.
# Usage: scripts/mirror_diff.sh [MIRROR_WS2_URL]   (default http://127.0.0.1:5000/ws/2)
# The release IDs are the ones the test cassettes already exercise.
set -u
MIRROR="${1:-http://127.0.0.1:5000/ws/2}"
PUBLIC="https://musicbrainz.org/ws/2"
INC='artists+artist-credits+recordings+release-groups+media+isrcs+labels+genres+recording-level-rels+work-level-rels+release-rels+artist-rels+work-rels'
UA='tagging-ms-mirror-diff/0.1 ( https://github.com/j-milkovits/music_tagging_ms )'
OUT="${TMPDIR:-/tmp}/mirror_diff"
mkdir -p "$OUT"
RELEASES=(
  2dc5dfc7-61df-4948-8c6b-e25a4cd039c8
  83bd362a-7de3-47a1-962f-c74b57e1b13e
  88b363cb-86b2-431f-84b4-525d4aa7aa62
  8e061dc4-790e-4587-ba53-011e7852f88d
  b9608c76-ae35-372d-9e05-af2c5e1caec3
)
rc=0
for id in "${RELEASES[@]}"; do
  curl -sf -A "$UA" "$PUBLIC/release/$id?fmt=json&inc=$INC" | jq -S . > "$OUT/pub_$id.json" || { echo "PUBLIC FAIL $id"; rc=1; continue; }
  curl -sf "$MIRROR/release/$id?fmt=json&inc=$INC" | jq -S . > "$OUT/mir_$id.json" || { echo "MIRROR FAIL $id"; rc=1; continue; }
  if diff -q "$OUT/pub_$id.json" "$OUT/mir_$id.json" >/dev/null; then
    echo "IDENTICAL $id"
  else
    n=$(diff "$OUT/pub_$id.json" "$OUT/mir_$id.json" | grep -c '^[<>]')
    echo "DIFFERS   $id ($n lines; see $OUT/{pub,mir}_$id.json)"
    rc=1
  fi
  sleep 1.1   # public API: 1 req/s
done
exit $rc

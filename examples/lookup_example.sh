#!/usr/bin/env bash
# Example: submit two files to /api/lookup in joint mode.
#
# Usage:
#   TAGGING_MS_API_KEY=<token> ./examples/lookup_example.sh path/to/track1.wav path/to/track2.wav
#
# Requires fpcalc (Chromaprint) and jq.
set -euo pipefail

: "${TAGGING_MS_API_KEY:?Set TAGGING_MS_API_KEY to your service bearer token}"
HOST="${TAGGING_MS_HOST:-http://127.0.0.1:8000}"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <audio-file> [<audio-file> ...]" >&2
  exit 1
fi

# Build items[] JSON array via fpcalc + jq.
items=$(
  for path in "$@"; do
    fpcalc -json "$path" \
      | jq --arg sid "$(basename "$path")" '{
          source_id: $sid,
          fingerprint: .fingerprint,
          duration: (.duration | floor),
        }'
  done | jq -s '.'
)

payload=$(jq -n --argjson items "$items" '{
  items: $items,
  joint: true,
  preferred_release_countries: ["DE", "XE", "XW"],
  thresholds: {
    min_per_file_score: 0.5,
    min_coverage: 0.6,
    split_margin: 0.15
  },
  search_limit: 10
}')

curl --silent --show-error --fail \
  -H "Authorization: Bearer $TAGGING_MS_API_KEY" \
  -H "Content-Type: application/json" \
  --data "$payload" \
  "$HOST/api/lookup" \
  | jq '.'

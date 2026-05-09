"""Unit tests for AcoustID response normalisation.

Covers the three latent bugs surfaced during plan verification:
- date dict coercion
- release-event dedupe
- track key naming
"""

from __future__ import annotations

from tagging_ms.acoustid import _coerce_date, _make_releases_node


def test_coerce_date_dict_with_full_ymd() -> None:
    assert _coerce_date({"year": 2013, "month": 5, "day": 20}) == "2013-05-20"


def test_coerce_date_dict_year_only() -> None:
    assert _coerce_date({"year": 2021}) == "2021"


def test_coerce_date_dict_year_month() -> None:
    assert _coerce_date({"year": 2014, "month": 1}) == "2014-01"


def test_coerce_date_passes_strings_through() -> None:
    assert _coerce_date("2014-01-09") == "2014-01-09"


def test_coerce_date_empty_inputs() -> None:
    assert _coerce_date(None) == ""
    assert _coerce_date("") == ""
    assert _coerce_date({}) == ""


def test_make_releases_node_preserves_release_id_uniqueness() -> None:
    """A release with multiple releaseevents must appear once per release id."""
    recording = {
        "id": "rec-1",
        "releasegroups": [
            {
                "id": "rg-1",
                "type": "Album",
                "title": "Album",
                "releases": [
                    {
                        "id": "rel-1",
                        "title": "Album",
                        "country": "FR",
                        "date": {"year": 2013},
                        "releaseevents": [
                            {"country": "DE", "date": {"year": 2013, "month": 5, "day": 20}},
                            {"country": "US", "date": {"year": 2013, "month": 5, "day": 21}},
                        ],
                        "mediums": [{"format": "CD", "track_count": 13}],
                    }
                ],
            }
        ],
    }
    nodes = _make_releases_node(recording)
    ids = [n["id"] for n in nodes]
    assert len(nodes) == 1
    assert ids == ["rel-1"]
    # Date is normalised to ISO string.
    assert nodes[0]["date"] == "2013-05-20"
    assert nodes[0]["country"] == "DE"


def test_make_releases_node_handles_no_events() -> None:
    recording = {
        "id": "rec-2",
        "releasegroups": [
            {
                "id": "rg-2",
                "type": "Single",
                "releases": [
                    {
                        "id": "rel-2",
                        "title": "Single",
                        "country": "GB",
                        "date": {"year": 2020, "month": 3},
                        "mediums": [],
                    }
                ],
            }
        ],
    }
    nodes = _make_releases_node(recording)
    assert len(nodes) == 1
    assert nodes[0]["date"] == "2020-03"
    assert nodes[0]["country"] == "GB"


def test_make_releases_node_keeps_tracks_under_correct_key() -> None:
    """The 'tracks' key (plural) must be used, matching MB conventions."""
    recording = {
        "id": "rec-3",
        "releasegroups": [
            {
                "id": "rg-3",
                "releases": [
                    {
                        "id": "rel-3",
                        "title": "X",
                        "mediums": [
                            {
                                "format": "CD",
                                "track_count": 2,
                                "position": 1,
                                "tracks": [
                                    {"id": "t1", "position": 1},
                                    {"id": "t2", "position": 2},
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    nodes = _make_releases_node(recording)
    assert nodes[0]["media"][0]["tracks"][0]["id"] == "t1"
    # Singular 'track' was a previous bug; ensure it's gone.
    assert "track" not in nodes[0]["media"][0]

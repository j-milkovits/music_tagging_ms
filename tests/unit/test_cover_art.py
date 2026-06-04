"""Unit tests for Cover Art Archive fetching in MusicBrainzClient."""

from __future__ import annotations

import urllib.error

import pytest

from tagging_ms import musicbrainz
from tagging_ms.musicbrainz import MusicBrainzClient, _select_front_image


def test_select_front_image_prefers_front_flag() -> None:
    images = [
        {"image": "back.jpg", "front": False},
        {"image": "front.jpg", "front": True},
    ]
    assert _select_front_image(images)["image"] == "front.jpg"


def test_select_front_image_falls_back_to_first() -> None:
    images = [None, {"image": "only.jpg", "front": False}]
    assert _select_front_image(images)["image"] == "only.jpg"


def test_select_front_image_empty() -> None:
    assert _select_front_image([]) is None


def test_get_release_cover_art_returns_front_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "images": [
            {
                "front": True,
                "image": "http://coverartarchive.org/release/rid/1.jpg",
                "thumbnails": {"250": "http://coverartarchive.org/release/rid/1-250.jpg"},
            }
        ]
    }
    monkeypatch.setattr(musicbrainz.ratecontrol, "send_json", lambda factory, url: payload)
    result = MusicBrainzClient().get_release_cover_art("rid")
    assert result == {
        "cover_art_url": "http://coverartarchive.org/release/rid/1.jpg",
        "cover_art_thumb_url": "http://coverartarchive.org/release/rid/1-250.jpg",
    }


def test_get_release_cover_art_404_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_404(factory, url):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(musicbrainz.ratecontrol, "send_json", raise_404)
    assert MusicBrainzClient().get_release_cover_art("rid") is None


def test_get_release_cover_art_no_images_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        musicbrainz.ratecontrol, "send_json", lambda factory, url: {"images": []}
    )
    assert MusicBrainzClient().get_release_cover_art("rid") is None

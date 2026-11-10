"""
apps/learning/video_utils.py
------------------------------
Video URL normalisation and embed helpers for lesson pages.

Supported formats
-----------------
YouTube:
  https://www.youtube.com/watch?v=VIDEO_ID
  https://www.youtube.com/watch?v=VIDEO_ID&t=30s   (extra params stripped)
  https://youtu.be/VIDEO_ID
  https://youtu.be/VIDEO_ID?t=30
  https://www.youtube.com/shorts/VIDEO_ID
  https://www.youtube.com/embed/VIDEO_ID
  https://www.youtube.com/live/VIDEO_ID

Vimeo:
  https://vimeo.com/VIDEO_ID
  https://vimeo.com/channels/CHANNEL/VIDEO_ID
  https://player.vimeo.com/video/VIDEO_ID

Google Drive:
  https://drive.google.com/file/d/FILE_ID/view
  https://drive.google.com/open?id=FILE_ID

Any other URL: shown as a plain link, no embed.

Privacy note
------------
All YouTube embeds use youtube-nocookie.com. This is YouTube's own privacy-
enhanced domain — it does not set cookies until the student explicitly presses
play, which reduces tracking and is better practice in a school context.
The thumbnail is still loaded from img.youtube.com (a static image CDN, no
cookies, no tracking). This is the only external image source loaded by the
lesson page.

Public API
----------
  parse_video_url(url) → VideoInfo dataclass
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse


@dataclass
class VideoInfo:
    provider: str              # "youtube" | "vimeo" | "drive" | "other"
    video_id: Optional[str]    # YouTube ID or Vimeo ID; None for drive/other
    embed_url: Optional[str]   # URL for the iframe src (None if not embeddable)
    thumb_url: Optional[str]   # Thumbnail image URL (None if not available)
    original_url: str          # The teacher-entered URL, always available

    @property
    def is_embeddable(self) -> bool:
        return self.embed_url is not None

    @property
    def is_youtube(self) -> bool:
        return self.provider == "youtube"

    @property
    def is_vimeo(self) -> bool:
        return self.provider == "vimeo"

    @property
    def is_drive(self) -> bool:
        return self.provider == "drive"


_YT_PATTERN = re.compile(
    r"""
    (?:https?://)?                          # optional scheme
    (?:
        (?:www\.|m\.)?youtube(?:-nocookie)?\.com
        (?:
            /watch\?.*?v=([A-Za-z0-9_-]{11})   # /watch?v=ID
          | /shorts/([A-Za-z0-9_-]{11})         # /shorts/ID
          | /embed/([A-Za-z0-9_-]{11})          # /embed/ID
          | /live/([A-Za-z0-9_-]{11})           # /live/ID
          | /v/([A-Za-z0-9_-]{11})              # /v/ID (old)
        )
      | youtu\.be/([A-Za-z0-9_-]{11})           # youtu.be/ID
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

_VIMEO_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?(?:vimeo\.com|player\.vimeo\.com/video)"
    r"/(?:[^/]+/)*?(\d+)",
    re.IGNORECASE,
)

_DRIVE_PATTERN = re.compile(
    r"(?:https?://)?drive\.google\.com",
    re.IGNORECASE,
)


def parse_video_url(url: str) -> VideoInfo:
    """
    Parse *url* and return a VideoInfo describing how to embed and thumbnail it.

    All YouTube embeds are served from youtube-nocookie.com.
    """
    url = (url or "").strip()
    if not url:
        return VideoInfo(
            provider="other", video_id=None,
            embed_url=None, thumb_url=None, original_url=url,
        )

    # ── YouTube ──────────────────────────────────────────────────────────
    m = _YT_PATTERN.search(url)
    if m:
        video_id = next(g for g in m.groups() if g)
        embed_url = (
            f"https://www.youtube-nocookie.com/embed/{video_id}"
            "?autoplay=1&rel=0&modestbranding=1"
        )
        thumb_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
        return VideoInfo(
            provider="youtube", video_id=video_id,
            embed_url=embed_url, thumb_url=thumb_url, original_url=url,
        )

    # ── Vimeo ─────────────────────────────────────────────────────────────
    m = _VIMEO_PATTERN.search(url)
    if m:
        video_id = m.group(1)
        embed_url = f"https://player.vimeo.com/video/{video_id}?autoplay=1"
        return VideoInfo(
            provider="vimeo", video_id=video_id,
            embed_url=embed_url, thumb_url=None, original_url=url,
        )

    # ── Google Drive ──────────────────────────────────────────────────────
    if _DRIVE_PATTERN.search(url):
        return VideoInfo(
            provider="drive", video_id=None,
            embed_url=None, thumb_url=None, original_url=url,
        )

    # ── Any other URL ─────────────────────────────────────────────────────
    return VideoInfo(
        provider="other", video_id=None,
        embed_url=None, thumb_url=None, original_url=url,
    )

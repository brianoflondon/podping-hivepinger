from enum import StrEnum
from typing import List

from pydantic import BaseModel, Field

CURRENT_PODPING_VERSION = "1.1"


class Mediums(StrEnum):
    MIXED = "mixed"
    PODCAST = "podcast"
    PODCAST_LIVE = "podcastL"
    MUSIC = "music"
    MUSIC_LIVE = "musicL"
    VIDEO = "video"
    VIDEO_LIVE = "videoL"
    FILM = "film"
    FILM_LIVE = "filmL"
    AUDIOBOOK = "audiobook"
    AUDIOBOOK_LIVE = "audiobookL"
    NEWSLETTER = "newsletter"
    NEWSLETTER_LIVE = "newsletterL"
    BLOG = "blog"
    BLOG_LIVE = "blogL"
    PUBLISHER = "publisher"
    PUBLISHER_LIVE = "publisherL"
    COURSE = "course"
    COURSE_LIVE = "courseL"


class Reasons(StrEnum):
    UPDATE = "update"
    LIVE = "live"
    LIVE_END = "liveEnd"


class Podping(BaseModel):
    """Dataclass for on-chain podping schema"""

    version: str = Field(CURRENT_PODPING_VERSION, description="Version of the podping schema")
    medium: Mediums = Field(..., description="Medium of the podping")
    reason: Reasons = Field(..., description="Reason for the podping")
    iris: List[str] = Field(..., description="List of IRIs associated with the podping")
    timestampNs: int = Field(..., description="Timestamp in nanoseconds")
    sessionId: int = Field(..., description="Session ID associated with the podping")

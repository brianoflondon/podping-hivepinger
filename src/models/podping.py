from enum import StrEnum
from typing import List

from pydantic import BaseModel, Field

from app import __version__  # absolute import to support running as a script

CURRENT_PODPING_VERSION = "1.1"


class Medium(StrEnum):
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


class Reason(StrEnum):
    UPDATE = "update"
    LIVE = "live"
    LIVE_END = "liveEnd"
    NEW_IRI = "newIRI"


class Podping(BaseModel):
    """Dataclass for on-chain podping schema"""

    # ``default=`` makes the parameter visible to static analysis tools so that
    # calls to ``Podping(...)`` without ``version`` do not raise warnings.
    version: str = Field(
        default=CURRENT_PODPING_VERSION, description="Version of the podping schema"
    )
    medium: Medium = Field(..., description="Medium of the podping")
    reason: Reason = Field(..., description="Reason for the podping")
    iris: List[str] = Field(..., description="List of IRIs associated with the podping")
    timestampNs: int = Field(..., description="Timestamp in nanoseconds")
    sessionId: int = Field(..., description="Session ID associated with the podping")


class StartupPodping(BaseModel):
    """Model for the inner custom_json object used in hive operations.

    Example payload:
    {
        "server_account": "podping.ddd",
        "message": "Podping startup complete",
        "uuid": "3182f286-df46-4506-8369-746cd34645f2",
        "hive": "https://api.openhive.network",
        "sessionId": 13314988016174307000,
        "v": "2.1.0"
    }
    """

    server_account: str = Field(
        ..., description="Hive account name of the server sending the podping"
    )
    message: str = Field(..., description="Message describing the startup event")
    uuid: str = Field(..., description="Unique identifier for this startup event")
    hive: str = Field(..., description="Hive node URL used for pinging")
    sessionId: int
    v: str = Field(__version__, description="Version of the pinging app")
    pinging_app: str = Field("hivepinger", description="Name of the app sending the podping")


class HiveOperationId:
    def __init__(
        self,
        prefix: str,
        medium: Medium = Medium.PODCAST,
        reason: Reason = Reason.UPDATE,
        startup: bool = False,
    ):
        self.prefix: str = prefix
        self.medium: Medium = medium
        self.reason: Reason = reason
        self.startup: bool = startup

    def __eq__(self, other):
        return str(self) == str(other)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(str(self))

    def __str__(self):
        if not self.startup:
            return f"{self.prefix}_{self.medium}_{str(self.reason).replace('_', '-')}"
        return f"{self.prefix}_startup"

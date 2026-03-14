from enum import StrEnum
from time import time
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from hivepinger import __version__

CURRENT_PODPING_VERSION = "1.1"


# Capnp ordinals match the @N annotations in podping_medium.capnp from
# https://github.com/Podcastindex-org/podping-schemas
_MEDIUM_CAPNP_ORDINALS: dict[str, int] = {
    "mixed": 0,
    "podcast": 1,
    "podcastL": 2,
    "music": 3,
    "musicL": 4,
    "video": 5,
    "videoL": 6,
    "film": 7,
    "filmL": 8,
    "audiobook": 9,
    "audiobookL": 10,
    "newsletter": 11,
    "newsletterL": 12,
    "blog": 13,
    "blogL": 14,
    "publisher": 15,
    "publisherL": 16,
    "course": 17,
    "courseL": 18,
}


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

    @property
    def capnp_ordinal(self) -> int:
        """Return the Cap'n Proto enum ordinal for this medium."""
        return _MEDIUM_CAPNP_ORDINALS[self.value]


# Capnp ordinals match the @N annotations in podping_reason.capnp.
# NEW_IRI is a hivepinger-only extension not present in the capnp schema;
# it maps to the "update" ordinal (0) when sent over the gossip wire.
_REASON_CAPNP_ORDINALS: dict[str, int] = {
    "update": 0,
    "live": 1,
    "liveEnd": 2,
    "newIRI": 0,  # not in capnp schema; falls back to update
}


class Reason(StrEnum):
    UPDATE = "update"
    LIVE = "live"
    LIVE_END = "liveEnd"
    NEW_IRI = "newIRI"

    @property
    def capnp_ordinal(self) -> int:
        """Return the Cap'n Proto enum ordinal for this reason."""
        return _REASON_CAPNP_ORDINALS[self.value]


class Podping(BaseModel):
    """Dataclass for on-chain podping schema"""

    # ``default=`` makes the parameter visible to static analysis tools so that
    # calls to ``Podping(...)`` without ``version`` do not raise warnings.
    version: str = Field(
        default=CURRENT_PODPING_VERSION, description="Version of the podping schema"
    )
    medium: Medium = Field(Medium.PODCAST, description="Medium of the podping")
    reason: Reason = Field(Reason.UPDATE, description="Reason for the podping")
    iris: List[str] = Field(
        default_factory=list, description="List of IRIs associated with the podping"
    )
    timestampNs: int = Field(
        default_factory=lambda: int(time() * 1e9), description="Timestamp in nanoseconds"
    )
    sessionId: int = Field(0, description="Session ID associated with the podping")


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


class HiveTrxID:
    def __init__(self, trx_id: str = "", trx: Dict[str, Any] | None = None):
        if trx is not None:
            self.trx_id = trx.get("trx_id", "")
        else:
            self.trx_id = trx_id

    def __str__(self) -> str:
        return self.trx_id

    @property
    def link(self, hive_url: str = "https://hive.ausbit.dev/tx/") -> str:
        return f"{hive_url}{self.trx_id}"

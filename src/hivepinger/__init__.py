# root_package/__init__.py
from pathlib import Path

from single_source import get_version

# ``single-source`` is handy when running from a working tree (development)
# because it scans the repository root for pyproject.toml or the version file.
# However, our Docker runtime stage strips out the pyproject, and installed
# distributions already expose their version via metadata.  To cover both
# scenarios we prefer importlib.metadata when available and fall back to the
# single-source helper only if the package isn't installed.

try:
    # when the package is installed into a virtual environment the version
    # can be obtained from the distribution metadata; this works even if the
    # pyproject.toml isn't present at runtime (such as inside the container).
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    try:
        __version__ = _pkg_version("podping-hivepinger")
    except PackageNotFoundError:  # noqa: PERF203
        # not installed, probably running from source in a checkout
        __version__ = (
            get_version(__name__, Path(__file__).parent.parent.parent, default_return="0.0.0")
            or "0.0.0"
        )
except ImportError:  # Python <3.8 shouldn't happen but be defensive
    __version__ = (
        get_version(__name__, Path(__file__).parent.parent.parent, default_return="0.0.0")
        or "0.0.0"
    )

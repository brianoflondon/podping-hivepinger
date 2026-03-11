import pytest

from importlib.metadata import PackageNotFoundError, version as pkg_version

from hivepinger import __version__


def test_version_not_default():
    # the version should never be the hard-coded default; it should either come
    # from package metadata or, when running from a checkout, from the
    # pyproject.toml via ``single_source``.  this catches regressions where the
    # lookup fails and we silently return "0.0.0".
    assert __version__ != "0.0.0"


def test_version_matches_metadata_when_available():
    # if the distribution metadata is present it should agree with
    # ``__version__``.  this is the behaviour used in the Docker image.
    try:
        dist_ver = pkg_version("podping-hivepinger")
    except PackageNotFoundError:
        pytest.skip("package metadata not installed in this environment")
    assert __version__ == dist_ver

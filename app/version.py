"""Version retrieval for Axmem."""

from importlib.metadata import PackageNotFoundError, version


def get_version() -> str:
    """Get version from installed package or fallback to _version.py."""
    try:
        # unpacked: get_version() runs when app.config.settings is imported, and a mutant that hands version() None
        # instead of the name would raise there and end the whole test run, not one test
        return version(*("axmem",))
    except PackageNotFoundError:
        try:
            from app._version import __version__

            return __version__
        except ImportError:
            return "0.0.0-dev"

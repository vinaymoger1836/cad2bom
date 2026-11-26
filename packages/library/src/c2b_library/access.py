"""Server-side access to the library a job is pinned to (ADR-019).

The API and the worker call these instead of reading `C2B_LIBRARY_DIR` directly, so a published
library version changes new jobs only.
"""

import logging

from c2b_jobs.settings import get_settings
from c2b_library import versions
from c2b_library.loader import Library
from c2b_library.versions import VersionError

log = logging.getLogger("c2b.library")

__all__ = ["VersionError", "active_version", "pinned_library", "seed_store"]


def active_version() -> str:
    """The version a new job is pinned to (read fresh: an admin publish takes effect at once)."""
    s = get_settings()
    return versions.active_version(s.library_store_dir, s.library_dir)


def pinned_library(version: str) -> Library:
    """The library of an existing job. Raises `VersionError` if it cannot be loaded.

    Jobs made before library versions were stored (before P8) carry a version label that no
    longer exists as files; they ran with whatever library the image had, so they use the
    image's library again, with a warning (ADR-019).
    """
    s = get_settings()
    try:
        return versions.resolve_library(s.library_store_dir, s.library_dir, version)
    except VersionError:
        if not versions.VERSION_RE.match(version):
            raise
        log.warning("library %s is not in the store; using the image library", version)
        base = versions.baseline_version(s.library_dir)
        return versions.resolve_library(s.library_store_dir, s.library_dir, base)


def seed_store() -> str:
    """Make the image's library available as a version in the store (API start-up)."""
    s = get_settings()
    return versions.seed(s.library_store_dir, s.library_dir)

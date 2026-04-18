"""Release metadata helpers shared by startup and runtime paths."""
from __future__ import annotations

import os
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version


def package_version() -> str:
    configured = os.getenv("DRAINO_APP_VERSION", "").strip()
    if configured:
        return configured
    try:
        return version("draino")
    except PackageNotFoundError:
        return "unknown"


def short_sha(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip()
    if value.startswith("sha256:"):
        return value[len("sha256:"):][:12]
    return value[:12]


def release_metadata(resolve_current_digest: Callable[[], str | None] | None = None) -> dict[str, str | None]:
    build_sha = os.getenv("DRAINO_BUILD_SHA", "").strip()
    current_digest = ""
    sha_source = None

    if build_sha:
        current_digest = build_sha
        sha_source = "build_sha"
    elif resolve_current_digest is not None:
        resolved = resolve_current_digest()
        if resolved:
            current_digest = resolved
            sha_source = "running_pod"

    return {
        "version": package_version(),
        "image_repository": os.getenv("DRAINO_IMAGE_REPOSITORY", "").strip() or None,
        "image_tag": os.getenv("DRAINO_IMAGE_TAG", "").strip() or None,
        "current_digest": current_digest or None,
        "short_sha": short_sha(current_digest),
        "sha_source": sha_source,
        "pod_name": (os.getenv("DRAINO_POD_NAME") or os.getenv("HOSTNAME") or "").strip() or None,
        "pod_namespace": os.getenv("DRAINO_POD_NAMESPACE", "").strip() or None,
    }

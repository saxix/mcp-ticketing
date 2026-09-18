"""Helpers for validating local image files before attaching them to tickets."""

_IMAGE_SIGNATURES: tuple[tuple[bytes, ...], ...] = (
    (b"\x89PNG\r\n\x1a\n",),  # PNG
    (b"\xff\xd8\xff",),  # JPEG
    (b"GIF87a", b"GIF89a"),  # GIF
    (b"BM",),  # BMP
    (b"II*\x00", b"MM\x00*"),  # TIFF
)


def is_image_file(path: str) -> bool:
    """Return True when the file at ``path`` starts with a known image signature."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return False
    if not head:
        return False
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return True
    return any(head.startswith(sig) for sigs in _IMAGE_SIGNATURES for sig in sigs)


def read_image_file(path: str) -> bytes | None:
    """Read the raw bytes of the image at ``path``, or None on failure.

    Callers should run this through ``asyncio.to_thread`` to avoid blocking
    the event loop.
    """
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None

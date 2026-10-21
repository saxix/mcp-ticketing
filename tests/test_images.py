"""Unit tests for image signature detection and reading."""

import pytest

from mcp_ticketing._images import is_image_file, read_image_file

PNG = b"\x89PNG\r\n\x1a\n" + b"payload"
JPEG = b"\xff\xd8\xff" + b"payload"
GIF87 = b"GIF87a" + b"payload"
GIF89 = b"GIF89a" + b"payload"
BMP = b"BM" + b"payload"
TIFF_LE = b"II*\x00" + b"payload"
TIFF_BE = b"MM\x00*" + b"payload"
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"payload"


@pytest.mark.parametrize("payload", [PNG, JPEG, GIF87, GIF89, BMP, TIFF_LE, TIFF_BE, WEBP])
def test_is_image_file_recognizes_signatures(tmp_path, payload):
    path = tmp_path / "image.bin"
    path.write_bytes(payload)
    assert is_image_file(str(path)) is True


@pytest.mark.parametrize("payload", [b"", b"not an image", b"RIFFxxxxNOPE"])
def test_is_image_file_rejects_non_images(tmp_path, payload):
    path = tmp_path / "data.bin"
    path.write_bytes(payload)
    assert is_image_file(str(path)) is False


def test_is_image_file_missing_path():
    assert is_image_file("/nonexistent/definitely-not-here.png") is False


def test_is_image_file_directory(tmp_path):
    assert is_image_file(str(tmp_path)) is False


def test_read_image_file_roundtrip(tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(PNG)
    assert read_image_file(str(path)) == PNG


def test_read_image_file_missing_path():
    assert read_image_file("/nonexistent/definitely-not-here.png") is None

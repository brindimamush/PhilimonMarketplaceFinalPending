# app/core/images.py
# LAYER: Domain / Core
# PURPOSE: Validates uploaded images.

from io import BytesIO

from PIL import Image, UnidentifiedImageError

from app.core.exceptions import LocalizedDomainError

FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


def validate_image_bytes(
    data: bytes,
    *,
    max_bytes: int,
    allowed_mimes: set[str],
) -> None:
    """
    Validates image size and decodability.

    Spec requirement:
    Do not trust file extensions or Telegram filenames.
    """
    if len(data) > max_bytes:
        raise LocalizedDomainError("error.unexpected_image")

    try:
        img = Image.open(BytesIO(data))
        img.verify()
    except (UnidentifiedImageError, Exception):
        raise LocalizedDomainError("error.unexpected_image")

    # Reopen after verify
    img = Image.open(BytesIO(data))
    image_format = (img.format or "").upper()
    mime = FORMAT_TO_MIME.get(image_format)

    if not mime or mime not in allowed_mimes:
        raise LocalizedDomainError("error.unexpected_image")
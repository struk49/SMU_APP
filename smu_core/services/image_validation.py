"""Bounded raster decoding. Never dispatch to document/image delegates."""

from io import BytesIO
import warnings

from PIL import Image, UnidentifiedImageError


RASTER_FORMATS = ("JPEG", "PNG", "GIF", "WEBP")
# Match the existing social-text byte/pixel budget; allow large phone dimensions.
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_DIMENSION = 8192

# GIF seek() can allocate disposal buffers before returning a frame to us.
# Bound Pillow's internal checks too, never relaxing an existing stricter cap.
if Image.MAX_IMAGE_PIXELS is None or Image.MAX_IMAGE_PIXELS > MAX_IMAGE_PIXELS:
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class ImageValidationError(ValueError):
    """Contains only a safe message suitable for an upload form."""

    def __init__(self, message, *, reason="invalid_image"):
        super().__init__(message)
        self.reason = reason


def _read(source, limit):
    if isinstance(source, bytes):
        return source[:limit]
    try:
        source.seek(0)
        try:
            data = source.read(limit)
            if not isinstance(data, bytes):
                raise ImageValidationError("Could not read the uploaded image. Please select it again.")
            return data
        finally:
            source.seek(0)
    except (AttributeError, OSError, ValueError) as exc:
        raise ImageValidationError("Could not read the uploaded image. Please select it again.") from exc


def reject_document_upload(source):
    # Also prevent obvious document payloads masquerading as videos from reaching
    # the external auto-detection uploader. This is not video validation.
    prefix = _read(source, 1024).lstrip()
    if prefix.startswith((b"%!PS", b"%PDF-", b"\xc5\xd0\xd3\xc6")):
        raise ImageValidationError("EPS, PostScript and PDF uploads are not supported. Choose a raster image or video.")


def validate_raster_image(source, *, max_dimension=None):
    """Validate all raster frames, returning original bytes without altering media."""
    data = _read(source, MAX_IMAGE_BYTES + 1)
    if not isinstance(data, bytes) or not data:
        raise ImageValidationError("The uploaded image is empty or unreadable.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageValidationError("Images must be 20 MiB or smaller.")
    reject_document_upload(data)
    dimension_limit = MAX_IMAGE_DIMENSION if max_dimension is None else max_dimension

    def check_size(image, total=0):
        width, height = image.size
        pixels = width * height
        if (width < 1 or height < 1 or max(width, height) > dimension_limit
                or pixels + total > MAX_IMAGE_PIXELS):
            raise ImageValidationError(
                f"Images must fit within {dimension_limit} pixels per side and 16 million decoded pixels across all frames.",
                reason="image_dimensions_unsupported",
            )
        return total + pixels

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            # Restrict decoder selection before header inspection or pixel loading.
            with Image.open(BytesIO(data), formats=RASTER_FORMATS) as image:
                check_size(image)
                # Pillow treats a missing GIF trailer as ordinary end-of-frames.
                if image.format == "GIF" and not data.endswith(b";"):
                    raise ImageValidationError("The GIF image is incomplete. Please upload a complete file.")
                image.verify()
            with Image.open(BytesIO(data), formats=RASTER_FORMATS) as image:
                total = 0
                frame = 0
                while True:
                    total = check_size(image, total)
                    image.load()  # verify() alone does not detect every truncation.
                    frame += 1
                    try:
                        image.seek(frame)
                    except EOFError:
                        break
    except ImageValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageValidationError("The uploaded image has too many decoded pixels.", reason="image_dimensions_unsupported") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError) as exc:
        raise ImageValidationError("Upload a complete, valid JPEG, PNG, GIF or WebP image. This file is unsupported or damaged.") from exc
    return data

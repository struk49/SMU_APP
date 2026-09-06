"""Deterministic, in-memory text rendering for social images."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError


FONT_PATH = (
    Path(__file__).resolve().parents[2]
    / "assets"
    / "fonts"
    / "SMUSocialText-Regular.ttf"
)

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_DIMENSION = 4096
MAX_PIXELS = 16_000_000
MAX_FIT_ITERATIONS = 24
MIN_FONT_SIZE = 18

TEXT_LIMITS = {
    "title": 180,
    "body": 600,
    "cta": 120,
    "brand": 120,
}

LINE_LIMITS = {
    "title": 4,
    "body": 8,
    "cta": 2,
    "brand": 2,
}


class SocialTextRenderError(ValueError):
    """A safe, categorical rendering failure that never contains user copy."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _validated_text(name, value, *, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise SocialTextRenderError("invalid_text")

    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise SocialTextRenderError("invalid_text") from exc

    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if any(ord(character) < 32 and character not in "\n\t" for character in value):
        raise SocialTextRenderError("invalid_text")
    if len(value) > TEXT_LIMITS[name]:
        raise SocialTextRenderError("text_limit_exceeded")
    if required and not value:
        raise SocialTextRenderError("title_required")
    return value


def _load_font(size):
    try:
        return ImageFont.truetype(str(FONT_PATH), size=size)
    except (OSError, ValueError) as exc:
        raise SocialTextRenderError("font_unavailable") from exc


def _line_width(draw, text, font):
    box = draw.textbbox((0, 0), text or " ", font=font)
    return box[2] - box[0]


def _wrap_text(draw, text, font, max_width):
    lines = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue

        line = words[0]
        if _line_width(draw, line, font) > max_width:
            return None

        for word in words[1:]:
            candidate = f"{line} {word}"
            if _line_width(draw, candidate, font) <= max_width:
                line = candidate
            else:
                lines.append(line)
                line = word
                if _line_width(draw, line, font) > max_width:
                    return None
        lines.append(line)
    return lines


def _fit_block(draw, text, *, max_width, max_height, max_lines, start_size):
    start_size = max(start_size, MIN_FONT_SIZE)
    for iteration in range(MAX_FIT_ITERATIONS):
        if MAX_FIT_ITERATIONS == 1:
            size = MIN_FONT_SIZE
        else:
            size = round(
                start_size
                - (start_size - MIN_FONT_SIZE)
                * iteration
                / (MAX_FIT_ITERATIONS - 1)
            )
        font = _load_font(size)
        lines = _wrap_text(draw, text, font, max_width)
        spacing = max(4, size // 5)
        if lines is not None and len(lines) <= max_lines:
            rendered = "\n".join(lines)
            box = draw.multiline_textbbox(
                (0, 0), rendered, font=font, spacing=spacing
            )
            if box[2] - box[0] <= max_width and box[3] - box[1] <= max_height:
                return font, rendered, spacing

    raise SocialTextRenderError("text_does_not_fit")


def _draw_block(draw, text, box, *, max_lines, start_size):
    if not text:
        return
    left, top, right, bottom = box
    font, rendered, spacing = _fit_block(
        draw,
        text,
        max_width=right - left,
        max_height=bottom - top,
        max_lines=max_lines,
        start_size=start_size,
    )
    draw.multiline_text(
        (left, top),
        rendered,
        font=font,
        fill=(255, 255, 255),
        spacing=spacing,
        align="left",
        stroke_width=max(1, font.size // 28),
        stroke_fill=(0, 0, 0),
    )


def render_social_text(
    image_bytes,
    *,
    title,
    body=None,
    cta=None,
    brand=None,
    layout="carousel",
):
    """Render structured copy onto an image and return in-memory PNG bytes."""
    if layout != "carousel":
        raise SocialTextRenderError("unsupported_layout")
    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise SocialTextRenderError("invalid_image")
    if len(image_bytes) > MAX_INPUT_BYTES:
        raise SocialTextRenderError("image_too_large")

    title = _validated_text("title", title, required=True)
    body = _validated_text("body", body)
    cta = _validated_text("cta", cta)
    brand = _validated_text("brand", brand)

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            width, height = source.size
            if (
                width < 1
                or height < 1
                or width > MAX_DIMENSION
                or height > MAX_DIMENSION
                or width * height > MAX_PIXELS
            ):
                raise SocialTextRenderError("image_dimensions_unsupported")
            image = source.convert("RGBA")
    except SocialTextRenderError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise SocialTextRenderError("invalid_image") from exc

    width, height = image.size
    margin = max(16, round(min(width, height) * 0.06))
    if width - 2 * margin < MIN_FONT_SIZE or height - 2 * margin < 4 * MIN_FONT_SIZE:
        raise SocialTextRenderError("image_dimensions_unsupported")

    draw = ImageDraw.Draw(image)
    content_width = width - 2 * margin
    title_box = (margin, margin, width - margin, round(height * 0.32))
    body_box = (margin, round(height * 0.35), width - margin, round(height * 0.64))
    cta_box = (margin, round(height * 0.70), width - margin, round(height * 0.82))
    brand_box = (margin, round(height * 0.88), width - margin, height - margin)

    _draw_block(
        draw,
        title,
        title_box,
        max_lines=LINE_LIMITS["title"],
        start_size=max(MIN_FONT_SIZE, round(content_width * 0.075)),
    )
    _draw_block(
        draw,
        body,
        body_box,
        max_lines=LINE_LIMITS["body"],
        start_size=max(MIN_FONT_SIZE, round(content_width * 0.042)),
    )
    _draw_block(
        draw,
        cta,
        cta_box,
        max_lines=LINE_LIMITS["cta"],
        start_size=max(MIN_FONT_SIZE, round(content_width * 0.045)),
    )
    _draw_block(
        draw,
        brand,
        brand_box,
        max_lines=LINE_LIMITS["brand"],
        start_size=max(MIN_FONT_SIZE, round(content_width * 0.032)),
    )

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()

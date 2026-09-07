"""Deterministic, in-memory text rendering for social images."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageStat, UnidentifiedImageError


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

LAYOUT_ROLES = {"cover", "phrase", "info", "cta"}
ROLE_DESIGN_LAYOUTS = {
    "cover": "hero",
    "phrase": "split",
    "info": "editorial",
    "cta": "cta",
}
STYLE_TOKENS = {
    "default": {
        "headline": 1.00, "body": 1.00, "region_width": 1.00,
        "gap": 1.00, "accent": "line", "strength": 0.70, "surface_alpha": 112,
    },
    "realistic": {
        "headline": 0.90, "body": 0.96, "region_width": 0.90,
        "gap": 1.05, "accent": "line", "strength": 0.22, "surface_alpha": 96,
    },
    "viral_carousel": {
        "headline": 1.24, "body": 1.04, "region_width": 1.08,
        "gap": 0.76, "accent": "block", "strength": 0.90, "surface_alpha": 124,
    },
    "luxury": {
        "headline": 0.96, "body": 0.94, "region_width": 0.84,
        "gap": 1.55, "accent": "line", "strength": 0.24, "surface_alpha": 92,
    },
    "minimal": {
        "headline": 1.02, "body": 0.96, "region_width": 0.88,
        "gap": 1.35, "accent": "line", "strength": 0.10, "surface_alpha": 88,
    },
    "corporate": {
        "headline": 1.06, "body": 1.00, "region_width": 0.98,
        "gap": 1.10, "accent": "divider", "strength": 0.58, "surface_alpha": 108,
    },
    "pixar": {
        "headline": 1.14, "body": 1.03, "region_width": 1.04,
        "gap": 1.18, "accent": "circle", "strength": 0.68, "surface_alpha": 106,
    },
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


def _validate_font_support(*values):
    font = _load_font(32)
    missing_mask = font.getmask("\u0378")
    missing_signature = (missing_mask.size, bytes(missing_mask))
    for value in values:
        for character in value:
            if character.isspace():
                continue
            mask = font.getmask(character)
            if character == "\ufffd" or (mask.size, bytes(mask)) == missing_signature:
                raise SocialTextRenderError("unsupported_text_character")


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


def _fit_block(
    draw,
    text,
    *,
    max_width,
    max_height,
    max_lines,
    start_size,
    min_size=MIN_FONT_SIZE,
    preferred_max_lines=None,
):
    min_size = max(min_size, MIN_FONT_SIZE)
    start_size = max(start_size, min_size)
    line_targets = [max_lines]
    if preferred_max_lines and preferred_max_lines < max_lines:
        line_targets.insert(0, preferred_max_lines)
    for line_target in line_targets:
        for iteration in range(MAX_FIT_ITERATIONS):
            if MAX_FIT_ITERATIONS == 1:
                size = min_size
            else:
                size = round(
                    start_size
                    - (start_size - min_size)
                    * iteration
                    / (MAX_FIT_ITERATIONS - 1)
                )
            font = _load_font(size)
            lines = _wrap_text(draw, text, font, max_width)
            spacing = max(4, size // 5)
            if lines is not None and len(lines) <= line_target:
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
    panel_padding = max(10, round(min(right - left, bottom - top) * 0.05))
    stroke_width = 1
    font, rendered, spacing = _fit_block(
        draw,
        text,
        max_width=right - left - 2 * (panel_padding + stroke_width),
        max_height=bottom - top - 2 * (panel_padding + stroke_width),
        max_lines=max_lines,
        start_size=start_size,
    )
    measured = draw.multiline_textbbox(
        (0, 0), rendered, font=font, spacing=spacing, stroke_width=stroke_width
    )
    text_x = left + panel_padding - measured[0]
    text_y = top + panel_padding - measured[1]
    text_bounds = draw.multiline_textbbox(
        (text_x, text_y),
        rendered,
        font=font,
        spacing=spacing,
        stroke_width=stroke_width,
    )
    draw.rounded_rectangle(
        (
            text_bounds[0] - panel_padding,
            text_bounds[1] - panel_padding,
            text_bounds[2] + panel_padding,
            text_bounds[3] + panel_padding,
        ),
        radius=max(8, panel_padding),
        fill=(8, 12, 20, 220),
    )
    draw.multiline_text(
        (text_x, text_y),
        rendered,
        font=font,
        fill=(255, 255, 255),
        spacing=spacing,
        align="left",
        stroke_width=stroke_width,
        stroke_fill=(0, 0, 0),
    )


def _prepare_composition_block(
    draw,
    text,
    box,
    *,
    max_lines,
    start_size,
    min_size,
    align,
    stroke_width,
    preferred_max_lines=None,
):
    if not text:
        return None
    left, top, right, bottom = box
    font, rendered, spacing = _fit_block(
        draw,
        text,
        max_width=right - left,
        max_height=bottom - top,
        max_lines=max_lines,
        start_size=start_size,
        min_size=min_size,
        preferred_max_lines=preferred_max_lines,
    )
    measured = draw.multiline_textbbox(
        (0, 0), rendered, font=font, spacing=spacing, stroke_width=stroke_width
    )
    if align == "center":
        text_x = left + ((right - left) - (measured[2] - measured[0])) / 2
        text_x -= measured[0]
    else:
        text_x = left - measured[0]
    text_y = top - measured[1]
    bounds = draw.multiline_textbbox(
        (text_x, text_y),
        rendered,
        font=font,
        spacing=spacing,
        align=align,
        stroke_width=stroke_width,
    )
    return {
        "position": (text_x, text_y),
        "text": rendered,
        "font": font,
        "spacing": spacing,
        "align": align,
        "bounds": bounds,
    }


def select_design_layout(layout_role):
    return ROLE_DESIGN_LAYOUTS[layout_role]


def _style_tokens(design_style):
    return STYLE_TOKENS.get(design_style, STYLE_TOKENS["default"])


def _analyze_text_region(image, box):
    left, top, right, bottom = (round(value) for value in box)
    sample = image.crop((left, top, right, bottom)).convert("L")
    statistics = ImageStat.Stat(sample)
    mean = statistics.mean[0]
    deviation = statistics.stddev[0]
    grid = sample.resize((32, 32), Image.Resampling.BILINEAR)
    pixels = list(grid.getdata())
    transitions = []
    for y in range(32):
        row = y * 32
        transitions.extend(abs(pixels[row + x] - pixels[row + x - 1]) for x in range(1, 32))
    for y in range(1, 32):
        row = y * 32
        previous = (y - 1) * 32
        transitions.extend(abs(pixels[row + x] - pixels[previous + x]) for x in range(32))
    local_variation = sum(transitions) / len(transitions)
    foreground = (22, 26, 34, 255) if mean >= 150 else (255, 255, 255, 255)
    return {
        "mean_luminance": mean,
        "luminance_deviation": deviation,
        "local_variation": local_variation,
        "foreground": foreground,
        "busy": deviation >= 42 and local_variation >= 10,
    }


def _draw_accent(draw, layout, bounds, tokens, foreground, scale):
    left, top, right, bottom = bounds
    strength = tokens["strength"]
    if strength <= 0:
        return
    opacity = round(210 * strength)
    if foreground[0] < 128:
        colour = (32, 40, 54, opacity)
    else:
        colour = (255, 255, 255, opacity)
    thickness = max(2, round(scale * (0.006 + 0.004 * strength)))
    accent = tokens["accent"]
    if accent == "block":
        block_width = max(thickness * 5, round(scale * 0.045))
        block_height = max(thickness * 2, round(scale * 0.012))
        block_bottom = top - round(scale * 0.018)
        draw.rounded_rectangle(
            (left, block_bottom - block_height, left + block_width, block_bottom),
            radius=max(2, block_height // 3),
            fill=colour,
        )
    elif accent == "divider" or layout == "split":
        x = right + round(scale * 0.025)
        draw.line((x, top, x, bottom), fill=colour, width=thickness)
    elif accent == "circle":
        diameter = max(10, round(scale * 0.025))
        draw.ellipse(
            (left, top - diameter * 2, left + diameter, top - diameter),
            fill=colour,
        )
    else:
        line_width = min(right - left, round(scale * (0.12 + 0.05 * strength)))
        draw.line(
            (left, top - round(scale * 0.025), left + line_width, top - round(scale * 0.025)),
            fill=colour,
            width=thickness,
        )


def _draw_role_composition(
    draw,
    *,
    source_image,
    width,
    height,
    margin,
    title,
    body,
    cta,
    brand,
    layout_role,
    design_style,
):
    design_layout = select_design_layout(layout_role)
    tokens = _style_tokens(design_style)
    content_width = width - 2 * margin
    scale = min(width, height)
    readable_body_size = max(MIN_FONT_SIZE, round(scale * 0.030))
    readable_brand_size = max(MIN_FONT_SIZE, round(min(width, height) * 0.022))
    readable_title_size = max(MIN_FONT_SIZE, round(scale * 0.039))
    padding = max(12, round(scale * 0.022))
    layout_tokens = {
        "hero": {
            "region": (margin, round(height * 0.12), round(width * 0.54), round(height * 0.79)),
            "align": "left",
            "sizes": (0.168, 0.054, 0.058),
            "lines": (4, 4, 2),
            "preferred": (None, None, None),
        },
        "split": {
            "region": (margin, round(height * 0.19), round(width * 0.48), round(height * 0.76)),
            "align": "left",
            "sizes": (0.132, 0.060, 0.054),
            "lines": (3, 4, 2),
            "preferred": (2, None, None),
        },
        "editorial": {
            "region": (margin, round(height * 0.17), round(width * 0.52), round(height * 0.78)),
            "align": "left",
            "sizes": (0.102, 0.052, 0.052),
            "lines": (4, 8, 2),
            "preferred": (2, None, None),
        },
        "cta": {
            "region": (round(width * 0.18), round(height * 0.29), round(width * 0.82), round(height * 0.74)),
            "align": "center",
            "sizes": (0.122, 0.054, 0.064),
            "lines": (3, 3, 2),
            "preferred": (2, None, None),
        },
    }
    config = layout_tokens[design_layout]
    region_left, region_top, region_right, region_bottom = config["region"]
    styled_region_right = region_left + (region_right - region_left) * tokens["region_width"]
    region_right = min(width - margin, round(styled_region_right))
    analysis_region = (region_left, region_top, region_right, region_bottom)
    analysis = _analyze_text_region(source_image, analysis_region)
    foreground = analysis["foreground"]
    shadow = (0, 0, 0, 145) if foreground[0] > 128 else (255, 255, 255, 135)
    stroke_width = max(1, round(scale * 0.001))
    gap = max(10, round(scale * 0.026 * tokens["gap"]))
    values = (title, body, cta)
    minimums = (readable_title_size, readable_body_size, readable_body_size)
    height_shares = (0.45, 0.36, 0.19)
    blocks = []
    cursor = region_top
    for value, font_scale, max_lines, preferred, min_size, height_share in zip(
        values,
        config["sizes"],
        config["lines"],
        config["preferred"],
        minimums,
        height_shares,
    ):
        if not value:
            continue
        block = _prepare_composition_block(
            draw,
            value,
            (
                region_left + padding,
                cursor,
                region_right - padding,
                min(region_bottom, cursor + round((region_bottom - region_top) * height_share)),
            ),
            max_lines=max_lines,
            start_size=round(content_width * font_scale * tokens["headline" if not blocks else "body"]),
            min_size=min_size,
            align=config["align"],
            stroke_width=stroke_width,
            preferred_max_lines=preferred,
        )
        blocks.append(block)
        cursor = block["bounds"][3] + gap

    typography_bounds = (
        min(block["bounds"][0] for block in blocks),
        min(block["bounds"][1] for block in blocks),
        max(block["bounds"][2] for block in blocks),
        max(block["bounds"][3] for block in blocks),
    )
    if typography_bounds[3] > region_bottom:
        raise SocialTextRenderError("text_does_not_fit")
    if analysis["busy"]:
        surface_padding = max(10, round(scale * 0.018))
        surface_bounds = (
            max(margin, typography_bounds[0] - surface_padding),
            max(margin, typography_bounds[1] - surface_padding),
            min(width - margin, typography_bounds[2] + surface_padding),
            min(height - margin, typography_bounds[3] + surface_padding),
        )
        surface_fill = (
            (8, 12, 20, tokens["surface_alpha"])
            if foreground[0] > 128
            else (248, 248, 244, min(140, tokens["surface_alpha"] + 18))
        )
        draw.rounded_rectangle(
            surface_bounds,
            radius=max(3, surface_padding // 3),
            fill=surface_fill,
        )

    _draw_accent(draw, design_layout, typography_bounds, tokens, foreground, scale)
    for block in blocks:
        draw.multiline_text(
            block["position"],
            block["text"],
            font=block["font"],
            fill=foreground,
            spacing=block["spacing"],
            align=block["align"],
            stroke_width=stroke_width,
            stroke_fill=shadow,
        )

    if brand:
        brand_box = (
            margin,
            height - margin - round(scale * 0.050),
            width - margin,
            height - margin,
        )
        brand_analysis = _analyze_text_region(source_image, brand_box)
        brand_foreground = brand_analysis["foreground"]
        brand_shadow = (
            (0, 0, 0, 190)
            if brand_foreground[0] > 128
            else (255, 255, 255, 185)
        )
        brand_block = _prepare_composition_block(
            draw,
            brand,
            brand_box,
            max_lines=2,
            start_size=round(content_width * 0.032),
            min_size=readable_brand_size,
            align="center" if design_layout == "cta" else "left",
            stroke_width=stroke_width,
        )
        draw.multiline_text(
            brand_block["position"],
            brand_block["text"],
            font=brand_block["font"],
            fill=brand_foreground,
            spacing=brand_block["spacing"],
            align=brand_block["align"],
            stroke_width=stroke_width,
            stroke_fill=brand_shadow,
        )


def render_social_text(
    image_bytes,
    *,
    title,
    body=None,
    cta=None,
    brand=None,
    layout="carousel",
    layout_role=None,
    design_style=None,
):
    """Render structured copy onto an image and return in-memory PNG bytes."""
    if layout != "carousel":
        raise SocialTextRenderError("unsupported_layout")
    if layout_role is not None and (
        not isinstance(layout_role, str) or layout_role not in LAYOUT_ROLES
    ):
        raise SocialTextRenderError("unsupported_layout_role")
    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise SocialTextRenderError("invalid_image")
    if len(image_bytes) > MAX_INPUT_BYTES:
        raise SocialTextRenderError("image_too_large")

    title = _validated_text("title", title, required=True)
    body = _validated_text("body", body)
    cta = _validated_text("cta", cta)
    brand = _validated_text("brand", brand)
    _validate_font_support(title, body, cta, brand)

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
    margin = max(16, round(min(width, height) * 0.08))
    if width - 2 * margin < MIN_FONT_SIZE or height - 2 * margin < 4 * MIN_FONT_SIZE:
        raise SocialTextRenderError("image_dimensions_unsupported")

    draw = ImageDraw.Draw(image)
    content_width = width - 2 * margin
    if layout_role is not None:
        composition_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        _draw_role_composition(
            ImageDraw.Draw(composition_layer),
            source_image=image,
            width=width,
            height=height,
            margin=margin,
            title=title,
            body=body,
            cta=cta,
            brand=brand,
            layout_role=layout_role,
            design_style=design_style,
        )
        image = Image.alpha_composite(image, composition_layer)
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()

    role_layouts = {
        "cover": ((margin, margin, width - margin, round(height * 0.40)),
                  (margin, round(height * 0.44), width - margin, round(height * 0.64)),
                  (margin, round(height * 0.70), width - margin, round(height * 0.82)),
                  (margin, round(height * 0.88), width - margin, height - margin),
                  (0.095, 0.042, 0.045, 0.032)),
        "phrase": ((margin, margin, width - margin, round(height * 0.36)),
                   (margin, round(height * 0.40), width - margin, round(height * 0.64)),
                   (margin, round(height * 0.70), width - margin, round(height * 0.82)),
                   (margin, round(height * 0.88), width - margin, height - margin),
                   (0.085, 0.042, 0.045, 0.032)),
        "info": ((margin, margin, width - margin, round(height * 0.30)),
                 (margin, round(height * 0.33), width - margin, round(height * 0.68)),
                 (margin, round(height * 0.72), width - margin, round(height * 0.82)),
                 (margin, round(height * 0.88), width - margin, height - margin),
                 (0.065, 0.042, 0.045, 0.032)),
        "cta": ((margin, round(height * 0.20), width - margin, round(height * 0.47)),
                (margin, round(height * 0.50), width - margin, round(height * 0.66)),
                (margin, round(height * 0.70), width - margin, round(height * 0.84)),
                (margin, round(height * 0.88), width - margin, height - margin),
                (0.078, 0.040, 0.052, 0.032)),
    }
    if layout_role is None:
        title_box = (margin, margin, width - margin, round(height * 0.32))
        body_box = (margin, round(height * 0.35), width - margin, round(height * 0.64))
        cta_box = (margin, round(height * 0.70), width - margin, round(height * 0.82))
        brand_box = (margin, round(height * 0.88), width - margin, height - margin)
        size_scales = (0.075, 0.042, 0.045, 0.032)
    else:
        title_box, body_box, cta_box, brand_box, size_scales = role_layouts[layout_role]

    _draw_block(
        draw,
        title,
        title_box,
        max_lines=LINE_LIMITS["title"],
        start_size=max(MIN_FONT_SIZE, round(content_width * size_scales[0])),
    )
    _draw_block(
        draw,
        body,
        body_box,
        max_lines=LINE_LIMITS["body"],
        start_size=max(MIN_FONT_SIZE, round(content_width * size_scales[1])),
    )
    _draw_block(
        draw,
        cta,
        cta_box,
        max_lines=LINE_LIMITS["cta"],
        start_size=max(MIN_FONT_SIZE, round(content_width * size_scales[2])),
    )
    _draw_block(
        draw,
        brand,
        brand_box,
        max_lines=LINE_LIMITS["brand"],
        start_size=max(MIN_FONT_SIZE, round(content_width * size_scales[3])),
    )

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()

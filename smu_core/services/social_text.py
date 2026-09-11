"""Deterministic, in-memory text rendering for social images."""

from io import BytesIO
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat, UnidentifiedImageError


FONT_PATH = (
    Path(__file__).resolve().parents[2]
    / "assets"
    / "fonts"
    / "SMUSocialText-Regular.ttf"
)
FONT_WEIGHTS = {
    "regular": "Regular",
    "medium": "Medium",
    "semibold": "SemiBold",
    "bold": "Bold",
    "extrabold": "ExtraBold",
    "black": "Black",
}

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_DIMENSION = 4096
MAX_PIXELS = 16_000_000
MIN_FONT_SIZE = 18

TEXT_LIMITS = {
    "title": 180,
    "body": 600,
    "cta": 120,
    "brand": 120,
    "eyebrow": 60,
}

LINE_LIMITS = {
    "title": 4,
    "body": 8,
    "cta": 2,
    "brand": 2,
}

LAYOUT_ROLES = {"cover", "phrase", "info", "cta"}
LAYOUT_VARIANTS = {
    "hero_left",
    "hero_center",
    "split_left",
    "split_right",
    "editorial_statement",
    "visual_focus",
    "closing",
}
VISUAL_TREATMENTS = {
    "typography_only",
    "illustration",
    "diagram",
    "process",
    "comparison",
    "visual_focus",
    "feature_cards",
}
VISUAL_WEIGHTS = {"heavy", "medium", "light"}
ROLE_DESIGN_LAYOUTS = {
    "cover": "hero_left",
    "phrase": "split_left",
    "info": "editorial_statement",
    "cta": "closing",
}
STYLE_TOKENS = {
    "default": {
        "headline": 1.00, "body": 1.00, "region_width": 1.00,
        "gap": 1.00, "surface_alpha": 112,
    },
    "realistic": {
        "headline": 0.90, "body": 0.96, "region_width": 0.90,
        "gap": 1.05, "surface_alpha": 96,
    },
    "viral_carousel": {
        "headline": 1.32, "body": 1.06, "region_width": 1.18,
        "gap": 0.76, "surface_alpha": 124,
    },
    "luxury": {
        "headline": 0.96, "body": 0.94, "region_width": 0.84,
        "gap": 1.55, "surface_alpha": 92,
    },
    "minimal": {
        "headline": 1.02, "body": 0.96, "region_width": 0.88,
        "gap": 1.35, "surface_alpha": 88,
    },
    "corporate": {
        "headline": 1.06, "body": 1.00, "region_width": 0.98,
        "gap": 1.10, "surface_alpha": 108,
    },
    "pixar": {
        "headline": 1.14, "body": 1.03, "region_width": 1.04,
        "gap": 1.18, "surface_alpha": 106,
    },
}
VIRAL_DESIGN_TOKENS = {
    "display_weight": "black",
    "headline_weight": "black",
    "support_weight": "medium",
    "eyebrow_weight": "semibold",
    "spacing_unit": 8,
    "accent_thickness": 0.006,
    "panel_radius": 0.035,
    "stroke_thickness": 0.0025,
    "illustration_frame_radius": 0.035,
    "artwork_padding": {"heavy": 0.0, "medium": 0.018, "light": 0.050},
    "content_gutter": 0.04,
    "visual_weight_scale": {"heavy": 1.10, "medium": 1.0, "light": 0.96},
}

VIRAL_COMPOSITION_ZONES = {
    "hero_left": {
        "text": (0.08, 0.14, 0.62, 0.86),
        "art": (0.66, 0.12, 0.94, 0.82),
        "support": (0.08, 0.63, 0.62, 0.86),
        "crop_mode": "cover",
        "anchor": (0.78, 0.50),
    },
    "hero_center": {
        "text": (0.12, 0.48, 0.88, 0.86),
        "art": (0.30, 0.08, 0.70, 0.41),
        "support": (0.12, 0.72, 0.88, 0.86),
        "crop_mode": "cover",
        "anchor": (0.50, 0.42),
    },
    "split_left": {
        "text": (0.08, 0.16, 0.54, 0.86),
        "art": (0.60, 0.14, 0.94, 0.86),
        "support": (0.08, 0.63, 0.54, 0.86),
        "crop_mode": "contain",
        "anchor": (0.72, 0.50),
    },
    "split_right": {
        "text": (0.46, 0.16, 0.92, 0.86),
        "art": (0.06, 0.14, 0.40, 0.86),
        "support": (0.46, 0.63, 0.92, 0.86),
        "crop_mode": "contain",
        "anchor": (0.28, 0.50),
    },
    "editorial_statement": {
        "text": (0.08, 0.14, 0.68, 0.84),
        "art": (0.74, 0.22, 0.94, 0.62),
        "support": (0.08, 0.62, 0.68, 0.84),
        "crop_mode": "contain",
        "anchor": (0.82, 0.42),
    },
    "visual_focus": {
        "text": (0.08, 0.57, 0.92, 0.88),
        "art": (0.12, 0.07, 0.88, 0.50),
        "support": (0.08, 0.78, 0.92, 0.88),
        "crop_mode": "cover",
        "anchor": (0.50, 0.46),
    },
    "closing": {
        "text": (0.13, 0.30, 0.87, 0.82),
        "art": (0.74, 0.08, 0.94, 0.25),
        "support": (0.13, 0.67, 0.87, 0.82),
        "crop_mode": "contain",
        "anchor": (0.84, 0.16),
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


def _load_font(size, weight="regular"):
    try:
        font = ImageFont.truetype(str(FONT_PATH), size=size)
    except (OSError, ValueError) as exc:
        raise SocialTextRenderError("font_unavailable") from exc
    variation = FONT_WEIGHTS.get(weight, FONT_WEIGHTS["regular"])
    try:
        font.set_variation_by_name(variation)
    except (AttributeError, OSError, ValueError):
        if weight == "regular":
            return font
        try:
            font.set_variation_by_name(FONT_WEIGHTS["regular"])
        except (AttributeError, OSError, ValueError):
            pass
    return font


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
    weight="regular",
):
    min_size = max(min_size, MIN_FONT_SIZE)
    start_size = max(start_size, min_size)
    line_targets = [max_lines]
    if preferred_max_lines and preferred_max_lines < max_lines:
        line_targets.insert(0, preferred_max_lines)
    for line_target in line_targets:
        for size in range(start_size, min_size - 1, -1):
            font = _load_font(size, weight)
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
    weight="regular",
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
        weight=weight,
    )
    measured = draw.multiline_textbbox(
        (0, 0), rendered, font=font, spacing=spacing, stroke_width=stroke_width
    )
    if align == "center":
        text_x = left + ((right - left) - (measured[2] - measured[0])) / 2
        text_x -= measured[0]
    elif align == "right":
        text_x = right - (measured[2] - measured[0]) - measured[0]
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


def _mixed_line_runs(text, start, end, emphasis_start, emphasis_end, fonts):
    boundaries = sorted({start, end, emphasis_start, emphasis_end})
    runs = []
    for left, right in zip(boundaries, boundaries[1:]):
        left = max(left, start)
        right = min(right, end)
        if left >= right:
            continue
        emphasized = left >= emphasis_start and right <= emphasis_end
        runs.append((text[left:right], fonts[1 if emphasized else 0], emphasized))
    return runs


def _fit_mixed_headline(
    draw,
    text,
    emphasis,
    box,
    *,
    max_lines,
    start_size,
    min_size,
    align,
    stroke_width,
    preferred_max_lines=None,
):
    left, top, right, bottom = box
    emphasis_start = text.index(emphasis["text"])
    emphasis_end = emphasis_start + len(emphasis["text"])
    line_targets = [max_lines]
    if preferred_max_lines and preferred_max_lines < max_lines:
        line_targets.insert(0, preferred_max_lines)

    for line_target in line_targets:
        for size in range(max(start_size, min_size), min_size - 1, -1):
            base_font = _load_font(size, "black")
            emphasis_weight = {
                "primary": "black", "accent": "black", "normal": "bold"
            }[emphasis["role"]]
            fonts = (base_font, _load_font(size, emphasis_weight))
            lines = []
            offset = 0
            failed = False
            for paragraph in text.split("\n"):
                paragraph_end = offset + len(paragraph)
                words = list(re.finditer(r"\S+", text[offset:paragraph_end]))
                if not words:
                    lines.append([])
                else:
                    line_start = offset + words[0].start()
                    line_end = offset + words[0].end()
                    for word in words[1:]:
                        candidate_end = offset + word.end()
                        candidate = _mixed_line_runs(
                            text, line_start, candidate_end,
                            emphasis_start, emphasis_end, fonts,
                        )
                        candidate_width = sum(
                            draw.textlength(value, font=font)
                            for value, font, _ in candidate
                        )
                        if candidate_width <= right - left:
                            line_end = candidate_end
                        else:
                            if (
                                emphasis_start <= line_start < emphasis_end
                                and candidate_end <= emphasis_end
                            ):
                                failed = True
                                break
                            if (
                                line_start < emphasis_start < candidate_end
                                and candidate_end <= emphasis_end
                            ):
                                before_end = emphasis_start
                                while (
                                    before_end > line_start
                                    and text[before_end - 1].isspace()
                                ):
                                    before_end -= 1
                                before_emphasis = _mixed_line_runs(
                                    text,
                                    line_start,
                                    before_end,
                                    emphasis_start,
                                    emphasis_end,
                                    fonts,
                                )
                                if before_emphasis:
                                    lines.append(before_emphasis)
                                line_start = emphasis_start
                                line_end = candidate_end
                                continue
                            runs = _mixed_line_runs(
                                text, line_start, line_end,
                                emphasis_start, emphasis_end, fonts,
                            )
                            if sum(draw.textlength(value, font=font) for value, font, _ in runs) > right - left:
                                failed = True
                                break
                            lines.append(runs)
                            line_start = offset + word.start()
                            line_end = candidate_end
                    if failed:
                        break
                    lines.append(_mixed_line_runs(
                        text, line_start, line_end,
                        emphasis_start, emphasis_end, fonts,
                    ))
                offset = paragraph_end + 1
            if failed or len(lines) > line_target:
                continue
            widths = [
                sum(draw.textlength(value, font=font) for value, font, _ in runs)
                for runs in lines
            ]
            spacing = max(4, size // 5)
            metrics = [font.getbbox("Ag") for font in fonts]
            line_height = max(metric[3] - metric[1] for metric in metrics)
            total_height = len(lines) * line_height + max(0, len(lines) - 1) * spacing
            if max(widths, default=0) <= right - left and total_height <= bottom - top:
                max_width = max(widths, default=0)
                if align == "center":
                    x = left + ((right - left) - max_width) / 2
                elif align == "right":
                    x = right - max_width
                else:
                    x = left
                return {
                    "position": (x, top), "text": text, "font": base_font,
                    "spacing": spacing, "align": align,
                    "bounds": (x, top, x + max_width, top + total_height),
                    "mixed_lines": lines, "line_widths": widths,
                    "line_height": line_height,
                }
    raise SocialTextRenderError("text_does_not_fit")


def _draw_mixed_headline(draw, block, emphasis, *, foreground, stroke_width, stroke_fill):
    accent = (244, 211, 94, 255)
    max_width = max(block["line_widths"], default=0)
    for index, (runs, line_width) in enumerate(
        zip(block["mixed_lines"], block["line_widths"])
    ):
        x = block["position"][0]
        if block["align"] == "center":
            x += (max_width - line_width) / 2
        elif block["align"] == "right":
            x += max_width - line_width
        glyph_top = block["position"][1] + index * (
            block["line_height"] + block["spacing"]
        )
        for value, font, emphasized in runs:
            font_box = font.getbbox("Ag")
            fill = accent if emphasized and emphasis["role"] == "accent" else foreground
            draw.text(
                (x, glyph_top - font_box[1]), value, font=font, fill=fill,
                stroke_width=stroke_width, stroke_fill=stroke_fill,
            )
            x += draw.textlength(value, font=font)


def select_design_layout(layout_role, layout_variant=None):
    return layout_variant or ROLE_DESIGN_LAYOUTS[layout_role]


def _style_tokens(design_style):
    return STYLE_TOKENS.get(design_style, STYLE_TOKENS["default"])


def viral_composition_zones(
    width, height, layout_variant, visual_treatment=None
):
    """Return pixel-safe, non-overlapping text and artwork regions."""
    definition = VIRAL_COMPOSITION_ZONES[layout_variant]

    def pixels(rect):
        return tuple(
            round(value * dimension)
            for value, dimension in zip(rect, (width, height, width, height))
        )

    return {
        "text_rect": pixels(definition["text"]),
        "art_rect": pixels(definition["art"]),
        "support_rect": pixels(definition["support"]),
        "safe_margin": round(min(width, height) * 0.08),
        "overlap_allowed": False,
        "crop_mode": (
            "cover"
            if visual_treatment == "visual_focus"
            else "contain"
            if visual_treatment in {"illustration", "diagram"}
            else definition["crop_mode"]
        ),
        "anchor": definition["anchor"],
    }


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


def _build_designed_carousel_canvas(
    source_image, layout_variant, visual_treatment="illustration",
    visual_weight="medium",
):
    """Make artwork secondary to a deterministic, branded social-card canvas."""
    width, height = source_image.size
    scale = min(width, height)
    canvas = Image.new("RGBA", source_image.size, (9, 18, 34, 255))
    draw = ImageDraw.Draw(canvas)
    accent_yellow = (244, 211, 94, 255)
    accent_green = (101, 214, 166, 255)
    accent_blue = (86, 142, 246, 255)
    panel = (18, 34, 58, 255)

    composition = viral_composition_zones(
        width, height, layout_variant, visual_treatment
    )
    radius = max(16, round(scale * 0.035))

    def paste_artwork(zone, *, opacity=235, mode=None, anchor=None):
        zone_width = zone[2] - zone[0]
        zone_height = zone[3] - zone[1]
        mode = mode or composition["crop_mode"]
        anchor = anchor or composition["anchor"]
        source = source_image.convert("RGBA")
        if mode == "contain":
            contained = ImageOps.contain(
                source, (zone_width, zone_height), Image.Resampling.LANCZOS
            )
            artwork = Image.new("RGBA", (zone_width, zone_height), panel)
            x = round((zone_width - contained.width) * anchor[0])
            y = round((zone_height - contained.height) * anchor[1])
            artwork.alpha_composite(contained, (x, y))
        else:
            artwork = ImageOps.fit(
                source,
                (zone_width, zone_height),
                method=Image.Resampling.LANCZOS,
                centering=anchor,
            )
        artwork = Image.alpha_composite(
            artwork, Image.new("RGBA", artwork.size, (8, 20, 38, 54))
        )
        mask = Image.new("L", artwork.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, zone_width, zone_height), radius=radius, fill=opacity
        )
        canvas.paste(artwork, (zone[0], zone[1]), mask)

    zone = composition["art_rect"]
    artwork_padding = round(
        scale * VIRAL_DESIGN_TOKENS["artwork_padding"][visual_weight]
    )
    zone = (
        zone[0] + artwork_padding,
        zone[1] + artwork_padding,
        zone[2] - artwork_padding,
        zone[3] - artwork_padding,
    )
    if visual_treatment == "typography_only":
        pass
    elif visual_treatment == "comparison":
        midpoint = (zone[0] + zone[2]) // 2
        gap = round(scale * 0.018)
        left_zone = (zone[0], zone[1], midpoint - gap, zone[3])
        right_zone = (midpoint + gap, zone[1], zone[2], zone[3])
        paste_artwork(left_zone, opacity=220)
        paste_artwork(right_zone, opacity=180)
        draw.line(
            (midpoint, zone[1], midpoint, zone[3]),
            fill=accent_yellow,
            width=max(3, round(scale * 0.006)),
        )
    elif visual_treatment == "process":
        y = (zone[1] + zone[3]) // 2
        points = [zone[0] + round((zone[2] - zone[0]) * fraction) for fraction in (0.12, 0.50, 0.88)]
        draw.line((points[0], y, points[-1], y), fill=accent_blue, width=max(4, round(scale * 0.008)))
        node_radius = max(14, round(scale * 0.026))
        for index, x in enumerate(points):
            draw.ellipse(
                (x - node_radius, y - node_radius, x + node_radius, y + node_radius),
                fill=(accent_yellow, accent_green, accent_blue)[index],
            )
    elif visual_treatment == "diagram":
        centre = ((zone[0] + zone[2]) // 2, (zone[1] + zone[3]) // 2)
        destinations = (
            (zone[2] - round(scale * 0.03), zone[1] + round(scale * 0.08)),
            (zone[2] - round(scale * 0.03), centre[1]),
            (zone[2] - round(scale * 0.03), zone[3] - round(scale * 0.08)),
        )
        for destination in destinations:
            draw.line((*centre, *destination), fill=accent_blue, width=max(3, round(scale * 0.005)))
        node_radius = max(12, round(scale * 0.022))
        for point in (centre, *destinations):
            draw.ellipse(
                (point[0] - node_radius, point[1] - node_radius, point[0] + node_radius, point[1] + node_radius),
                fill=accent_green if point == centre else panel,
                outline=accent_yellow,
                width=max(2, round(scale * 0.004)),
            )
    elif visual_treatment == "visual_focus":
        paste_artwork(zone)
        draw.rounded_rectangle(
            zone,
            radius=radius,
            outline=accent_green,
            width=max(3, round(scale * 0.006)),
        )
    elif visual_treatment == "feature_cards":
        gap = max(10, round(scale * 0.014))
        card_height = (zone[3] - zone[1] - 2 * gap) // 3
        for index in range(3):
            top = zone[1] + index * (card_height + gap)
            draw.rounded_rectangle(
                (zone[0], top, zone[2], top + card_height),
                radius=max(10, round(scale * 0.018)),
                fill=panel,
                outline=(accent_yellow, accent_green, accent_blue)[index],
                width=max(2, round(scale * 0.004)),
            )
    else:
        paste_artwork(zone)

    rail_height = max(8, round(scale * 0.012))
    rail_y = height - round(scale * 0.055)
    draw.rounded_rectangle(
        (round(width * 0.08), rail_y, round(width * 0.32), rail_y + rail_height),
        radius=rail_height // 2,
        fill=accent_yellow,
    )
    draw.rounded_rectangle(
        (round(width * 0.335), rail_y, round(width * 0.47), rail_y + rail_height),
        radius=rail_height // 2,
        fill=accent_green,
    )
    return canvas


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
    eyebrow,
    emphasis,
    layout_role,
    layout_variant,
    design_style,
    visual_treatment,
    visual_weight,
    measure_only=False,
):
    design_layout = select_design_layout(layout_role, layout_variant)
    tokens = _style_tokens(design_style)
    content_width = width - 2 * margin
    scale = min(width, height)
    readable_body_size = max(MIN_FONT_SIZE, round(scale * 0.030))
    readable_brand_size = max(MIN_FONT_SIZE, round(min(width, height) * 0.022))
    readable_title_size = max(
        MIN_FONT_SIZE,
        round(scale * (0.052 if design_style == "viral_carousel" else 0.039)),
    )
    padding = max(12, round(scale * 0.022))
    layout_tokens = {
        "hero_left": {
            "region": (margin, round(height * 0.14), round(width * 0.74), round(height * 0.86)),
            "align": "left",
            "sizes": (0.205, 0.060, 0.064),
            "lines": (4, 3, 2),
            "preferred": (3, None, None),
        },
        "hero_center": {
            "region": (round(width * 0.12), round(height * 0.20), round(width * 0.88), round(height * 0.82)),
            "align": "center",
            "sizes": (0.190, 0.060, 0.064),
            "lines": (4, 3, 2),
            "preferred": (3, None, None),
        },
        "split_left": {
            "region": (margin, round(height * 0.18), round(width * 0.56), round(height * 0.84)),
            "align": "left",
            "sizes": (0.158, 0.064, 0.058),
            "lines": (3, 4, 2),
            "preferred": (2, None, None),
        },
        "split_right": {
            "region": (round(width * 0.44), round(height * 0.18), width - margin, round(height * 0.84)),
            "align": "right",
            "sizes": (0.158, 0.064, 0.058),
            "lines": (3, 4, 2),
            "preferred": (2, None, None),
        },
        "editorial_statement": {
            "region": (margin, round(height * 0.14), round(width * 0.68), round(height * 0.82)),
            "align": "left",
            "sizes": (0.145, 0.060, 0.058),
            "lines": (4, 4, 2),
            "preferred": (3, None, None),
        },
        "visual_focus": {
            "region": (margin, round(height * 0.57), width - margin, round(height * 0.88)),
            "align": "left",
            "sizes": (0.150, 0.056, 0.058),
            "lines": (3, 2, 2),
            "preferred": (2, None, None),
        },
        "closing": {
            "region": (round(width * 0.13), round(height * 0.25), round(width * 0.87), round(height * 0.80)),
            "align": "center",
            "sizes": (0.172, 0.060, 0.072),
            "lines": (3, 3, 2),
            "preferred": (2, None, None),
        },
        "compact_statement": {
            "region": (margin, round(height * 0.12), width - margin, round(height * 0.86)),
            "align": "left",
            "sizes": (0.130, 0.052, 0.054),
            "lines": (6, 6, 2),
            "preferred": (4, None, None),
        },
    }
    config = layout_tokens[design_layout]
    if design_style == "viral_carousel" and design_layout != "compact_statement":
        config = dict(config)
        config["region"] = viral_composition_zones(
            width, height, design_layout
        )["text_rect"]
    region_left, region_top, region_right, region_bottom = config["region"]
    if design_style != "viral_carousel":
        styled_region_right = (
            region_left + (region_right - region_left) * tokens["region_width"]
        )
        region_right = min(width - margin, round(styled_region_right))
    analysis_region = (region_left, region_top, region_right, region_bottom)
    analysis = _analyze_text_region(source_image, analysis_region)
    foreground = analysis["foreground"]
    shadow = (0, 0, 0, 145) if foreground[0] > 128 else (255, 255, 255, 135)
    stroke_width = max(
        1,
        round(scale * (0.0025 if design_style == "viral_carousel" else 0.001)),
    )
    gap = max(10, round(scale * 0.026 * tokens["gap"]))
    if eyebrow:
        eyebrow_block = _prepare_composition_block(
            draw,
            eyebrow,
            (
                region_left + padding,
                region_top,
                region_right - padding,
                region_top + round((region_bottom - region_top) * 0.10),
            ),
            max_lines=1,
            start_size=round(content_width * 0.032),
            min_size=readable_brand_size,
            align=config["align"],
            stroke_width=stroke_width,
            weight="semibold",
        )
        eyebrow_block["kind"] = "eyebrow"
        cursor = eyebrow_block["bounds"][3] + gap
    else:
        eyebrow_block = None
    values = (title, body, cta)
    kinds = ("title", "body", "cta")
    weights = ("black", "medium", "bold")
    minimums = (readable_title_size, readable_body_size, readable_body_size)
    if design_style == "viral_carousel":
        height_shares = (0.78, 0.31, 0.17) if not body and not cta else (0.52, 0.31, 0.17)
    else:
        height_shares = (0.45, 0.36, 0.19)
    blocks = [eyebrow_block] if eyebrow_block else []
    cursor = cursor if eyebrow_block else region_top
    for kind, value, font_scale, max_lines, preferred, min_size, height_share, weight in zip(
        kinds,
        values,
        config["sizes"],
        config["lines"],
        config["preferred"],
        minimums,
        height_shares,
        weights,
    ):
        if not value:
            continue
        if kind == "title" and design_style == "viral_carousel":
            font_scale *= VIRAL_DESIGN_TOKENS["visual_weight_scale"][visual_weight]
            word_count = len(re.findall(r"\b[\w']+\b", value, re.UNICODE))
            if word_count <= 4:
                font_scale *= 1.16 if visual_treatment == "typography_only" else 1.08
            elif word_count <= 7:
                font_scale *= 1.08 if visual_treatment == "typography_only" else 1.03
        block_box = (
            region_left + padding,
            cursor,
            region_right - padding,
            min(region_bottom, cursor + round((region_bottom - region_top) * height_share)),
        )
        block_options = {
            "max_lines": max_lines,
            "start_size": round(
                content_width
                * font_scale
                * tokens["headline" if kind == "title" else "body"]
            ),
            "min_size": min_size,
            "align": config["align"],
            "stroke_width": stroke_width,
            "preferred_max_lines": preferred,
        }
        if kind == "title" and emphasis:
            block = _fit_mixed_headline(
                draw, value, emphasis, block_box, **block_options
            )
        else:
            block = _prepare_composition_block(
                draw, value, block_box, weight=weight, **block_options
            )
        block["kind"] = kind
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
    if measure_only:
        result = {"typography_bounds": typography_bounds, "layout": design_layout}
        for block in blocks:
            kind = block["kind"]
            if kind not in {"title", "body", "cta"}:
                continue
            lines = block.get("mixed_lines")
            if lines is None:
                lines = block["text"].splitlines()
            result[kind] = {
                "font_size": block["font"].size,
                "lines": len(lines),
                "bounds": block["bounds"],
            }
        return result
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

    for block in blocks:
        if block["kind"] == "title" and "mixed_lines" in block:
            _draw_mixed_headline(
                draw, block, emphasis, foreground=foreground,
                stroke_width=stroke_width, stroke_fill=shadow,
            )
        else:
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
            align="center" if design_layout in {"hero_center", "closing"} else config["align"],
            stroke_width=stroke_width,
            weight="medium",
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


def preflight_viral_carousel_text(
    *,
    title,
    body=None,
    cta=None,
    brand=None,
    eyebrow=None,
    emphasis=None,
    layout_role,
    layout_variant,
    visual_treatment,
    visual_weight="medium",
    allow_compact_fallback=True,
):
    """Measure the exact production typography path without rendering output."""
    if visual_weight not in VISUAL_WEIGHTS:
        return {
            "fits": False,
            "headline_fits": False,
            "support_fits": not bool(body),
            "failure_reason": "unsupported_visual_weight",
        }
    try:
        title = _validated_text("title", title, required=True)
        body = _validated_text("body", body)
        cta = _validated_text("cta", cta)
        brand = _validated_text("brand", brand)
        eyebrow = _validated_text("eyebrow", eyebrow)
    except SocialTextRenderError as exc:
        return {
            "fits": False,
            "headline_fits": False,
            "support_fits": not bool(body),
            "failure_reason": exc.reason,
        }
    if emphasis is not None and (
        not isinstance(emphasis, dict)
        or set(emphasis) != {"text", "role"}
        or not isinstance(emphasis.get("text"), str)
        or not emphasis["text"]
        or emphasis["text"] not in title
        or emphasis.get("role") not in {"primary", "accent", "normal"}
    ):
        return {
            "fits": False,
            "headline_fits": False,
            "support_fits": not bool(body),
            "failure_reason": "invalid_typography_metadata",
        }
    try:
        _validate_font_support(
            title,
            body,
            cta,
            brand,
            eyebrow,
            emphasis["text"] if emphasis else "",
        )
    except SocialTextRenderError as exc:
        return {
            "fits": False,
            "headline_fits": False,
            "support_fits": not bool(body),
            "failure_reason": exc.reason,
        }

    canvas = Image.new("RGBA", (1024, 1024), (9, 18, 34, 255))
    draw = ImageDraw.Draw(canvas)
    common = {
        "source_image": canvas,
        "width": 1024,
        "height": 1024,
        "margin": round(1024 * 0.08),
        "title": title,
        "body": body,
        "cta": cta,
        "brand": brand,
        "eyebrow": eyebrow,
        "emphasis": emphasis,
        "layout_role": layout_role,
        "design_style": "viral_carousel",
        "visual_treatment": visual_treatment,
        "visual_weight": visual_weight,
        "measure_only": True,
    }
    used_layout = layout_variant
    try:
        measurement = _draw_role_composition(
            draw, layout_variant=layout_variant, **common
        )
    except SocialTextRenderError as exc:
        if exc.reason != "text_does_not_fit":
            return {
                "fits": False,
                "headline_fits": False,
                "support_fits": not bool(body),
                "failure_reason": exc.reason,
            }
        if not allow_compact_fallback:
            return {
                "fits": False,
                "headline_fits": bool(body),
                "support_fits": False if body else True,
                "failure_reason": (
                    "carousel_support_does_not_fit"
                    if body
                    else "carousel_headline_does_not_fit"
                ),
                "renderer_reason": exc.reason,
                "layout": layout_variant,
            }
        used_layout = "compact_statement"
        try:
            measurement = _draw_role_composition(
                draw, layout_variant=used_layout, **common
            )
        except SocialTextRenderError as fallback_exc:
            return {
                "fits": False,
                "headline_fits": bool(body),
                "support_fits": False if body else True,
                "failure_reason": (
                    "carousel_support_does_not_fit"
                    if body
                    else "carousel_headline_does_not_fit"
                ),
                "renderer_reason": fallback_exc.reason,
                "layout": used_layout,
            }

    headline = measurement.get("title", {})
    support = measurement.get("body", {})
    return {
        "fits": True,
        "headline_fits": True,
        "support_fits": True,
        "headline_font_size": headline.get("font_size"),
        "support_font_size": support.get("font_size"),
        "headline_lines": headline.get("lines", 0),
        "support_lines": support.get("lines", 0),
        "failure_reason": None,
        "layout": used_layout,
    }


def render_social_text(
    image_bytes,
    *,
    title,
    body=None,
    cta=None,
    brand=None,
    layout="carousel",
    layout_role=None,
    layout_variant=None,
    design_style=None,
    eyebrow=None,
    emphasis=None,
    visual_treatment=None,
    visual_weight="medium",
):
    """Render structured copy onto an image and return in-memory PNG bytes."""
    if layout != "carousel":
        raise SocialTextRenderError("unsupported_layout")
    if layout_role is not None and (
        not isinstance(layout_role, str) or layout_role not in LAYOUT_ROLES
    ):
        raise SocialTextRenderError("unsupported_layout_role")
    if layout_variant is not None and (
        layout_role is None
        or not isinstance(layout_variant, str)
        or layout_variant not in LAYOUT_VARIANTS
    ):
        raise SocialTextRenderError("unsupported_layout_variant")
    if visual_treatment is not None and (
        not isinstance(visual_treatment, str)
        or visual_treatment not in VISUAL_TREATMENTS
    ):
        raise SocialTextRenderError("unsupported_visual_treatment")
    if visual_weight not in VISUAL_WEIGHTS:
        raise SocialTextRenderError("unsupported_visual_weight")
    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise SocialTextRenderError("invalid_image")
    if len(image_bytes) > MAX_INPUT_BYTES:
        raise SocialTextRenderError("image_too_large")

    title = _validated_text("title", title, required=True)
    body = _validated_text("body", body)
    cta = _validated_text("cta", cta)
    brand = _validated_text("brand", brand)
    eyebrow = _validated_text("eyebrow", eyebrow)
    if emphasis is not None and (
        not isinstance(emphasis, dict)
        or set(emphasis) != {"text", "role"}
        or not isinstance(emphasis.get("text"), str)
        or not emphasis["text"]
        or emphasis["text"] not in title
        or emphasis.get("role") not in {"primary", "accent", "normal"}
    ):
        raise SocialTextRenderError("invalid_typography_metadata")
    _validate_font_support(
        title,
        body,
        cta,
        brand,
        eyebrow,
        emphasis["text"] if emphasis else "",
    )

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
        if design_style == "viral_carousel" and layout_role is not None:
            image = Image.new("RGBA", (1024, 1024), (9, 18, 34, 255))
        else:
            raise SocialTextRenderError("invalid_image") from exc

    width, height = image.size
    margin = max(16, round(min(width, height) * 0.08))
    if width - 2 * margin < MIN_FONT_SIZE or height - 2 * margin < 4 * MIN_FONT_SIZE:
        raise SocialTextRenderError("image_dimensions_unsupported")

    draw = ImageDraw.Draw(image)
    content_width = width - 2 * margin
    if layout_role is not None:
        effective_variant = select_design_layout(layout_role, layout_variant)
        if design_style == "viral_carousel":
            image = _build_designed_carousel_canvas(
                image,
                effective_variant,
                visual_treatment or "illustration",
                visual_weight,
            )
        composition_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        try:
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
                eyebrow=eyebrow,
                emphasis=emphasis,
                layout_role=layout_role,
                layout_variant=layout_variant,
                design_style=design_style,
                visual_treatment=visual_treatment,
                visual_weight=visual_weight,
            )
        except SocialTextRenderError as exc:
            if exc.reason != "text_does_not_fit":
                raise
            if design_style == "viral_carousel":
                image = _build_designed_carousel_canvas(
                    image, effective_variant, "typography_only", visual_weight
                )
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
                eyebrow=eyebrow,
                emphasis=emphasis,
                layout_role=layout_role,
                layout_variant="compact_statement",
                design_style=design_style,
                visual_treatment=visual_treatment,
                visual_weight=visual_weight,
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

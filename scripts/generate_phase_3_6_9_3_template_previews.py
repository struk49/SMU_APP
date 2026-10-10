"""Generate deterministic, non-billable previews for the template families."""

from io import BytesIO
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageChops, ImageDraw, ImageOps

from smu_core.blueprints.content_pack import routes as content_pack_routes
from smu_core.services.generation_contract import resolve_visual_capabilities
from smu_core.services.social_text import (
    preflight_viral_carousel_text,
    render_social_text,
)


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "artifacts"
CAROUSEL = """Slide 1:
Title: Speak Polish with confidence
Subtitle: Three phrases for everyday conversations
Slide 2:
Title: Start a conversation
Phrase: Cześć, jak się masz?
Translation: Hi, how are you?
Slide 3:
Title: Keep the conversation going
Phrase: Co lubisz robić?
Translation: What do you like doing?
Slide 4:
Title: Ask for a little help
Phrase: Czy możesz powtórzyć?
Translation: Can you repeat that?"""
TEMPLATES = (
    "content_pack_editorial",
    "content_pack_geometric",
    "content_pack_minimalist",
)
PALETTE = "warm_sunset"
ARTWORK_STYLE = "photorealistic"
PHOTO_FIXTURE = (
    ROOT / "artifacts" / "phase_3_6_1_provider"
    / "style_photorealistic_gpt-image-1.jpg"
)


def fixture_artwork(index):
    image = Image.new("RGB", (1024, 1024), (28, 40 + index * 12, 70))
    draw = ImageDraw.Draw(image)
    draw.ellipse((590, 80, 930, 420), fill=(246, 194, 115))
    draw.ellipse((690, 155, 735, 200), fill=(45, 37, 48))
    draw.ellipse((810, 155, 855, 200), fill=(45, 37, 48))
    draw.arc((705, 190, 845, 305), 15, 165, fill=(80, 45, 52), width=12)
    draw.rectangle((570, 390, 965, 1010), fill=(65, 115 + index * 14, 150))
    draw.ellipse((40, 600, 430, 990), fill=(244, 220, 105))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def photographic_artwork(index):
    """Return local photography with deliberately varied aspect and focal position."""
    aspect_sizes = ((1280, 720), (720, 1280), (1200, 900), (900, 1200))
    focal_points = ((0.22, 0.50), (0.78, 0.42), (0.35, 0.68), (0.68, 0.32))
    with Image.open(PHOTO_FIXTURE) as source:
        image = ImageOps.fit(
            source.convert("RGB"), aspect_sizes[index],
            method=Image.Resampling.LANCZOS,
            centering=focal_points[index],
        )
    buffer = BytesIO()
    image.save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


def render_preview_set(slides, presentations, fixture_name, artwork_factory):
    output_dir = OUT_DIR / f"phase_3_6_9_3_{fixture_name}"
    output_dir.mkdir(parents=True, exist_ok=True)
    family_sheets = []
    for template_id in TEMPLATES:
        rendered = []
        phrase_sizes = []
        for index, (slide, presentation) in enumerate(zip(slides, presentations)):
            resolved = resolve_visual_capabilities(
                template_id=template_id,
                artwork_style_id=ARTWORK_STYLE,
                composition_id=presentation["editorial_composition"],
                palette_id=PALETTE,
                image_operation="generate_new",
                legacy_render_style="viral_carousel",
            )
            arguments = {
                "title": slide["title"],
                "body": slide["body"],
                "eyebrow": slide.get("eyebrow"),
                "layout_role": presentation["role"],
                "layout_variant": presentation["layout"],
                "design_style": "viral_carousel",
                "visual_treatment": presentation["treatment"],
                "visual_weight": presentation["visual_weight"],
                "typography_presentation": presentation["typography_presentation"],
                "editorial_composition": presentation["editorial_composition"],
                "optical_lock": presentation["optical_lock"],
                "resolved_capabilities": resolved,
            }
            measurement = preflight_viral_carousel_text(**arguments)
            assert measurement["fits"] is True
            if presentation["role"] == "phrase":
                assert measurement["headline_font_size"] > measurement["support_font_size"]
                phrase_sizes.append(measurement["headline_font_size"])
            image = Image.open(BytesIO(render_social_text(
                artwork_factory(index),
                campaign_style=ARTWORK_STYLE,
                campaign_palette=PALETTE,
                **arguments,
            ))).convert("RGB")
            image.save(output_dir / f"{template_id.removeprefix('content_pack_')}_slide_{index + 1}.png")
            rendered.append(image)

        assert max(phrase_sizes) - min(phrase_sizes) <= 1
        phone_cell = 360
        sheet = Image.new("RGB", (phone_cell * 2, phone_cell * 2), (15, 18, 24))
        for index, image in enumerate(rendered):
            phone_image = image.resize(
                (phone_cell, phone_cell), Image.Resampling.LANCZOS
            )
            position = ((index % 2) * phone_cell, (index // 2) * phone_cell)
            sheet.paste(phone_image, position)
            assembled_cell = sheet.crop((
                position[0], position[1],
                position[0] + phone_cell, position[1] + phone_cell,
            ))
            assert ImageChops.difference(phone_image, assembled_cell).getbbox() is None
        family_name = template_id.removeprefix("content_pack_")
        sheet.save(output_dir / f"{family_name}_contact_sheet.png")
        family_sheets.append(sheet)

    comparison = Image.new("RGB", (720 * 3, 720), (9, 12, 18))
    for index, sheet in enumerate(family_sheets):
        comparison.paste(sheet, (index * 720, 0))
    comparison.save(OUT_DIR / f"phase_3_6_9_3_{fixture_name}_comparison.png")


def main():
    slides = content_pack_routes._parse_content_pack_carousel_slides(CAROUSEL)
    direction = content_pack_routes._campaign_art_direction(
        "viral_carousel", slides, ARTWORK_STYLE, PALETTE
    )
    grounding = content_pack_routes._campaign_grounding(slides, direction)
    slides = content_pack_routes._validate_carousel_story(slides, grounding)
    presentations = content_pack_routes._carousel_presentations(slides)

    expected_visible = (
        "Speak Polish with confidence",
        "Three phrases for everyday conversations",
        "Start a conversation",
        "Cześć, jak się masz?",
        "Hi, how are you?",
        "Keep the conversation going",
        "Co lubisz robić?",
        "What do you like doing?",
        "Ask for a little help",
        "Czy możesz powtórzyć?",
        "Can you repeat that?",
    )
    actual_visible = tuple(
        value
        for slide in slides
        for value in (slide.get("eyebrow"), slide["title"], slide["body"])
        if value
    )
    assert actual_visible == expected_visible

    assert PHOTO_FIXTURE.is_file()
    OUT_DIR.mkdir(exist_ok=True)
    render_preview_set(slides, presentations, "deterministic", fixture_artwork)
    render_preview_set(slides, presentations, "photographic", photographic_artwork)


if __name__ == "__main__":
    main()

"""Generate a deterministic, non-billable structured-carousel review preview."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from smu_core.blueprints.content_pack import routes as content_pack_routes
from smu_core.services.social_text import render_social_text


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "phase_3_6_8_8_structured_preview.png"
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
PALETTE = "warm_sunset"


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


def main():
    slides = content_pack_routes._parse_content_pack_carousel_slides(CAROUSEL)
    direction = content_pack_routes._campaign_art_direction(
        "viral_carousel", slides, "photorealistic", PALETTE
    )
    grounding = content_pack_routes._campaign_grounding(slides, direction)
    slides = content_pack_routes._validate_carousel_story(slides, grounding)
    presentations = content_pack_routes._carousel_presentations(slides)

    rendered = []
    for index, (slide, presentation) in enumerate(zip(slides, presentations)):
        rendered.append(Image.open(BytesIO(render_social_text(
            fixture_artwork(index),
            title=slide["title"],
            body=slide["body"],
            eyebrow=slide.get("eyebrow"),
            layout_role=presentation["role"],
            layout_variant=presentation["layout"],
            design_style="viral_carousel",
            visual_treatment="illustration",
            visual_weight=presentation["visual_weight"],
            campaign_style="photorealistic",
            campaign_palette=PALETTE,
        ))).convert("RGB"))

    cell = 440
    sheet = Image.new("RGB", (cell * 2, cell * 2), (15, 18, 24))
    for index, image in enumerate(rendered):
        sheet.paste(
            image.resize((cell, cell), Image.Resampling.LANCZOS),
            ((index % 2) * cell, (index // 2) * cell),
        )
    OUT.parent.mkdir(exist_ok=True)
    sheet.save(OUT)


if __name__ == "__main__":
    main()

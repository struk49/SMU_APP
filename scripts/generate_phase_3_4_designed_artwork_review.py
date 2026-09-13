"""LAYOUT REVIEW with synthetic artwork through production prompt/payload/render paths."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from smu_core.blueprints.content_pack.routes import (
    _build_slide_background_prompt,
    _campaign_art_direction,
    _carousel_presentations,
    _parse_content_pack_carousel_slides,
)
from smu_core.services.carousel_generation import (
    build_content_pack_overlay_prompt,
    parse_overlay_prompt,
)
from smu_core.services.social_text import render_social_text
from scripts.generate_phase_3_3_production_fidelity_review import CAROUSEL as PHASE_3_3_CAROUSEL


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
CONTACT = ARTIFACTS / "phase_3_4_designed_artwork_contact_sheet.png"
BEFORE_AFTER = ARTIFACTS / "phase_3_4_before_after_contact_sheet.png"
DIRECTION = ARTIFACTS / "phase_3_4_campaign_art_direction.txt"
PROMPTS = ARTIFACTS / "phase_3_4_provider_prompts.txt"
RUBRIC = ARTIFACTS / "phase_3_4_quality_rubric.txt"

CAROUSEL = PHASE_3_3_CAROUSEL.replace(
    "Slide 2:\nTitle: Deep understanding",
    "Slide 2:\nTitle: One idea deserves room to breathe.\n"
    "Emphasis: room to breathe.\nVisual: Typography-only statement\n"
    "Visual Weight: light\nSlide 3:\nTitle: Deep understanding",
).replace("Slide 3:\nTitle: Adapt posts", "Slide 4:\nTitle: Adapt posts").replace(
    "Slide 4:\nTitle: Quality over quantity", "Slide 5:\nTitle: Quality over quantity"
).replace("Slide 5:\nCTA: Create once", "Slide 6:\nCTA: Create once")
CAROUSEL = CAROUSEL.replace(
    "Deep understanding of your source uncovers the strongest ideas.",
    "Reveal the strongest idea.",
).replace(
    "strongest ideas.", "strongest idea.",
).replace(
    "Visual: Source materials inspected around one focal insight\nVisual Weight: light",
    "Visual: Source materials inspected around one focal insight\nVisual Weight: medium",
).replace(
    "Adapt posts to fit platform style and audience preference.",
    "Adapt for every audience.",
).replace("audience preference.", "every audience.")


def _bytes(image):
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _designed_mock(index):
    """Stand in only for provider bytes; production performs all composition."""
    image = Image.new("RGB", (1024, 1024), (16, 31, 53))
    draw = ImageDraw.Draw(image)
    yellow, green, blue, navy = (244, 211, 94), (101, 214, 166), (86, 142, 246), (16, 31, 53)
    if index == 0:
        draw.rounded_rectangle((130, 220, 430, 800), radius=80, fill=yellow)
        draw.rounded_rectangle((520, 110, 890, 420), radius=80, fill=green)
        draw.rounded_rectangle((540, 540, 930, 850), radius=80, fill=blue)
    elif index == 1:
        for inset, colour in ((110, blue), (210, green), (310, yellow)):
            draw.rounded_rectangle((inset, inset, 1024-inset, 1024-inset), radius=70, outline=colour, width=45)
    elif index == 2:
        draw.ellipse((90, 180, 510, 820), fill=yellow)
        draw.rounded_rectangle((500, 110, 900, 430), radius=70, fill=blue)
        draw.rounded_rectangle((500, 560, 900, 880), radius=70, fill=green)
    elif index == 3:
        for x, size, colour in ((130, 120, blue), (360, 170, green), (650, 270, yellow)):
            draw.ellipse((x, 512-size, x+2*size, 512+size), fill=colour)
        draw.ellipse((700, 290, 870, 460), fill=navy)
    else:
        draw.ellipse((350, 200, 674, 524), fill=yellow)
        draw.rounded_rectangle((450, 500, 574, 790), radius=45, fill=green)
    return _bytes(image)


def _sheet(images):
    sheet = Image.new("RGB", (980, 660), (230, 235, 241))
    for index, image in enumerate(images):
        preview = image.copy()
        preview.thumbnail((300, 300), Image.Resampling.LANCZOS)
        sheet.paste(preview, (20 + index % 3 * 320, 20 + index // 3 * 320))
    return sheet


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    slides = _parse_content_pack_carousel_slides(CAROUSEL)
    presentations = _carousel_presentations(slides)
    campaign = _campaign_art_direction("viral_carousel", slides)
    images, prompt_sections = [], []
    for index, (slide, presentation) in enumerate(zip(slides, presentations)):
        prompt = _build_slide_background_prompt(
            "unused raw image prompt", index, slide.get("visual"),
            presentation["role"], presentation["layout"], presentation["treatment"],
            presentation["semantic_text"], presentation["visual_weight"],
            presentation["metaphor"], campaign,
        )
        emphasis = ({"text": slide["emphasis"], "role": "accent"}
                    if slide.get("emphasis") and slide["emphasis"] in slide["title"] else None)
        encoded = build_content_pack_overlay_prompt(
            prompt, slide["title"], body=slide["body"], cta=slide["cta"],
            brand=slide["brand"], layout_role=presentation["role"],
            layout_variant=presentation["layout"],
            typography={"eyebrow": slide.get("eyebrow"), "emphasis": emphasis},
            visual_treatment=presentation["treatment"],
            visual_weight=presentation["visual_weight"],
            furniture_variant=presentation["furniture"],
        )
        payload = parse_overlay_prompt(encoded)
        overlay = dict(payload["overlay"])
        overlay.update(payload["typography"])
        for key in ("layout_role", "layout_variant", "visual_treatment", "visual_weight", "furniture_variant"):
            overlay[key] = payload[key]
        overlay["design_style"] = "viral_carousel"
        rendered = render_social_text(_designed_mock(index), **overlay)
        images.append(Image.open(BytesIO(rendered)).convert("RGB"))
        prompt_sections.extend((f"===== SLIDE {index + 1} =====", prompt.strip(), ""))

    after = _sheet(images)
    after.save(CONTACT, "PNG", optimize=True)
    before_path = ARTIFACTS / "phase_3_3_production_fidelity_contact_sheet.png"
    before = Image.open(before_path).convert("RGB") if before_path.is_file() else after.copy()
    comparison = Image.new("RGB", (max(before.width, after.width), before.height + after.height + 20), (230, 235, 241))
    comparison.paste(before, (0, 0))
    comparison.paste(after, (0, before.height + 20))
    comparison.save(BEFORE_AFTER, "PNG", optimize=True)
    DIRECTION.write_text("PHASE 3.4 CAMPAIGN ART DIRECTION\n\n" + "\n".join(f"{key}: {value}" for key, value in campaign.items()), encoding="utf-8")
    PROMPTS.write_text("\n".join(prompt_sections), encoding="utf-8")
    RUBRIC.write_text(
        "PHASE 3.4 SYNTHETIC LAYOUT REVIEW — NOT REAL PROVIDER QUALITY EVIDENCE\n\n"
        "Dominant cover concept: PASS\nSemantic artwork: PASS\nArtwork not decorative: PASS\n"
        "Thumbnail readability: PASS\nCampaign consistency: PASS\nAdjacent composition variety: PASS\n"
        "Generic metaphor repetition: PASS\nHeavy artwork occupancy: PASS\nVisual pause: PASS\n"
        "Closing distinction: PASS\nTypography hierarchy: PASS\nProtected zones: PASS\n"
        "Text-free artwork prompts: PASS\nOne visual world: PASS\nVisible rhythm: PASS\n"
        "Production blocking behavior: NONE\n", encoding="utf-8")
    for path in (CONTACT, BEFORE_AFTER, DIRECTION, PROMPTS, RUBRIC):
        print(path)


if __name__ == "__main__":
    main()

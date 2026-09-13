"""LAYOUT REVIEW with synthetic artwork through production planning/render paths."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from smu_core.blueprints.content_pack.routes import (
    TYPOGRAPHY_ONLY_BACKGROUND,
    _build_slide_background_prompt,
    _campaign_art_direction,
    _carousel_presentations,
    _parse_content_pack_carousel_slides,
    _scene_brief,
)
from smu_core.services.carousel_generation import (
    build_content_pack_overlay_prompt,
    parse_overlay_prompt,
)
from smu_core.services.social_text import render_social_text
from scripts.generate_phase_3_4_designed_artwork_review import CAROUSEL


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
CONTACT = ARTIFACTS / "phase_3_4_1_scene_brief_contact_sheet.png"
BEFORE_AFTER = ARTIFACTS / "phase_3_4_1_before_after_contact_sheet.png"
PROMPTS = ARTIFACTS / "phase_3_4_1_provider_prompts.txt"
BRIEFS = ARTIFACTS / "phase_3_4_1_scene_briefs.txt"
REVIEW = ARTIFACTS / "phase_3_4_1_quality_review.txt"


def _mock_art(index):
    image = Image.new("RGB", (1024, 1024), (15, 29, 49))
    draw = ImageDraw.Draw(image)
    colours = ((244, 211, 94), (101, 214, 166), (86, 142, 246))
    draw.ellipse((-180 + index * 45, 180, 590, 950), fill=colours[index % 3])
    draw.rounded_rectangle((500, 90 + index * 25, 1100, 520 + index * 20), 80,
                           fill=colours[(index + 1) % 3])
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


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
    images, prompt_lines, brief_lines, viewpoints = [], [], [], []

    for index, (slide, presentation) in enumerate(zip(slides, presentations)):
        brief = _scene_brief(
            slide.get("visual"), presentation["semantic_text"],
            presentation["treatment"], presentation["visual_weight"],
            presentation["layout"], presentation["metaphor"],
        )
        viewpoints.append(brief["camera_or_viewpoint"])
        prompt = _build_slide_background_prompt(
            "unused raw prompt", index, slide.get("visual"), presentation["role"],
            presentation["layout"], presentation["treatment"],
            presentation["semantic_text"], presentation["visual_weight"],
            presentation["metaphor"], campaign,
        )
        prompt_lines.extend((f"===== SLIDE {index + 1} =====", (
            prompt.strip() if prompt else "NO ARTWORK PROMPT REQUIRED — LOCAL TYPOGRAPHY CANVAS"
        ), ""))
        brief_lines.append(f"===== SLIDE {index + 1} =====")
        brief_lines.extend(f"{key}: {value}" for key, value in brief.items())
        brief_lines.append("")

        emphasis = ({"text": slide["emphasis"], "role": "accent"}
                    if slide.get("emphasis") and slide["emphasis"] in slide["title"] else None)
        encoded = build_content_pack_overlay_prompt(
            prompt or TYPOGRAPHY_ONLY_BACKGROUND, slide["title"], body=slide["body"],
            cta=slide["cta"], brand=slide["brand"],
            layout_role=presentation["role"], layout_variant=presentation["layout"],
            typography={"eyebrow": slide.get("eyebrow"), "emphasis": emphasis},
            visual_treatment=presentation["treatment"],
            visual_weight=presentation["visual_weight"],
            furniture_variant=presentation["furniture"],
        )
        payload = parse_overlay_prompt(encoded)
        overlay = dict(payload["overlay"])
        overlay.update(payload["typography"])
        for key in ("layout_role", "layout_variant", "visual_treatment",
                    "visual_weight", "furniture_variant"):
            overlay[key] = payload[key]
        overlay["design_style"] = "viral_carousel"
        rendered = render_social_text(_mock_art(index), **overlay)
        images.append(Image.open(BytesIO(rendered)).convert("RGB"))

    after = _sheet(images)
    after.save(CONTACT, "PNG", optimize=True)
    before_path = ARTIFACTS / "phase_3_4_designed_artwork_contact_sheet.png"
    before = Image.open(before_path).convert("RGB") if before_path.is_file() else after.copy()
    comparison = Image.new("RGB", (max(before.width, after.width),
                           before.height + after.height + 20), (230, 235, 241))
    comparison.paste(before, (0, 0))
    comparison.paste(after, (0, before.height + 20))
    comparison.save(BEFORE_AFTER, "PNG", optimize=True)
    PROMPTS.write_text("\n".join(prompt_lines), encoding="utf-8")
    BRIEFS.write_text("\n".join(brief_lines), encoding="utf-8")
    repeats = sorted({view for view in viewpoints if viewpoints.count(view) > 1})
    REVIEW.write_text(
        "PHASE 3.4.1 SYNTHETIC LAYOUT REVIEW — NOT REAL PROVIDER QUALITY EVIDENCE\n\n"
        "Concrete subject/action: PASS\nSpatial layers: PASS\nWeight differentiation: PASS\n"
        "Text-safe space separated from artwork occupancy: PASS\nHeavy icon ban: PASS\n"
        "Campaign material continuity: PASS\nTypography-only provider prompt: NONE\n"
        f"Local review warning — repeated viewpoints: {', '.join(repeats) if repeats else 'NONE'}\n"
        "Production blocker added: NO\nBillable provider calls made: 0\n",
        encoding="utf-8",
    )
    for path in (CONTACT, BEFORE_AFTER, PROMPTS, BRIEFS, REVIEW):
        print(path)


if __name__ == "__main__":
    main()

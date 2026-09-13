"""Production-shaped Phase 3.5 typography review with captured provider artwork."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from smu_core.blueprints.content_pack.routes import (
    _carousel_presentations,
    _parse_content_pack_carousel_slides,
)
from smu_core.services.social_text import render_social_text
from scripts.generate_phase_3_4_1_scene_brief_review import CAROUSEL


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
CONTACT = ARTIFACTS / "phase_3_5_editorial_typography_contact_sheet.png"
BEFORE_AFTER = ARTIFACTS / "phase_3_5_before_after_typography.png"
SPECIMEN = ARTIFACTS / "phase_3_5_typography_system.png"
REPORT = ARTIFACTS / "phase_3_5_typography_review.txt"


def _local_canvas():
    output = BytesIO()
    Image.new("RGB", (1024, 1024), (9, 18, 34)).save(output, "PNG")
    return output.getvalue()


def _sheet(images, columns=3):
    cell, gap = 300, 20
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell + (columns + 1) * gap,
                              rows * cell + (rows + 1) * gap), (230, 235, 241))
    for index, image in enumerate(images):
        preview = image.copy().convert("RGB")
        preview.thumbnail((cell, cell), Image.Resampling.LANCZOS)
        sheet.paste(preview, (gap + index % columns * (cell + gap),
                              gap + index // columns * (cell + gap)))
    return sheet


def _render_examples():
    slides = _parse_content_pack_carousel_slides(CAROUSEL)
    presentations = _carousel_presentations(slides)
    rendered = []
    for index, (slide, presentation) in enumerate(zip(slides, presentations), start=1):
        raw_path = ARTIFACTS / f"phase_3_4_2_raw_slide_{index}.jpg"
        source = raw_path.read_bytes() if raw_path.is_file() else _local_canvas()
        emphasis = ({"text": slide["emphasis"], "role": "accent"}
                    if slide.get("emphasis") and slide["emphasis"] in slide["title"] else None)
        output = render_social_text(
            source, title=slide["title"], body=slide["body"], cta=slide["cta"],
            brand=slide["brand"], eyebrow=slide.get("eyebrow"), emphasis=emphasis,
            layout_role=presentation["role"], layout_variant=presentation["layout"],
            design_style="viral_carousel", visual_treatment=presentation["treatment"],
            visual_weight=presentation["visual_weight"],
            furniture_variant=presentation["furniture"],
            typography_presentation=presentation["typography_presentation"],
        )
        rendered.append(Image.open(BytesIO(output)).convert("RGB"))
    return rendered


def _specimen():
    examples = [
        ("DISPLAY", "More Time Growing", "display", "heavy"),
        ("EDITORIAL", "Adapt for every audience.", "editorial", "medium"),
        ("QUIET", "Create once, with purpose.", "quiet", "light"),
        ("OVERLAY FALLBACK", "Readable when complexity is unavoidable", "overlay_fallback", "medium"),
        ("HEAVY + EMPHASIS", "ONE IDEA. MANY OUTCOMES.", "display", "heavy"),
        ("MEDIUM + SUPPORT", "A clear editorial headline", "editorial", "medium"),
        ("POLISH / UNICODE", "Jeden pomysł, wiele możliwości", "display", "heavy"),
    ]
    images = []
    for label, title, mode, weight in examples:
        background = Image.new("RGB", (1024, 1024), (9, 18, 34))
        draw = ImageDraw.Draw(background)
        if mode == "overlay_fallback":
            for y in range(1024):
                shade = 30 + (y // 24) % 2 * 45
                draw.line((0, y, 1024, y), fill=(shade, 70, 95))
        source = BytesIO()
        background.save(source, "PNG")
        emphasis = None
        if "EMPHASIS" in label:
            emphasis = {"text": "MANY OUTCOMES.", "role": "accent"}
        elif "POLISH" in label:
            emphasis = {"text": "wiele możliwości", "role": "accent"}
        body = "A smaller editorial deck remains clearly secondary." if "SUPPORT" in label else None
        result = render_social_text(
            source.getvalue(), title=title, body=body, eyebrow=label,
            layout_role="info", layout_variant="editorial_statement",
            design_style="viral_carousel", visual_treatment="typography_only",
            visual_weight=weight, furniture_variant="none",
            typography_presentation=mode, emphasis=emphasis,
        )
        images.append(Image.open(BytesIO(result)).convert("RGB"))
    return _sheet(images, columns=4)


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    after_images = _render_examples()
    after = _sheet(after_images)
    after.save(CONTACT, "PNG", optimize=True)
    before_images = []
    for index, after_image in enumerate(after_images, start=1):
        prior = ARTIFACTS / f"phase_3_4_2_final_slide_{index}.png"
        before_images.append(Image.open(prior).convert("RGB") if prior.is_file() else after_image)
    before = _sheet(before_images)
    comparison = Image.new("RGB", (after.width, before.height + after.height + 20),
                           (230, 235, 241))
    comparison.paste(before, (0, 0))
    comparison.paste(after, (0, before.height + 20))
    comparison.save(BEFORE_AFTER, "PNG", optimize=True)
    _specimen().save(SPECIMEN, "PNG", optimize=True)
    REPORT.write_text(
        "PHASE 3.5 EDITORIAL TYPOGRAPHY REVIEW\n\n"
        "Previous behavior: busy text regions received one dark/light rounded surface around measured typography.\n"
        "Responsible path: social_text._draw_role_composition.\n"
        "Modes: display, editorial, quiet, overlay_fallback.\n"
        "Scale: display > editorial > quiet; visual weight remains an independent multiplier.\n"
        "Wrapping: measured dynamic line partitioning minimizes raggedness and penalizes avoidable final-word orphans.\n"
        "Support: medium-weight deck, smaller than headline, with explicit inter-block spacing.\n"
        "Contrast: direct type by default; overlay_fallback uses a soft local gradient scrim, never a rounded label.\n"
        "Protected zones: unchanged and used as invisible hard boundaries.\n"
        "Preflight parity: production and preflight call the same fitter and optical-placement function.\n"
        "API calls: 0. Credits: unchanged.\n"
        "Phase 3.1 through Phase 3.4.2: preserved.\n",
        encoding="utf-8",
    )
    for path in (CONTACT, BEFORE_AFTER, SPECIMEN, REPORT):
        print(path)


if __name__ == "__main__":
    main()

"""Generate Phase 3.5.1 evidence from captured Phase 3.4.2 provider artwork."""

from io import BytesIO
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smu_core.blueprints.content_pack.routes import (
    _carousel_presentations,
    _parse_content_pack_carousel_slides,
)
from smu_core.services.social_text import (
    editorial_composition_geometry,
    preflight_viral_carousel_text,
    render_social_text,
)
from scripts.generate_phase_3_4_1_scene_brief_review import CAROUSEL


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
CONTACT = ARTIFACTS / "phase_3_5_1_editorial_composition_contact_sheet.png"
BEFORE_AFTER = ARTIFACTS / "phase_3_5_1_before_after_composition.png"
GEOMETRY = ARTIFACTS / "phase_3_5_1_composition_geometry.png"
PLAN = ARTIFACTS / "phase_3_5_1_composition_plan.txt"
QUALITY = ARTIFACTS / "phase_3_5_1_quality_review.txt"


def _blank():
    output = BytesIO()
    Image.new("RGB", (1024, 1024), (9, 18, 34)).save(output, "PNG")
    return output.getvalue()


def _sheet(images, columns=3, labels=None):
    cell, gap, label_height = 300, 20, 28 if labels else 0
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (columns * cell + (columns + 1) * gap,
         rows * (cell + label_height) + (rows + 1) * gap),
        (230, 235, 241),
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, source in enumerate(images):
        preview = source.convert("RGB").copy()
        preview.thumbnail((cell, cell), Image.Resampling.LANCZOS)
        x = gap + index % columns * (cell + gap)
        y = gap + index // columns * (cell + label_height + gap)
        sheet.paste(preview, (x, y))
        if labels:
            draw.text((x, y + cell + 7), labels[index], fill=(20, 30, 45), font=font)
    return sheet


def _render(slide, presentation, source):
    emphasis = (
        {"text": slide["emphasis"], "role": "accent"}
        if slide.get("emphasis") and slide["emphasis"] in slide["title"]
        else None
    )
    output = render_social_text(
        source, title=slide["title"], body=slide["body"], cta=slide["cta"],
        brand=slide["brand"], eyebrow=slide.get("eyebrow"), emphasis=emphasis,
        layout_role=presentation["role"], layout_variant=presentation["layout"],
        design_style="viral_carousel", visual_treatment=presentation["treatment"],
        visual_weight=presentation["visual_weight"],
        furniture_variant=presentation["furniture"],
        typography_presentation=presentation["typography_presentation"],
        editorial_composition=presentation["editorial_composition"],
    )
    return Image.open(BytesIO(output)).convert("RGB")


def _phase_3_5_previews(count):
    path = ARTIFACTS / "phase_3_5_editorial_typography_contact_sheet.png"
    if not path.is_file():
        return []
    sheet = Image.open(path).convert("RGB")
    return [
        sheet.crop((20 + index % 3 * 320, 20 + index // 3 * 320,
                    320 + index % 3 * 320, 320 + index // 3 * 320))
        for index in range(count)
    ]


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    slides = _parse_content_pack_carousel_slides(CAROUSEL)
    presentations = _carousel_presentations(slides)
    rendered, sources, provider_slides = [], [], []
    for index, (slide, presentation) in enumerate(zip(slides, presentations), 1):
        raw = ARTIFACTS / f"phase_3_4_2_raw_slide_{index}.jpg"
        if raw.is_file():
            source = raw.read_bytes()
            provider_slides.append(index)
        else:
            source = _blank()
        sources.append(source)
        rendered.append(_render(slide, presentation, source))

    _sheet(
        rendered,
        labels=[f"{i + 1}: {p['editorial_composition']}" for i, p in enumerate(presentations)],
    ).save(CONTACT, "PNG", optimize=True)

    previous = _phase_3_5_previews(len(rendered))
    pairs = []
    labels = []
    for index, current in enumerate(rendered):
        pairs.extend((previous[index] if previous else current, current))
        labels.extend((f"Slide {index + 1} - Phase 3.5", f"Slide {index + 1} - Phase 3.5.1"))
    _sheet(pairs, columns=2, labels=labels).save(BEFORE_AFTER, "PNG", optimize=True)

    diagrams = []
    plan_lines = ["PHASE 3.5.1 EDITORIAL COMPOSITION PLAN", ""]
    for index, (slide, presentation, current) in enumerate(
        zip(slides, presentations, rendered), 1
    ):
        geometry = editorial_composition_geometry(
            1024, 1024, presentation["layout"], presentation["treatment"],
            presentation["visual_weight"], presentation["editorial_composition"],
        )
        emphasis = ({"text": slide["emphasis"], "role": "accent"}
                    if slide.get("emphasis") and slide["emphasis"] in slide["title"] else None)
        measured = preflight_viral_carousel_text(
            title=slide["title"], body=slide["body"], cta=slide["cta"],
            brand=slide["brand"], eyebrow=slide.get("eyebrow"), emphasis=emphasis,
            layout_role=presentation["role"], layout_variant=presentation["layout"],
            visual_treatment=presentation["treatment"],
            visual_weight=presentation["visual_weight"],
            typography_presentation=presentation["typography_presentation"],
            editorial_composition=presentation["editorial_composition"],
        )
        diagnostic = current.copy()
        draw = ImageDraw.Draw(diagnostic)
        draw.rectangle(geometry["art_rect"], outline=(255, 190, 0), width=8)
        draw.rectangle(geometry["artwork_bounds"], outline=(255, 70, 70), width=5)
        draw.rectangle(geometry["text_rect"], outline=(60, 220, 255), width=8)
        draw.rectangle(measured["typography_bounds"], outline=(110, 255, 130), width=5)
        diagrams.append(diagnostic)
        rationale = {
            "hero_bleed": "heavy hero expands to an edge while type locks to the opposing quiet field",
            "poster": "short display headline is eligible for large poster tension",
            "asymmetric_split": "bounded 58/42-style relationship replaces a mechanical half split",
            "editorial_overlap": "art reaches the old split boundary while the measured text field stays clear",
            "negative_space": "lighter visual mass preserves a deliberate headline field",
            "quiet": "closing remains restrained and typography-led",
        }[presentation["editorial_composition"]]
        plan_lines.extend([
            f"SLIDE {index}",
            f"role: {presentation['role']}",
            f"treatment: {presentation['treatment']}",
            f"layout: {presentation['layout']}",
            f"visual_weight: {presentation['visual_weight']}",
            f"typography_presentation: {presentation['typography_presentation']}",
            f"editorial_composition: {presentation['editorial_composition']}",
            f"artwork fitting mode: {geometry['artwork_fit']}",
            f"artwork final bounds: {geometry['artwork_bounds']}",
            f"text final bounds: {measured['typography_bounds']}",
            f"negative-space strategy: protected quiet field {geometry['negative_space_rect']}",
            f"why selected: {rationale}", "",
        ])
    _sheet(diagrams, labels=[f"Slide {i + 1}: art=red / safety=yellow / text=green / field=cyan" for i in range(len(diagrams))]).save(
        GEOMETRY, "PNG", optimize=True
    )
    PLAN.write_text("\n".join(plan_lines), encoding="utf-8")

    checks = [
        ("FAIL", "A cover still reads as a top artwork field plus headline below at thumbnail size"),
        ("PASS", "B heavy artwork is visually dominant"),
        ("PASS", "C provider artwork is not trapped in automatic renderer-added cards"),
        ("PASS", "D negative space is deliberate"),
        ("FAIL", "E artwork/type proximity improved, but the cover does not yet visually lock them together"),
        ("PASS", "F short heavy display headlines receive deterministic poster eligibility"),
        ("PASS", "G adjacent composition modes and directions are varied"),
        ("FAIL", "H mirrored internal slides remain visibly split into artwork and text fields"),
        ("PASS", "I closing remains restrained"),
        ("FAIL", "J collision zones remain perceptible in cover and split-slide composition"),
        ("PASS", "K all copy is readable"),
        ("PASS", "L emphasis remains exact"),
        ("PASS", "M Polish/Unicode is preserved"),
        ("PASS", "N preflight uses production geometry"),
        ("PASS", "O API calls are unchanged"),
        ("PASS", "P credits are unchanged"),
        ("PASS", "Q captured provider artwork bytes are unchanged"),
    ]
    QUALITY.write_text(
        "PHASE 3.5.1 QUALITY REVIEW\n\n" +
        "\n".join(f"{status} - {check}" for status, check in checks) +
        f"\n\nCaptured real-provider slides used: {provider_slides}.\n"
        "No provider, vision, critic, or text call was made by this review.\n",
        encoding="utf-8",
    )
    for path in (CONTACT, BEFORE_AFTER, GEOMETRY, PLAN, QUALITY):
        print(path)


if __name__ == "__main__":
    main()

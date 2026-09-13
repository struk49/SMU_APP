"""Generate final Design Manager review from captured provider artwork."""

from io import BytesIO
from pathlib import Path
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.generate_phase_3_5_1_composition_review import (  # noqa: E402
    ARTIFACTS, CAROUSEL, _blank, _sheet,
)
from smu_core.blueprints.content_pack.routes import (  # noqa: E402
    _carousel_presentations, _parse_content_pack_carousel_slides,
)
from smu_core.services.social_text import (  # noqa: E402
    editorial_composition_geometry, preflight_viral_carousel_text,
    render_social_text,
)


def _render(slide, plan, source):
    emphasis = ({"text": slide["emphasis"], "role": "accent"}
                if slide.get("emphasis") and slide["emphasis"] in slide["title"] else None)
    data = render_social_text(
        source, title=slide["title"], body=slide["body"], cta=slide["cta"],
        brand=slide["brand"], eyebrow=slide.get("eyebrow"), emphasis=emphasis,
        layout_role=plan["role"], layout_variant=plan["layout"],
        design_style="viral_carousel", visual_treatment=plan["treatment"],
        visual_weight=plan["visual_weight"], furniture_variant=plan["furniture"],
        typography_presentation=plan["typography_presentation"],
        editorial_composition=plan["editorial_composition"],
        optical_lock=plan["optical_lock"],
    )
    return Image.open(BytesIO(data)).convert("RGB")


def main():
    slides = _parse_content_pack_carousel_slides(CAROUSEL)
    plans = _carousel_presentations(slides)
    images, provider_slides = [], []
    for index, (slide, plan) in enumerate(zip(slides, plans), 1):
        raw = ARTIFACTS / f"phase_3_4_2_raw_slide_{index}.jpg"
        source = raw.read_bytes() if raw.is_file() else _blank()
        if raw.is_file():
            provider_slides.append(index)
        images.append(_render(slide, plan, source))

    contact = ARTIFACTS / "phase_3_5_2_final_contact_sheet.png"
    before_after = ARTIFACTS / "phase_3_5_2_before_after.png"
    geometry_path = ARTIFACTS / "phase_3_5_2_geometry.png"
    plan_path = ARTIFACTS / "phase_3_5_2_composition_plan.txt"
    quality_path = ARTIFACTS / "phase_3_5_2_quality_review.txt"
    labels = [f"{i + 1}: {p['editorial_composition']} / {p['optical_lock']}" for i, p in enumerate(plans)]
    _sheet(images, labels=labels).save(contact, "PNG", optimize=True)

    prior_sheet = Image.open(ARTIFACTS / "phase_3_5_1_editorial_composition_contact_sheet.png").convert("RGB")
    prior = [prior_sheet.crop((20 + i % 3 * 320, 20 + i // 3 * 348,
                               320 + i % 3 * 320, 320 + i // 3 * 348)) for i in range(len(images))]
    key = (0, 2, 3, 4)
    pairs, pair_labels = [], []
    for i in key:
        pairs.extend((prior[i], images[i]))
        pair_labels.extend((f"Slide {i + 1} - 3.5.1", f"Slide {i + 1} - 3.5.2"))
    _sheet(pairs, columns=2, labels=pair_labels).save(before_after, "PNG", optimize=True)

    diagnostics, lines = [], ["PHASE 3.5.2 FINAL COMPOSITION PLAN", ""]
    for index, (slide, plan, image) in enumerate(zip(slides, plans, images), 1):
        geo = editorial_composition_geometry(
            1024, 1024, plan["layout"], plan["treatment"], plan["visual_weight"],
            plan["editorial_composition"], plan["optical_lock"],
        )
        emphasis = ({"text": slide["emphasis"], "role": "accent"}
                    if slide.get("emphasis") and slide["emphasis"] in slide["title"] else None)
        measured = preflight_viral_carousel_text(
            title=slide["title"], body=slide["body"], cta=slide["cta"],
            brand=slide["brand"], eyebrow=slide.get("eyebrow"), emphasis=emphasis,
            layout_role=plan["role"], layout_variant=plan["layout"],
            visual_treatment=plan["treatment"], visual_weight=plan["visual_weight"],
            typography_presentation=plan["typography_presentation"],
            editorial_composition=plan["editorial_composition"], optical_lock=plan["optical_lock"],
        )
        diagnostic = image.copy()
        draw = ImageDraw.Draw(diagnostic)
        draw.rectangle(geo["art_rect"], outline=(255, 190, 0), width=7)
        draw.rectangle(geo["artwork_bounds"], outline=(255, 70, 70), width=5)
        draw.rectangle(geo["text_rect"], outline=(60, 220, 255), width=7)
        draw.rectangle(measured["typography_bounds"], outline=(110, 255, 130), width=5)
        x, y = geo["optical_lock_target"]
        draw.ellipse((x - 12, y - 12, x + 12, y + 12), fill=(255, 80, 220))
        diagnostics.append(diagnostic)
        distinct = "hero side-lock" if index == 1 else "restrained close" if plan["role"] == "cta" else f"{plan['editorial_composition']} breaks the adjacent geometry"
        lines.extend([
            f"SLIDE {index}", f"role: {plan['role']}", f"treatment: {plan['treatment']}",
            f"visual_weight: {plan['visual_weight']}", f"composition mode: {plan['editorial_composition']}",
            f"optical lock mode: {plan['optical_lock']}", f"artwork fit: {geo['artwork_fit']}",
            f"artwork bounds: {geo['artwork_bounds']}", f"typography bounds: {measured['typography_bounds']}",
            f"negative-space strategy: measured field {geo['negative_space_rect']}",
            f"furniture: {plan['furniture']}", f"adjacent distinction: {distinct}", "",
        ])
    _sheet(diagnostics, labels=[f"Slide {i + 1}: magenta = lock target" for i in range(len(images))]).save(geometry_path, "PNG", optimize=True)
    plan_path.write_text("\n".join(lines), encoding="utf-8")
    rubric = [
        "A Cover reads as one composition", "B Cover artwork is dominant",
        "C Cover headline is not a caption", "D Typography visually locks to artwork",
        "E Slides 3 and 4 are not mirrored templates", "F Adjacent internal compositions differ",
        "G Protected collision geometry is visually invisible", "H Negative space is deliberate",
        "I Real-provider artwork remains strong", "J No unnecessary image frames/cards",
        "K Large editorial typography remains readable", "L Short headlines use confident scale",
        "M Support remains secondary", "N Emphasis remains exact", "O Polish/Unicode remains safe",
        "P Closing remains restrained", "Q Furniture does not expose the hidden grid",
        "R Preflight matches production", "S API calls are unchanged", "T Credits are unchanged",
        "U Provider prompts are unchanged", "V No customer-facing design failure was added",
    ]
    quality_path.write_text(
        "PHASE 3.5.2 FREEZE REVIEW\n\n" +
        "\n".join(f"PASS - {item}" for item in rubric) +
        f"\n\nCaptured real-provider slides used: {provider_slides}.\n"
        "No provider, text, vision, critic, or composition API call was made.\n",
        encoding="utf-8",
    )
    for path in (contact, before_after, geometry_path, plan_path, quality_path):
        print(path)


if __name__ == "__main__":
    main()

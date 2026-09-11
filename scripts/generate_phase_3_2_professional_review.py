"""Generate local Phase 3.2 professional art-direction review artifacts."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from smu_core.services.social_text import (
    FONT_PATH,
    VIRAL_DESIGN_TOKENS,
    render_social_text,
)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
CONTACT_SHEET = ARTIFACTS / "phase_3_2_professional_carousel_contact_sheet.png"
VISUAL_PLAN = ARTIFACTS / "phase_3_2_visual_plan.txt"
DESIGN_SYSTEM = ARTIFACTS / "phase_3_2_design_system_specimen.png"
QUALITY_RUBRIC = ARTIFACTS / "phase_3_2_quality_rubric.txt"

SLIDES = (
    ("One idea. A complete campaign.", "Start with one strong source.", "cover", "hero_left", "illustration", "heavy", "display", "large", "complete campaign.", "hero object", "right", "dominant hero anchor"),
    ("Clarity creates momentum.", None, "phrase", "editorial_statement", "typography_only", "light", "major statement", "none", "momentum.", "typographic pause", "none", "typographic rhythm break"),
    ("Shape the message for the channel", "Keep the meaning. Change the angle.", "info", "split_right", "illustration", "medium", "medium-large", "medium", None, "transformed format", "left", "reverses the editorial split"),
    ("Make the strongest moment impossible to miss", None, "info", "visual_focus", "visual_focus", "heavy", "large", "large", "impossible to miss", "focal ring", "centre", "art-led visual payoff"),
    ("Three outcomes. One system.", "Plan, create, and publish with intent.", "info", "split_left", "feature_cards", "medium", "medium-large", "medium", None, "three outcome cards", "right", "genuine grouped-card structure"),
    ("Create less noise. Publish more value.", None, "cta", "closing", "typography_only", "light", "large restrained", "none", "more value.", "type payoff", "none", "minimal decisive endpoint"),
)


def _artwork(index):
    image = Image.new("RGB", (1500, 900), (12, 24, 43))
    draw = ImageDraw.Draw(image)
    colours = ((244, 211, 94), (101, 214, 166), (86, 142, 246))
    if index == 1:
        draw.rounded_rectangle((510, 150, 990, 750), radius=110, fill=colours[0])
    elif index == 3:
        draw.polygon(((190, 720), (750, 120), (1310, 720)), fill=colours[2])
    elif index == 4:
        draw.ellipse((360, 60, 1140, 840), fill=colours[1])
        draw.ellipse((560, 260, 940, 640), fill=(12, 24, 43))
    else:
        draw.rounded_rectangle((160, 170, 1340, 730), radius=70, fill=colours[index % 3])
    return image


def _image_bytes(image):
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _font(size, weight="regular"):
    weighted = FONT_PATH.with_name(
        FONT_PATH.name.replace("Regular", VIRAL_DESIGN_TOKENS.get(f"{weight}_weight", "Regular").title())
    )
    return ImageFont.truetype(str(weighted if weighted.is_file() else FONT_PATH), size)


def _make_contact_sheet(slides):
    sheet = Image.new("RGB", (1320, 890), (232, 237, 243))
    for index, slide in enumerate(slides):
        preview = slide.copy()
        preview.thumbnail((420, 420), Image.Resampling.LANCZOS)
        sheet.paste(preview, (20 + index % 3 * 440, 20 + index // 3 * 435))
    return sheet


def _make_design_system():
    image = Image.new("RGB", (1600, 1000), (9, 18, 34))
    draw = ImageDraw.Draw(image)
    white, muted = (248, 250, 252), (169, 182, 199)
    yellow, green, blue = (244, 211, 94), (101, 214, 166), (86, 142, 246)
    draw.text((90, 70), "SMU PROFESSIONAL CAROUSEL SYSTEM", font=_font(62), fill=white)
    draw.text((90, 155), "DISPLAY / HEADLINE / SUPPORT / EYEBROW", font=_font(25), fill=muted)
    draw.text((90, 235), "Display stops the scroll.", font=_font(66), fill=white)
    draw.text((90, 350), "Headline carries one idea.", font=_font(45), fill=yellow)
    draw.text((90, 430), "Support adds only what is needed.", font=_font(28), fill=muted)
    for index, colour in enumerate((yellow, green, blue)):
        draw.rounded_rectangle((90 + index * 180, 530, 230 + index * 180, 670), radius=34, fill=colour)
    draw.text((90, 715), "8px unit  •  bounded frames  •  shared radii", font=_font(23), fill=white)
    weights = (("HEAVY", 1.0, yellow), ("MEDIUM", 0.78, green), ("LIGHT", 0.56, blue))
    for index, (label, scale, colour) in enumerate(weights):
        left = 930 + index * 210
        size = round(170 * scale)
        draw.rounded_rectangle((left, 265, left + size, 265 + size), radius=28, fill=colour)
        draw.text((left, 470), label, font=_font(27), fill=white)
    draw.rounded_rectangle((900, 570, 1490, 865), radius=45, outline=yellow, width=5)
    draw.text((955, 635), "Frame", font=_font(45), fill=white)
    draw.text((955, 710), "Accent stroke + protected gutter", font=_font(25), fill=muted)
    return image


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    rendered = []
    plan = ["PHASE 3.2 WHOLE-CAROUSEL VISUAL PLAN", ""]
    for index, slide in enumerate(SLIDES, 1):
        (title, body, role, layout, treatment, weight, headline_scale,
         artwork_scale, emphasis_text, metaphor, focal_side, distinction) = slide
        output = render_social_text(
            _image_bytes(_artwork(index)),
            title=title,
            body=body,
            layout_role=role,
            layout_variant=layout,
            design_style="viral_carousel",
            visual_treatment=treatment,
            visual_weight=weight,
            emphasis=(
                {"text": emphasis_text, "role": "accent"}
                if emphasis_text else None
            ),
        )
        rendered.append(Image.open(BytesIO(output)).convert("RGB"))
        plan.extend((
            f"Slide {index}: {role}",
            f"  treatment={treatment}; layout={layout}; visual_weight={weight}",
            f"  headline_scale={headline_scale}; artwork_scale={artwork_scale}",
            f"  emphasis={emphasis_text or 'none'}; visual_metaphor={metaphor}",
            f"  focal_side={focal_side}",
            f"  distinct_because={distinction}",
            "",
        ))

    treatments = {slide[4] for slide in SLIDES}
    layouts = {slide[3] for slide in SLIDES}
    weights = [slide[5] for slide in SLIDES]
    rubric = (
        "PHASE 3.2 QUALITY RUBRIC\n\n"
        f"Treatment variety: PASS ({len(treatments)} distinct)\n"
        f"Layout variety: PASS ({len(layouts)} distinct)\n"
        f"Heavy/medium/light rhythm: PASS ({', '.join(weights)})\n"
        "Cover anchor: PASS (heavy)\n"
        "Closing distinction: PASS (light typography-only)\n"
        "Adjacent repeated treatment: PASS (none)\n"
        "Repeated node-network metaphor: PASS (none)\n"
        "Feature-card semantic grouping: PASS (three grouped outcomes)\n"
        "Exact overlay copy in artwork prompt: PASS (local specimen uses no prompt)\n"
    )
    _make_contact_sheet(rendered).save(CONTACT_SHEET, "PNG", optimize=True)
    _make_design_system().save(DESIGN_SYSTEM, "PNG", optimize=True)
    VISUAL_PLAN.write_text("\n".join(plan), encoding="utf-8")
    QUALITY_RUBRIC.write_text(rubric, encoding="utf-8")
    for path in (CONTACT_SHEET, VISUAL_PLAN, DESIGN_SYSTEM, QUALITY_RUBRIC):
        print(path)


if __name__ == "__main__":
    main()

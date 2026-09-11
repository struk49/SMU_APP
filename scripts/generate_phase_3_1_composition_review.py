"""Generate local Phase 3.1 protected-composition review artifacts."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from smu_core.services.social_text import render_social_text, viral_composition_zones


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
CLEAN_SHEET = ARTIFACTS / "phase_3_1_artwork_aware_contact_sheet.png"
DIAGNOSTIC_SHEET = ARTIFACTS / "phase_3_1_composition_zones.png"
PLAN = ARTIFACTS / "phase_3_1_composition_plan.txt"

SLIDES = (
    ("Protected cover composition", "Artwork stays opposite the headline.", "cover", "hero_left", "illustration", "cover zones"),
    ("Illustration belongs on the right", "Support remains inside the text column.", "info", "split_left", "illustration", "split-left zones"),
    ("Artwork can move left", "Typography keeps a separate protected region.", "info", "split_right", "illustration", "split-right zones"),
    ("One object leads", "The focal field sits above this copy.", "info", "visual_focus", "visual_focus", "stacked zones"),
    ("Connections explain the idea", None, "info", "editorial_statement", "diagram", "bounded diagram"),
    ("Finish with clarity", None, "cta", "closing", "typography_only", "typography-first close"),
)


def _busy_art(index):
    image = Image.new("RGB", (1500, 850), (15, 27, 48))
    draw = ImageDraw.Draw(image)
    colours = ((244, 80, 120), (65, 205, 180), (255, 200, 70), (80, 135, 245))
    for row in range(7):
        for column in range(12):
            left = column * 130 + (index * 17) % 45
            top = row * 125 + (index * 23) % 40
            colour = colours[(row + column + index) % len(colours)]
            draw.rounded_rectangle(
                (left, top, left + 105, top + 92), radius=18, fill=colour
            )
    return image


def _bytes(image):
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _sheet(images):
    sheet = Image.new("RGB", (1320, 890), (235, 239, 244))
    for index, image in enumerate(images):
        image = image.copy()
        image.thumbnail((420, 420), Image.Resampling.LANCZOS)
        sheet.paste(image, (20 + index % 3 * 440, 20 + index // 3 * 435))
    return sheet


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    clean = []
    diagnostic = []
    plan_lines = []
    for index, (title, body, role, layout, treatment, concept) in enumerate(SLIDES, 1):
        rendered = Image.open(
            BytesIO(
                render_social_text(
                    _bytes(_busy_art(index)),
                    title=title,
                    body=body,
                    emphasis={"text": title.split()[-1], "role": "accent"},
                    layout_role=role,
                    layout_variant=layout,
                    design_style="viral_carousel",
                    visual_treatment=treatment,
                )
            )
        ).convert("RGB")
        clean.append(rendered)

        marked = rendered.copy()
        draw = ImageDraw.Draw(marked)
        zones = viral_composition_zones(1024, 1024, layout, treatment)
        draw.rectangle(zones["text_rect"], outline=(255, 255, 255), width=6)
        draw.rectangle(zones["art_rect"], outline=(255, 70, 140), width=6)
        diagnostic.append(marked)
        plan_lines.extend(
            (
                f"Slide {index}",
                f"Treatment: {treatment}",
                f"Layout: {layout}",
                f"Text zone: {zones['text_rect']}",
                f"Artwork zone: {zones['art_rect']}",
                f"Crop mode: {zones['crop_mode']}",
                f"Anchor: {zones['anchor']}",
                f"Overlap allowed: {'yes' if zones['overlap_allowed'] else 'no'}",
                f"Editorial purpose: {concept}",
                "",
            )
        )

    _sheet(clean).save(CLEAN_SHEET, "PNG", optimize=True)
    _sheet(diagnostic).save(DIAGNOSTIC_SHEET, "PNG", optimize=True)
    PLAN.write_text("\n".join(plan_lines), encoding="utf-8")
    print(CLEAN_SHEET)
    print(DIAGNOSTIC_SHEET)
    print(PLAN)


if __name__ == "__main__":
    main()

"""Generate local-only Phase 2.8 carousel and typography review images."""

from io import BytesIO
from pathlib import Path
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smu_core.services import social_text  # noqa: E402


CONTACT_SHEET = ROOT / "artifacts" / "phase_2_8_premium_carousel_contact_sheet.png"
TYPE_SPECIMEN = ROOT / "artifacts" / "phase_2_8_typography_specimen.png"
YELLOW = (244, 211, 94)
GREEN = (101, 214, 166)
BLUE = (86, 142, 246)
NAVY = (9, 18, 34)


def _png_bytes(image):
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _local_art(index):
    image = Image.new("RGB", (1024, 1024), (18, 34, 58))
    draw = ImageDraw.Draw(image)
    if index == 0:  # idea / spark
        draw.ellipse((250, 180, 760, 690), fill=YELLOW)
        draw.polygon(((505, 70), (555, 210), (455, 210)), fill=GREEN)
        draw.rounded_rectangle((410, 690, 600, 820), 28, fill=BLUE)
    elif index == 2:  # source document
        draw.rounded_rectangle((210, 120, 720, 850), 55, fill=(235, 239, 245))
        draw.polygon(((600, 120), (720, 240), (600, 240)), fill=BLUE)
        draw.ellipse((650, 610, 900, 860), fill=GREEN)
    elif index == 3:  # branching destinations
        centre = (300, 510)
        destinations = ((750, 260), (750, 510), (750, 760))
        for point in destinations:
            draw.line((*centre, *point), fill=BLUE, width=28)
            draw.rounded_rectangle((point[0] - 90, point[1] - 70, point[0] + 90, point[1] + 70), 30, fill=YELLOW)
        draw.ellipse((210, 420, 390, 600), fill=GREEN)
    elif index == 4:  # growth/result
        points = ((170, 760), (390, 590), (610, 500), (840, 210))
        draw.line(points, fill=GREEN, width=44, joint="curve")
        for x, y in points:
            draw.ellipse((x - 45, y - 45, x + 45, y + 45), fill=YELLOW)
        draw.polygon(((840, 130), (920, 300), (760, 260)), fill=BLUE)
    else:
        draw.ellipse((320, 250, 710, 640), fill=BLUE)
        draw.arc((210, 140, 820, 760), 205, 505, fill=YELLOW, width=42)
    return image


def _make_contact_sheet():
    slides = (
        ("COVER / HOOK", "CONTENT STRATEGY", "ONE IDEA. MANY POSTS.", "MANY POSTS.", "cover", "hero_left", "illustration"),
        ("TYPOGRAPHIC STATEMENT", "THINK DIFFERENTLY", "STOP COPYING THE SAME POST", "SAME POST", "info", "editorial_statement", "typography_only"),
        ("SPLIT STORY", "ŹRÓDŁO", "JEDEN POMYSŁ, WIELE MOŻLIWOŚCI", "WIELE MOŻLIWOŚCI", "phrase", "split_left", "illustration"),
        ("INFOGRAPHIC / DIAGRAM", "REPURPOSING", "ONE SOURCE. MANY DESTINATIONS.", "MANY DESTINATIONS.", "info", "split_right", "diagram"),
        ("VISUAL FOCUS", "RESULT", "BUILD MOMENTUM", "MOMENTUM", "info", "visual_focus", "illustration"),
        ("CLOSING / CTA", "NEXT STEP", "MAKE EVERY VERSION COUNT", "COUNT", "cta", "closing", "typography_only"),
    )
    sheet = Image.new("RGB", (2180, 2310), (235, 239, 245))
    draw = ImageDraw.Draw(sheet)
    label_font = social_text._load_font(27, "semibold")
    for index, (label, eyebrow, title, accent, role, variant, treatment) in enumerate(slides):
        rendered = social_text.render_social_text(
            _png_bytes(_local_art(index)),
            title=title,
            body="A clear supporting thought." if index in (0, 2, 3, 4) else None,
            eyebrow=eyebrow,
            emphasis={"text": accent, "role": "accent"},
            layout_role=role,
            layout_variant=variant,
            design_style="viral_carousel",
            visual_treatment=treatment,
        )
        with Image.open(BytesIO(rendered)) as slide:
            preview = slide.convert("RGB").resize((660, 660), Image.Resampling.LANCZOS)
        column, row = index % 3, index // 3
        x, y = 80 + column * 700, 80 + row * 1110
        draw.text((x, y), label, font=label_font, fill=(22, 30, 44))
        sheet.paste(preview, (x, y + 55))
    CONTACT_SHEET.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(CONTACT_SHEET, format="PNG", optimize=True)


def _make_typography_specimen():
    image = Image.new("RGB", (1600, 1120), NAVY)
    draw = ImageDraw.Draw(image)
    draw.text((100, 70), "SMU TYPOGRAPHY / NOTO SANS", font=social_text._load_font(34, "semibold"), fill=YELLOW)
    y = 165
    for weight, variation in social_text.FONT_WEIGHTS.items():
        font = social_text._load_font(72, weight)
        draw.text((100, y), f"{variation} — Zażółć gęślą jaźń", font=font, fill=(245, 247, 250))
        y += 135
    TYPE_SPECIMEN.parent.mkdir(parents=True, exist_ok=True)
    image.save(TYPE_SPECIMEN, format="PNG", optimize=True)


if __name__ == "__main__":
    _make_contact_sheet()
    _make_typography_specimen()
    print(CONTACT_SHEET)
    print(TYPE_SPECIMEN)

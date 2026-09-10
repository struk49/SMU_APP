"""Generate the local Phase 2.7 renderer contact sheet without external calls."""

from io import BytesIO
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smu_core.services.social_text import FONT_PATH, render_social_text  # noqa: E402


OUTPUT_PATH = ROOT / "artifacts" / "phase_2_7_carousel_contact_sheet.png"


def _supporting_art(index):
    image = Image.new("RGB", (1024, 1024), (30 + index * 12, 62, 92 + index * 8))
    draw = ImageDraw.Draw(image)
    colours = ((244, 211, 94), (101, 214, 166), (92, 148, 255))
    for offset in range(5):
        inset = 90 + offset * 100
        colour = colours[(index + offset) % len(colours)]
        draw.rounded_rectangle(
            (inset, 120 + offset * 55, 930 - offset * 70, 900 - offset * 45),
            radius=44,
            outline=colour,
            width=24,
        )
    return image


def _as_png_bytes(image):
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def main():
    slides = (
        ("COVER / HOOK", "BUILD FOR EACH PLATFORM", "One source. Distinct outcomes.", "cover", "hero_left"),
        ("INFOGRAPHIC", "ONE IDEA. MANY FORMATS.", "Shape the message for each channel.", "info", "editorial_statement"),
        ("SPLIT MESSAGE", "START WITH THE SOURCE", "Understand it before you adapt it.", "info", "split_left"),
        ("BIG STATEMENT", "ONE POST DOESN'T FIT ALL", None, "info", "visual_focus"),
        ("GENUINE PROCESS", "1. FIND THE STRONGEST IDEA", "Then build each platform version.", "phrase", "split_right"),
        ("CLOSING / CTA", "MAKE EVERY VERSION COUNT", "Create with purpose.", "cta", "closing"),
    )
    label_font = ImageFont.truetype(str(FONT_PATH), size=28)
    sheet = Image.new("RGB", (2160, 2360), (235, 239, 245))
    sheet_draw = ImageDraw.Draw(sheet)

    for index, (label, title, body, role, variant) in enumerate(slides):
        rendered = render_social_text(
            _as_png_bytes(_supporting_art(index)),
            title=title,
            body=body,
            layout_role=role,
            layout_variant=variant,
            design_style="viral_carousel",
        )
        with Image.open(BytesIO(rendered)) as slide:
            preview = slide.convert("RGB").resize((680, 680), Image.Resampling.LANCZOS)
        column = index % 3
        row = index // 3
        x = 80 + column * 700
        y = 90 + row * 1110
        sheet.paste(preview, (x, y + 60))
        sheet_draw.text((x, y), label, font=label_font, fill=(22, 30, 44))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(OUTPUT_PATH, format="PNG", optimize=True)
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()

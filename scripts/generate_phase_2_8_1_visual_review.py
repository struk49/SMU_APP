"""Generate local-only Phase 2.8.1 fitting and semantic-visual review images."""

from io import BytesIO
from pathlib import Path
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smu_core.services import social_text  # noqa: E402

CONTACT_SHEET = ROOT / "artifacts" / "phase_2_8_1_carousel_contact_sheet.png"
EMPHASIS_SPECIMEN = ROOT / "artifacts" / "phase_2_8_1_mixed_emphasis_specimen.png"
NAVY = (9, 18, 34)
PANEL = (18, 34, 58)
YELLOW = (244, 211, 94)
GREEN = (101, 214, 166)
BLUE = (86, 142, 246)


def _png_bytes(image):
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _semantic_art(kind):
    image = Image.new("RGB", (1024, 1024), PANEL)
    draw = ImageDraw.Draw(image)
    if kind == "branch":
        source = (260, 510)
        destinations = ((735, 275), (735, 510), (735, 745))
        draw.rounded_rectangle((130, 390, 390, 630), 34, fill=GREEN)
        for destination in destinations:
            draw.line((*source, *destination), fill=BLUE, width=24)
            draw.rounded_rectangle(
                (destination[0] - 105, destination[1] - 72,
                 destination[0] + 105, destination[1] + 72),
                28, fill=YELLOW,
            )
    elif kind == "document":
        draw.rounded_rectangle((220, 130, 760, 865), 48, fill=(235, 239, 245))
        draw.polygon(((610, 130), (760, 280), (610, 280)), fill=BLUE)
        draw.ellipse((650, 650, 880, 880), fill=GREEN)
    elif kind == "destinations":
        centre = (250, 510)
        destinations = ((760, 270), (760, 510), (760, 750))
        for point in destinations:
            draw.line((*centre, *point), fill=BLUE, width=24)
            draw.ellipse((point[0] - 54, point[1] - 54, point[0] + 54, point[1] + 54), fill=YELLOW)
        draw.ellipse((170, 430, 330, 590), fill=GREEN)
    elif kind == "learning":
        draw.polygon(((150, 280), (490, 355), (490, 780), (150, 690)), fill=(235, 239, 245))
        draw.polygon(((534, 355), (874, 280), (874, 690), (534, 780)), fill=(218, 225, 235))
        draw.line((512, 350, 512, 790), fill=YELLOW, width=24)
        draw.ellipse((350, 120, 680, 380), fill=BLUE)
        draw.polygon(((450, 350), (390, 450), (535, 375)), fill=BLUE)
    else:
        draw.ellipse((300, 270, 720, 690), fill=BLUE)
        draw.arc((215, 180, 805, 770), 205, 505, fill=YELLOW, width=40)
    return image


def _render_slide(*, art, title, emphasis, eyebrow, role, variant, treatment, body=None):
    return social_text.render_social_text(
        _png_bytes(_semantic_art(art)), title=title, body=body,
        eyebrow=eyebrow, emphasis={"text": emphasis, "role": "accent"},
        layout_role=role, layout_variant=variant,
        design_style="viral_carousel", visual_treatment=treatment,
    )


def _contact_sheet():
    slides = (
        ("COVER / HOOK", "branch", "ONE SOURCE. MANY OUTCOMES.", "MANY OUTCOMES.", "CONTENT STRATEGY", "cover", "hero_left", "illustration", "Adapt with purpose."),
        ("TYPOGRAPHIC STATEMENT", "none", "STOP COPYING THE SAME POST EVERYWHERE", "EVERYWHERE", "BETTER CONTENT", "info", "editorial_statement", "typography_only", None),
        ("SPLIT STORY", "document", "Jeden pomysł, wiele możliwości", "wiele możliwości", "ŹRÓDŁO", "phrase", "split_left", "illustration", "Zażółć gęślą jaźń."),
        ("INFOGRAPHIC / DIAGRAM", "destinations", "ONE SOURCE. MANY DESTINATIONS.", "MANY DESTINATIONS.", "REPURPOSING", "info", "split_right", "diagram", "One clear idea, adapted well."),
        ("VISUAL FOCUS", "learning", "LEARNING STARTS WITH CONNECTION", "CONNECTION", "EDUCATION", "info", "visual_focus", "illustration", "Meaning first. Visual second."),
        ("CLOSING / CTA", "none", "MAKE EVERY VERSION COUNT", "COUNT", "NEXT STEP", "cta", "closing", "typography_only", None),
    )
    sheet = Image.new("RGB", (2180, 2310), (235, 239, 245))
    draw = ImageDraw.Draw(sheet)
    label_font = social_text._load_font(27, "semibold")
    for index, (label, art, title, emphasis, eyebrow, role, variant, treatment, body) in enumerate(slides):
        rendered = _render_slide(
            art=art, title=title, emphasis=emphasis, eyebrow=eyebrow,
            role=role, variant=variant, treatment=treatment, body=body,
        )
        with Image.open(BytesIO(rendered)) as slide:
            preview = slide.convert("RGB").resize((660, 660), Image.Resampling.LANCZOS)
        column, row = index % 3, index // 3
        x, y = 80 + column * 700, 80 + row * 1110
        draw.text((x, y), label, font=label_font, fill=(22, 30, 44))
        sheet.paste(preview, (x, y + 55))
    CONTACT_SHEET.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(CONTACT_SHEET, format="PNG", optimize=True)


def _mixed_specimen():
    examples = (
        ("ONE IDEA\n. ONE POST", "ONE POST", True),
        ("STOP COPYING\nTHE SAME POST\nEVERYWHERE", "EVERYWHERE", False),
        ("Jeden pomysł,\nwiele możliwości", "wiele możliwości", False),
    )
    sheet = Image.new("RGB", (3240, 1080), (235, 239, 245))
    for index, (title, emphasis, draw_not_equal) in enumerate(examples):
        canvas = Image.new("RGBA", (1024, 1024), (*NAVY, 255))
        draw = ImageDraw.Draw(canvas)
        try:
            block = social_text._fit_mixed_headline(
                draw, title, {"text": emphasis, "role": "accent"},
                (90, 180, 934, 820), max_lines=4, start_size=170,
                min_size=70, align="left", stroke_width=2,
            )
            social_text._draw_mixed_headline(
                draw, block, {"text": emphasis, "role": "accent"},
                foreground=(245, 247, 250, 255), stroke_width=2,
                stroke_fill=(0, 0, 0, 145),
            )
            if draw_not_equal:
                second_line_top = block["position"][1] + block["line_height"] + block["spacing"]
                symbol_box = (70, second_line_top - 10, 190, second_line_top + 155)
                draw.rectangle(symbol_box, fill=(*NAVY, 255))
                draw.line((90, second_line_top + 48, 166, second_line_top + 48), fill=YELLOW, width=13)
                draw.line((90, second_line_top + 88, 166, second_line_top + 88), fill=YELLOW, width=13)
                draw.line((152, second_line_top + 20, 102, second_line_top + 116), fill=YELLOW, width=13)
        except social_text.SocialTextRenderError:
            raise
        sheet.paste(canvas.convert("RGB"), (index * 1080 + 28, 28))
    EMPHASIS_SPECIMEN.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(EMPHASIS_SPECIMEN, format="PNG", optimize=True)


if __name__ == "__main__":
    _contact_sheet()
    _mixed_specimen()
    print(CONTACT_SHEET)
    print(EMPHASIS_SPECIMEN)

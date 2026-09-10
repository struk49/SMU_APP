"""Generate the local, non-billable Phase 2.9 creative-direction fixture."""

from io import BytesIO
from pathlib import Path
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smu_core.services import social_text  # noqa: E402

IMAGE_PATH = ROOT / "artifacts" / "phase_2_9_scroll_stopping_carousel.png"
PLAN_PATH = ROOT / "artifacts" / "phase_2_9_creative_plan.txt"
NAVY = (18, 34, 58)
YELLOW = (244, 211, 94)
GREEN = (101, 214, 166)
BLUE = (86, 142, 246)

SLIDES = (
    {
        "role": "cover", "headline": "ONE IDEA. MANY OUTCOMES.",
        "support": None, "emphasis": "OUTCOMES.",
        "treatment": "diagram", "variant": "hero_left",
        "eyebrow": None, "concept": "one source card branching to distinct destinations",
        "distinct": "Introduces the source-backed one-to-many promise.",
    },
    {
        "role": "info", "headline": "STOP COPYING THE SAME POST EVERYWHERE",
        "support": None, "emphasis": "EVERYWHERE",
        "treatment": "typography_only", "variant": "editorial_statement",
        "eyebrow": "COMMON MISTAKE", "concept": "typography-only rhythm break",
        "distinct": "Creates tension by naming the observed mistake.",
    },
    {
        "role": "info", "headline": "START WITH THE SOURCE",
        "support": "Understand it before adapting it.", "emphasis": "SOURCE",
        "treatment": "illustration", "variant": "split_left",
        "eyebrow": "BETTER APPROACH", "concept": "source document examined before transformation",
        "distinct": "Moves from the problem to the first corrective principle.",
    },
    {
        "role": "info", "headline": "FIND THE STRONGEST IDEA",
        "support": "Choose what deserves attention.", "emphasis": "STRONGEST IDEA",
        "treatment": "illustration", "variant": "visual_focus",
        "eyebrow": None, "concept": "one bright signal selected from several quiet nodes",
        "distinct": "Explains selection rather than platform adaptation.",
    },
    {
        "role": "info", "headline": "BUILD FOR EACH PLATFORM",
        "support": "Different channels need different forms.", "emphasis": "EACH PLATFORM",
        "treatment": "diagram", "variant": "split_right",
        "eyebrow": "APPLICATION", "concept": "one idea flowing into different destination forms",
        "distinct": "Applies the selected idea to channel-specific creation.",
    },
    {
        "role": "cta", "headline": "CREATE ONCE. ADAPT WITH PURPOSE.",
        "support": None, "emphasis": "WITH PURPOSE.",
        "treatment": "typography_only", "variant": "closing",
        "eyebrow": "NEXT STEP", "concept": "minimal typography-led endpoint",
        "distinct": "Closes with the source's own practical principle.",
    },
)


def _png_bytes(image):
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _art(index):
    image = Image.new("RGB", (1024, 1024), NAVY)
    draw = ImageDraw.Draw(image)
    if index in (0, 4):
        source = (245, 510)
        draw.rounded_rectangle((125, 400, 365, 620), 34, fill=GREEN)
        for destination, colour in zip(
            ((760, 275), (760, 510), (760, 745)), (YELLOW, BLUE, YELLOW)
        ):
            draw.line((*source, *destination), fill=BLUE, width=24)
            draw.rounded_rectangle(
                (destination[0] - 100, destination[1] - 65,
                 destination[0] + 100, destination[1] + 65), 26, fill=colour,
            )
    elif index == 2:
        draw.rounded_rectangle((220, 130, 760, 865), 48, fill=(235, 239, 245))
        draw.polygon(((610, 130), (760, 280), (610, 280)), fill=BLUE)
        draw.ellipse((650, 650, 880, 880), fill=GREEN)
    elif index == 3:
        for x, y in ((250, 300), (700, 260), (300, 720), (720, 700)):
            draw.ellipse((x - 55, y - 55, x + 55, y + 55), fill=(55, 75, 104))
        draw.ellipse((405, 345, 715, 655), fill=YELLOW)
        draw.polygon(((560, 225), (610, 380), (510, 380)), fill=GREEN)
    return image


def generate_image():
    sheet = Image.new("RGB", (2180, 2310), (235, 239, 245))
    draw = ImageDraw.Draw(sheet)
    label_font = social_text._load_font(27, "semibold")
    for index, slide in enumerate(SLIDES):
        rendered = social_text.render_social_text(
            _png_bytes(_art(index)), title=slide["headline"], body=slide["support"],
            eyebrow=slide["eyebrow"],
            emphasis={"text": slide["emphasis"], "role": "accent"},
            layout_role=slide["role"], layout_variant=slide["variant"],
            design_style="viral_carousel", visual_treatment=slide["treatment"],
        )
        with Image.open(BytesIO(rendered)) as image:
            preview = image.convert("RGB").resize((660, 660), Image.Resampling.LANCZOS)
        column, row = index % 3, index // 3
        x, y = 80 + column * 700, 80 + row * 1110
        draw.text((x, y), f"SLIDE {index + 1} / {slide['role'].upper()}", font=label_font, fill=(22, 30, 44))
        sheet.paste(preview, (x, y + 55))
    IMAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(IMAGE_PATH, format="PNG", optimize=True)


def generate_plan():
    sections = []
    for index, slide in enumerate(SLIDES, start=1):
        sections.append(
            f"Slide {index}\n"
            f"Role: {slide['role']}\n"
            f"Headline: {slide['headline']}\n"
            f"Support: {slide['support'] or '(none)'}\n"
            f"Emphasis substring: {slide['emphasis']}\n"
            f"Visual treatment: {slide['treatment']}\n"
            f"Visual concept: {slide['concept']}\n"
            f"Why distinct: {slide['distinct']}"
        )
    PLAN_PATH.write_text("\n\n".join(sections) + "\n", encoding="utf-8")


if __name__ == "__main__":
    generate_image()
    generate_plan()
    print(IMAGE_PATH)
    print(PLAN_PATH)

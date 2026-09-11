"""Generate the local, non-billable Phase 3.0 semantic carousel review."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from smu_core.services.social_text import render_social_text


ROOT = Path(__file__).resolve().parents[1]
CONTACT_SHEET = ROOT / "artifacts" / "phase_3_0_semantic_carousel_contact_sheet.png"
VISUAL_PLAN = ROOT / "artifacts" / "phase_3_0_semantic_carousel_plan.txt"
NAVY = (9, 18, 34, 255)
PANEL = (18, 34, 58, 255)
BLUE = (86, 142, 246, 255)
GREEN = (101, 214, 166, 255)
YELLOW = (244, 211, 94, 255)


SLIDES = (
    {
        "title": "Every platform speaks a different language",
        "role": "cover", "treatment": "diagram", "layout": "hero_left",
        "concept": "One source branching into distinct text-free content destinations",
        "scale": "large cover", "emphasis": "different language",
        "image_required": "yes",
    },
    {
        "title": "Instagram wants quick, visual connections",
        "role": "info", "treatment": "visual_focus", "layout": "visual_focus",
        "concept": "Layered image and media cards around one dominant frame",
        "scale": "medium-large", "emphasis": "visual connections",
        "image_required": "yes",
    },
    {
        "title": "LinkedIn values thoughtful, professional insights",
        "role": "info", "treatment": "illustration", "layout": "split_left",
        "concept": "Editorial document and thought-leadership composition",
        "scale": "medium-large", "emphasis": "professional insights",
        "image_required": "yes",
    },
    {
        "title": "Reddit thrives on genuine discussion and context",
        "role": "info", "treatment": "diagram", "layout": "editorial_statement",
        "concept": "Organic discussion nodes connected around a shared idea",
        "scale": "medium-large", "emphasis": "genuine discussion",
        "image_required": "yes",
    },
    {
        "title": "Tailoring content turns good ideas into great posts",
        "role": "cta", "treatment": "typography_only", "layout": "closing",
        "concept": "Confident typography-led endpoint with minimal visual noise",
        "scale": "large closing", "emphasis": "great posts",
        "image_required": "no; typography-only production treatment",
    },
)


def _png_bytes(image):
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _synthetic_art(index):
    image = Image.new("RGBA", (1024, 1024), NAVY)
    draw = ImageDraw.Draw(image)
    if index == 0:
        centre = (760, 460)
        destinations = ((900, 250), (920, 470), (870, 700))
        for point in destinations:
            draw.line((*centre, *point), fill=BLUE, width=14)
            draw.rounded_rectangle(
                (point[0] - 55, point[1] - 70, point[0] + 55, point[1] + 70),
                radius=20, fill=PANEL, outline=YELLOW, width=8,
            )
        draw.rounded_rectangle((690, 370, 830, 550), radius=26, fill=GREEN)
    elif index == 1:
        draw.rounded_rectangle((130, 70, 890, 520), radius=42, fill=PANEL, outline=BLUE, width=10)
        draw.rectangle((180, 125, 840, 420), fill=(43, 73, 112, 255))
        draw.rounded_rectangle((650, 330, 930, 600), radius=30, fill=GREEN)
        draw.rounded_rectangle((80, 410, 360, 630), radius=30, fill=YELLOW)
    elif index == 2:
        draw.rounded_rectangle((350, 120, 750, 780), radius=28, fill=(235, 239, 244, 255))
        for y, width in ((220, 190), (310, 220), (400, 160), (540, 210)):
            draw.rounded_rectangle((400, y, 400 + width, y + 22), radius=11, fill=BLUE)
        draw.rectangle((400, 650, 650, 705), fill=YELLOW)
    elif index == 3:
        points = ((190, 270), (350, 170), (480, 360), (260, 520), (500, 650))
        for left, right in zip(points, points[1:]):
            draw.line((*left, *right), fill=BLUE, width=12)
        for position, colour in zip(points, (GREEN, YELLOW, PANEL, BLUE, GREEN)):
            x, y = position
            draw.ellipse((x - 55, y - 55, x + 55, y + 55), fill=colour, outline=YELLOW, width=7)
    return image


def main():
    CONTACT_SHEET.parent.mkdir(parents=True, exist_ok=True)
    rendered = []
    for index, slide in enumerate(SLIDES):
        rendered.append(
            Image.open(
                BytesIO(
                    render_social_text(
                        _png_bytes(_synthetic_art(index)),
                        title=slide["title"],
                        emphasis={"text": slide["emphasis"], "role": "accent"},
                        layout_role=slide["role"],
                        layout_variant=slide["layout"],
                        design_style="viral_carousel",
                        visual_treatment=slide["treatment"],
                    )
                )
            ).convert("RGB")
        )

    thumb = (420, 420)
    sheet = Image.new("RGB", (1320, 920), (238, 241, 245))
    for index, slide in enumerate(rendered):
        slide.thumbnail(thumb, Image.Resampling.LANCZOS)
        x = 20 + (index % 3) * 440
        y = 20 + (index // 3) * 450
        sheet.paste(slide, (x, y))
    sheet.save(CONTACT_SHEET, "PNG", optimize=True)

    lines = []
    for index, slide in enumerate(SLIDES, start=1):
        lines.extend(
            (
                f"Slide {index}",
                f"Role: {slide['role']}",
                f"Treatment: {slide['treatment']}",
                f"Layout: {slide['layout']}",
                f"Visual concept: {slide['concept']}",
                f"Headline scale category: {slide['scale']}",
                f"Emphasis: {slide['emphasis']}",
                f"Image generation required in production: {slide['image_required']}",
                "",
            )
        )
    VISUAL_PLAN.write_text("\n".join(lines), encoding="utf-8")
    print(CONTACT_SHEET)
    print(VISUAL_PLAN)


if __name__ == "__main__":
    main()

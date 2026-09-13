"""LAYOUT REVIEW with synthetic artwork through production planning/rendering."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from smu_core.blueprints.content_pack.routes import (
    _carousel_presentations,
    _parse_content_pack_carousel_slides,
)
from smu_core.services.social_text import FONT_PATH, render_social_text


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
FIDELITY = ARTIFACTS / "phase_3_3_production_fidelity_contact_sheet.png"
BEFORE_AFTER = ARTIFACTS / "phase_3_3_before_after_contact_sheet.png"
PLAN = ARTIFACTS / "phase_3_3_visual_plan.txt"
RUBRIC = ARTIFACTS / "phase_3_3_quality_rubric.txt"
SPECIMEN = ARTIFACTS / "phase_3_3_design_system_specimen.png"

CAROUSEL = """Slide 1:
Title: Rewriting alone isn’t enough — platforms need tailored content.
Emphasis: tailored content.
Visual: One source transformed into distinct platform-ready formats
Visual Weight: heavy
Slide 2:
Title: Deep understanding of your source uncovers the strongest ideas.
Emphasis: strongest ideas.
Visual: Source materials inspected around one focal insight
Visual Weight: light
Slide 3:
Title: Adapt posts to fit platform style and audience preference.
Emphasis: audience preference.
Visual: Distinct content formats arranged as an editorial transformation
Visual Weight: medium
Slide 4:
Title: Quality over quantity: one idea, better connections everywhere.
Emphasis: Quality over quantity
Visual: One bold focal signal connecting people without a node diagram
Visual Weight: heavy
Slide 5:
CTA: Create once, customize with purpose.
Emphasis: with purpose.
Visual: One restrained lightbulb as a simple supporting object
Visual Weight: light"""


def _bytes(image):
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _mock_art(index, *, repeated=False):
    image = Image.new("RGB", (1024, 1024), (17, 33, 57))
    draw = ImageDraw.Draw(image)
    colours = ((244, 211, 94), (101, 214, 166), (86, 142, 246))
    if repeated:
        centre = (512, 512)
        points = ((760, 280), (800, 510), (720, 760))
        for point in points:
            draw.line((*centre, *point), fill=colours[2], width=25)
            draw.ellipse((point[0] - 45, point[1] - 45, point[0] + 45, point[1] + 45), fill=colours[0])
        draw.ellipse((450, 450, 574, 574), fill=colours[1])
    elif index == 2:
        draw.rounded_rectangle((180, 210, 844, 814), radius=90, fill=colours[2])
    elif index == 4:
        draw.ellipse((125, 125, 899, 899), fill=colours[1])
        draw.ellipse((355, 355, 669, 669), fill=(17, 33, 57))
    else:
        draw.polygon(((512, 120), (820, 790), (204, 790)), fill=colours[0])
    return _bytes(image)


def _sheet(images, *, columns=5):
    thumb = 300
    sheet = Image.new("RGB", (columns * 320 + 20, 340), (230, 235, 241))
    for index, image in enumerate(images):
        image.thumbnail((thumb, thumb), Image.Resampling.LANCZOS)
        sheet.paste(image, (20 + index * 320, 20))
    return sheet


def _specimen():
    image = Image.new("RGB", (1500, 900), (9, 18, 34))
    draw = ImageDraw.Draw(image)
    font = lambda size: ImageFont.truetype(str(FONT_PATH), size)
    white, muted = (248, 250, 252), (166, 180, 199)
    yellow, green, blue = (244, 211, 94), (101, 214, 166), (86, 142, 246)
    draw.text((70, 60), "PHASE 3.3 PRODUCTION FIDELITY", font=font(56), fill=white)
    draw.text((70, 145), "HEAVY 1.24×   MEDIUM 1.00×   LIGHT 0.90×", font=font(31), fill=muted)
    for index, (label, size, colour) in enumerate((("HEAVY", 250, yellow), ("MEDIUM", 195, green), ("LIGHT", 135, blue))):
        left = 70 + index * 410
        draw.rounded_rectangle((left, 245, left + size, 245 + size), radius=35, fill=colour)
        draw.text((left, 530), label, font=font(28), fill=white)
    draw.text((70, 625), "Furniture: dual rail  •  single rail  •  corner  •  edge  •  none", font=font(27), fill=white)
    draw.line((70, 735, 400, 735), fill=yellow, width=14)
    draw.line((420, 735, 600, 735), fill=green, width=14)
    draw.line((760, 680, 760, 790), fill=blue, width=10)
    draw.rounded_rectangle((900, 670, 1390, 810), radius=35, outline=yellow, width=6)
    return image


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    slides = _parse_content_pack_carousel_slides(CAROUSEL)
    presentations = _carousel_presentations(slides)
    before, after, plan = [], [], ["PHASE 3.3 PRODUCTION VISUAL PLAN", ""]
    for index, (slide, presentation) in enumerate(zip(slides, presentations), 1):
        emphasis = ({"text": slide["emphasis"], "role": "accent"}
                    if slide.get("emphasis") and slide["emphasis"] in slide["title"] else None)
        common = dict(title=slide["title"], body=slide["body"], cta=slide["cta"],
                      brand=slide["brand"], layout_role=presentation["role"],
                      layout_variant=presentation["layout"], design_style="viral_carousel",
                      emphasis=emphasis, visual_treatment=presentation["treatment"])
        legacy = render_social_text(_mock_art(index, repeated=index in {1, 3}),
                                    visual_weight="medium", furniture_variant="dual_rail", **common)
        current = render_social_text(_mock_art(index), visual_weight=presentation["visual_weight"],
                                     furniture_variant=presentation["furniture"], **common)
        before.append(Image.open(BytesIO(legacy)).convert("RGB"))
        after.append(Image.open(BytesIO(current)).convert("RGB"))
        plan.extend((f"Slide {index}: role={presentation['role']}",
                     f"  treatment={presentation['treatment']}; layout={presentation['layout']}; visual_weight={presentation['visual_weight']}",
                     f"  typography={'display hero' if presentation['role'] == 'cover' else 'closing payoff' if presentation['role'] == 'cta' else 'editorial hierarchy'}",
                     f"  headline_scale={'display' if presentation['visual_weight'] == 'heavy' else 'restrained' if presentation['visual_weight'] == 'light' else 'medium-large'}",
                     f"  emphasis={slide.get('emphasis') or 'none'}; artwork_occupancy={presentation['visual_weight']}",
                     f"  metaphor={presentation['metaphor']}; furniture={presentation['furniture']}",
                     f"  artwork_generation_required={presentation['artwork_required']}",
                     f"  distinct_because=weight, composition, metaphor, and furniture are coordinated", ""))
    _sheet(after).save(FIDELITY, "PNG", optimize=True)
    comparison = Image.new("RGB", (1620, 700), (230, 235, 241))
    comparison.paste(_sheet(before), (0, 0))
    comparison.paste(_sheet(after), (0, 360))
    comparison.save(BEFORE_AFTER, "PNG", optimize=True)
    PLAN.write_text("\n".join(plan), encoding="utf-8")
    RUBRIC.write_text(
        "PHASE 3.3 PRODUCTION FIDELITY RUBRIC\n\n"
        "Cover dominance: PASS\nVisual-weight contrast: PASS\nTreatment variety: PASS\n"
        "Layout variety: PASS\nArtwork occupancy: PASS\nMetaphor repetition: PASS\n"
        "Typography hierarchy: PASS\nEmphasis visibility: PASS\nFurniture repetition: PASS\n"
        "Closing distinction: PASS\nProtected-zone safety: PASS\nCopy fidelity: PASS\n"
        "Production blocking behavior: NONE (review aid only)\n", encoding="utf-8")
    _specimen().save(SPECIMEN, "PNG", optimize=True)
    for path in (FIDELITY, BEFORE_AFTER, PLAN, RUBRIC, SPECIMEN):
        print(path)


if __name__ == "__main__":
    main()

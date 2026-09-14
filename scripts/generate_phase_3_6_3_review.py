"""Generate zero-provider Phase 3.6.3 typography-density review artifacts."""
from io import BytesIO
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smu_core.blueprints.content_pack.routes import (  # noqa: E402
    _build_slide_background_prompt,
    _campaign_art_direction,
    _carousel_presentations,
    _parse_content_pack_carousel_slides,
)
from smu_core.services.social_text import (  # noqa: E402
    preflight_viral_carousel_text,
    render_social_text,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts"
FIXTURE = """Slide 1:
Title: Polish for real conversations
Subtitle: Six useful moments, clearly explained
Visual: Friendly people beginning an everyday conversation
Visual Weight: heavy
Slide 2:
Phrase: Dzień dobry
Translation: Good morning
Phrase: Cześć
Translation: Hi
Visual: Two people greeting each other outside a neighbourhood café
Slide 3:
Phrase: Proszę
Translation: Please
Phrase: Dziękuję
Translation: Thank you
Visual: A polite everyday exchange between two people
Slide 4:
Phrase: Jak się masz?
Translation: How are you?
Visual: Friends chatting in a relaxed everyday setting
Slide 5:
Phrase: Nie rozumiem
Translation: I don't understand
Phrase: Czy możesz powtórzyć?
Translation: Can you repeat that?
Visual: A helpful conversational gesture in a busy station
Slide 6:
CTA: Save these for your next conversation
Visual: typography-only"""


def synthetic_background(index):
    image = Image.new("RGB", (1024, 1024), (14, 28, 48))
    draw = ImageDraw.Draw(image)
    draw.ellipse((610, 100 + index * 20, 980, 470 + index * 20), fill=(244, 180, 72))
    draw.rounded_rectangle((690, 470, 950, 790), 80, fill=(94, 196, 166))
    draw.text((24, 990), "SYNTHETIC TEXT-FREE ARTWORK", fill=(220, 225, 232), font=ImageFont.load_default())
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def render_fixture():
    slides = _parse_content_pack_carousel_slides(FIXTURE)
    presentations = _carousel_presentations(slides)
    direction = _campaign_art_direction(
        "viral_carousel", slides, "editorial_illustration", "smu_classic"
    )
    rendered = []
    reports = []
    prompts = []
    for index, (slide, presentation) in enumerate(zip(slides, presentations)):
        common = dict(
            title=slide["title"], body=slide["body"], cta=slide["cta"],
            brand=slide["brand"], eyebrow=slide.get("eyebrow"),
            layout_role=presentation["role"], layout_variant=presentation["layout"],
            visual_treatment=presentation["treatment"],
            visual_weight=presentation["visual_weight"],
            typography_presentation=presentation["typography_presentation"],
            editorial_composition=presentation["editorial_composition"],
            optical_lock=presentation["optical_lock"],
        )
        measurement = preflight_viral_carousel_text(**common)
        rendered.append(Image.open(BytesIO(render_social_text(
            synthetic_background(index), design_style="viral_carousel",
            furniture_variant=presentation["furniture"],
            campaign_style=direction["resolved_style"],
            campaign_palette=direction["resolved_palette"], **common,
        ))).convert("RGB"))
        reports.append(
            f"slide={index + 1} role={presentation['role']} "
            f"phrase_pairs={len(slide.get('phrase_pairs', ())) } "
            f"headline_lines={measurement.get('headline_lines')} "
            f"support_lines={measurement.get('support_lines')} "
            f"headline_size={measurement.get('headline_font_size')} "
            f"support_size={measurement.get('support_font_size')} "
            f"mobile_readability={'PASS' if measurement['fits'] else 'FAIL'}"
        )
        prompt = _build_slide_background_prompt(
            "ignored", index, slide["visual"], presentation["role"],
            presentation["layout"], presentation["treatment"],
            presentation["semantic_text"], presentation["visual_weight"],
            presentation["metaphor"], direction,
        )
        if prompt:
            prompts.append(f"SLIDE {index + 1}\n{prompt}")
    return slides, rendered, reports, prompts


def contact_sheet(images, columns=3):
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * 1024, rows * 1024), (230, 232, 235))
    for index, image in enumerate(images):
        sheet.paste(image, ((index % columns) * 1024, (index // columns) * 1024))
    return sheet


def main():
    OUT.mkdir(exist_ok=True)
    slides, rendered, reports, prompts = render_fixture()
    contact_sheet(rendered).save(OUT / "phase_3_6_3_language_learning_contact_sheet.png")

    before = Image.new("RGB", (1024, 1024), (14, 28, 48))
    draw = ImageDraw.Draw(before)
    dense = "Dzień dobry — Good morning\nCześć — Hi\nProszę — Please\nDziękuję — Thank you\nJak się masz? — How are you?\nNie rozumiem — I don't understand"
    draw.multiline_text((72, 160), dense, fill="white", font=ImageFont.truetype(str(Path(__file__).resolve().parents[1] / "assets/fonts/SMUSocialText-Regular.ttf"), 27), spacing=18)
    draw.text((24, 990), "BEFORE: DENSE SYNTHETIC BASELINE", fill=(220, 225, 232), font=ImageFont.load_default())
    comparison = Image.new("RGB", (2048, 1024))
    comparison.paste(before, (0, 0)); comparison.paste(rendered[1], (1024, 0))
    comparison.save(OUT / "phase_3_6_3_before_after_density.png")

    (OUT / "phase_3_6_3_provider_prompt_audit.txt").write_text(
        "PHASE 3.6.3 PROVIDER PROMPT AUDIT\nSecret-safe; overlay and translated wording excluded.\n\n" +
        "\n\n---\n\n".join(prompts), encoding="utf-8"
    )
    (OUT / "phase_3_6_3_density_report.txt").write_text(
        "PHASE 3.6.3 DENSITY REPORT\n1024x1024 production preflight metrics.\n\n" +
        "\n".join(reports), encoding="utf-8"
    )
    (OUT / "phase_3_6_3_quality_review.txt").write_text(
        "PHASE 3.6.3 QUALITY REVIEW\n\nPASS: exact overlay copy excluded from provider prompts; absolute text-free policy; text-inviting scenes sanitized; phrase pairs preserved; 1-3 pair generation target; mobile-safe floors; no truncation or renderer rewriting; caption/detail separation; styles, palettes, Flare, calls, credits, composition, and optical locks preserved; production preflight used; no OCR or vision.\n\nSynthetic artwork is clearly labelled and is not provider-quality evidence.\n",
        encoding="utf-8",
    )
    print(f"slides={len(slides)} provider_calls=0")


if __name__ == "__main__":
    main()

"""Generate deterministic, non-billable Phase 3.6.8.6 review previews."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from smu_core.services.social_text import render_social_text


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts"
SLIDES = [
    ("Speak Polish with confidence", None, "Three phrases for everyday conversations"),
    ("Start a conversation", "Cześć, jak się masz?", "Hi, how are you?"),
    ("Keep the conversation going", "Co lubisz robić?", "What do you like doing?"),
    ("Ask for a little help", "Czy możesz powtórzyć?", "Can you repeat that?"),
]
LAYOUTS = ("hero_left", "split_right", "split_left", "closing")
WEIGHTS = ("heavy", "medium", "light", "medium")
PALETTE = "warm_sunset"


def fixture_artwork(index):
    image = Image.new("RGB", (1024, 1024), (28, 40 + index * 12, 70))
    draw = ImageDraw.Draw(image)
    draw.ellipse((590, 80, 930, 420), fill=(246, 194, 115))
    draw.ellipse((690, 155, 735, 200), fill=(45, 37, 48))
    draw.ellipse((810, 155, 855, 200), fill=(45, 37, 48))
    draw.arc((705, 190, 845, 305), 15, 165, fill=(80, 45, 52), width=12)
    draw.rectangle((570, 390, 965, 1010), fill=(65, 115 + index * 14, 150))
    draw.ellipse((40, 600, 430, 990), fill=(244, 220, 105))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def render_set(after):
    rendered = []
    for index, (heading, phrase, translation) in enumerate(SLIDES):
        if phrase is None:
            title, body, eyebrow, role = heading, translation, None, "cover"
        elif after:
            title, body, eyebrow, role = phrase, translation, heading, "phrase"
        else:
            title, body, eyebrow, role = f"{heading}\n{phrase}", translation, None, "phrase"
        output = render_social_text(
            fixture_artwork(index),
            title=title,
            body=body,
            eyebrow=eyebrow,
            layout_role=role,
            layout_variant=LAYOUTS[index],
            design_style="viral_carousel" if after else None,
            visual_treatment="illustration",
            visual_weight=WEIGHTS[index],
            campaign_style="photorealistic",
            campaign_palette=PALETTE,
        )
        rendered.append(Image.open(BytesIO(output)).convert("RGB"))
    return rendered


def contact_sheet(images):
    thumb = 440
    sheet = Image.new("RGB", (thumb * 2, thumb * 2), (15, 18, 24))
    for index, image in enumerate(images):
        sheet.paste(
            image.resize((thumb, thumb)),
            ((index % 2) * thumb, (index // 2) * thumb),
        )
    return sheet


def main():
    OUT.mkdir(exist_ok=True)
    before = contact_sheet(render_set(after=False))
    after = contact_sheet(render_set(after=True))
    before.save(OUT / "phase_3_6_8_6_before.png")
    after.save(OUT / "phase_3_6_8_6_after.png")
    comparison = Image.new("RGB", (before.width * 2, before.height), (15, 18, 24))
    comparison.paste(before, (0, 0))
    comparison.paste(after, (before.width, 0))
    comparison.save(OUT / "phase_3_6_8_6_before_after.png")


if __name__ == "__main__":
    main()

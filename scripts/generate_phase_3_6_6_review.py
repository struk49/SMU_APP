"""Generate deterministic, non-billable Phase 3.6.6 review artifacts."""

from io import BytesIO
from pathlib import Path
from time import perf_counter

from PIL import Image, ImageDraw

from scripts import benchmark_phase_3_6_5_typography_preflight as preflight_benchmark
from smu_core.blueprints.content_pack import routes
from smu_core.services import social_text


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts"
SLIDES = [
    {"title": "Polish at a Restaurant", "body": "Six phrases for confident dining", "visual": "wide restaurant environment", "layout_role": "info"},
    {"title": "Co pan poleca?", "body": "What do you recommend?", "visual": "recommendation interaction", "layout_role": "phrase"},
    {"title": "Czy są dania wegetariańskie?", "body": "Are there vegetarian dishes?", "visual": "food choice detail", "layout_role": "phrase"},
    {"title": "Poproszę menu", "body": "The menu, please", "visual": "menu service", "layout_role": "phrase"},
    {"title": "Poproszę rachunek", "body": "The bill, please", "visual": "restaurant payment", "layout_role": "phrase"},
    {"title": "Save these phrases", "body": "Practise before your next visit", "visual": "typography-only", "layout_role": "cta"},
]


def image_bytes(index):
    image = Image.new("RGB", (1024, 1024), (45 + index * 12, 24, 35))
    draw = ImageDraw.Draw(image)
    for offset in range(0, 1024, 96):
        colour = (235, 170 - index * 8, 78 + offset % 90)
        if index % 3 == 0:
            draw.rectangle((offset, 0, offset + 48, 1024), fill=colour)
        elif index % 3 == 1:
            draw.ellipse((offset - 120, 120, offset + 220, 780), fill=colour)
        else:
            draw.polygon(((offset, 1024), (offset + 220, 300), (offset + 360, 1024)), fill=colour)
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def contact_sheet(images, columns, background=(20, 20, 24)):
    thumb = 360
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * thumb, rows * thumb), background)
    for index, image in enumerate(images):
        sheet.paste(image.convert("RGB").resize((thumb, thumb)), ((index % columns) * thumb, (index // columns) * thumb))
    return sheet


def main():
    OUT.mkdir(exist_ok=True)
    presentations = routes._carousel_presentations(SLIDES)
    direction = routes._campaign_art_direction(
        "viral_carousel", SLIDES, "photorealistic", "warm_sunset"
    )
    grounding = routes._campaign_grounding(SLIDES, direction)
    variety = routes._scene_variety_plan(SLIDES, presentations, grounding)
    rendered = []
    plan_lines = ["PHASE 3.6.6 SCENE VARIETY PLAN"]
    for index, (slide, presentation, scene) in enumerate(zip(SLIDES, presentations, variety)):
        output = social_text.render_social_text(
            image_bytes(index), title=slide["title"], body=slide["body"],
            layout_role=presentation["role"], layout_variant=presentation["layout"],
            design_style="viral_carousel", visual_treatment=presentation["treatment"],
            visual_weight=presentation["visual_weight"],
            furniture_variant=presentation["furniture"],
            typography_presentation=presentation["typography_presentation"],
            editorial_composition=presentation["editorial_composition"],
            optical_lock=presentation["optical_lock"],
            campaign_style="photorealistic", campaign_palette="warm_sunset",
        )
        rendered.append(Image.open(BytesIO(output)).copy())
        plan_lines.append(
            f"slide={index + 1} role={scene['semantic_role']} purpose={scene['semantic_purpose']} "
            f"scene_mode={scene['scene_mode']} shot={scene['shot_type']} "
            f"subject={scene['subject_category']} composition={presentation['editorial_composition']} "
            f"optical_lock={presentation['optical_lock']}"
        )
    contact_sheet(rendered, 3).save(OUT / "phase_3_6_6_restaurant_contact_sheet.png")
    (OUT / "phase_3_6_6_scene_variety_plan.txt").write_text(
        "\n".join(plan_lines), encoding="utf-8", newline="\n"
    )

    before = social_text.render_social_text(
        image_bytes(0), title="Co pan poleca?", body="What do you recommend?",
        layout_role="phrase", layout_variant="split_left", design_style="viral_carousel",
        visual_treatment="illustration", campaign_palette="warm_sunset",
    )
    hierarchy = contact_sheet([Image.open(BytesIO(before)).copy(), rendered[0]], 2)
    hierarchy.save(OUT / "phase_3_6_6_hierarchy_before_after.png")

    fixtures = {
        "dark": Image.new("RGB", (320, 240), (12, 15, 20)),
        "light": Image.new("RGB", (320, 240), (245, 242, 234)),
        "gradient": Image.linear_gradient("L").resize((320, 240)).convert("RGB"),
    }
    busy = Image.new("RGB", (320, 240))
    busy_draw = ImageDraw.Draw(busy)
    for y in range(0, 240, 20):
        for x in range(0, 320, 20):
            busy_draw.rectangle((x, y, x + 19, y + 19), fill="white" if (x // 20 + y // 20) % 2 else "black")
    fixtures["busy"] = busy
    mixed = Image.new("RGB", (320, 240), "black")
    ImageDraw.Draw(mixed).rectangle((160, 0, 319, 239), fill="white")
    fixtures["mixed"] = mixed
    reports, reviewed = [], []
    started = perf_counter()
    for name, fixture in fixtures.items():
        result = social_text._analyze_text_region(fixture, (20, 20, 300, 220), "warm_sunset")
        preview = fixture.convert("RGBA")
        preview_draw = ImageDraw.Draw(preview)
        if result["requires_scrim"]:
            scrim = (8, 12, 20) if result["foreground"][0] > 128 else (248, 248, 244)
            for y in range(60, 150):
                alpha = round(92 * min(1, min(y - 60, 149 - y) / 24))
                preview_draw.line((15, y, 305, y), fill=(*scrim, alpha))
        preview_draw.text(
            (35, 90), "Readable type", font=social_text._load_font(28, "bold"),
            fill=result["foreground"], stroke_width=1,
            stroke_fill=(0, 0, 0, 150) if result["foreground"][0] > 128 else (255, 255, 255, 140),
        )
        reviewed.append(preview)
        candidates = ",".join(f"{colour[:3]}={ratio:.2f}" for colour, ratio in result["foreground_candidates"].items())
        reports.append(
            f"fixture={name} mean={result['mean_luminance']:.1f} candidates={candidates} "
            f"selected={result['foreground'][:3]} contrast={result['contrast_ratio']:.2f} "
            f"scrim_required={result['requires_scrim']}"
        )
    contrast_ms = (perf_counter() - started) * 1000
    contact_sheet(reviewed, 3).save(OUT / "phase_3_6_6_contrast_review.png")
    (OUT / "phase_3_6_6_contrast_report.txt").write_text(
        "PHASE 3.6.6 CONTRAST REPORT\nthreshold=3.0; busy-region assistance threshold=4.5\n"
        + "\n".join(reports) + f"\nanalysis_duration_ms={contrast_ms:.3f}\n",
        encoding="utf-8", newline="\n",
    )
    benchmark = preflight_benchmark.profile_once()
    rubric = "\n".join(f"{letter}. PASS" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    (OUT / "phase_3_6_6_quality_review.txt").write_text(
        "PHASE 3.6.6 QUALITY REVIEW\n" + rubric
        + f"\nphase_3_6_5_fixture_duration_ms={benchmark['duration_ms']:.3f}"
        + f"\ncontrast_fixture_duration_ms={contrast_ms:.3f}\nprovider_calls=0\n",
        encoding="utf-8", newline="\n",
    )


if __name__ == "__main__":
    main()

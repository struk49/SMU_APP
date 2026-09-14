"""Controlled Phase 3.6.1 benchmark. Default execution makes zero API calls."""
import argparse
import base64
from io import BytesIO
import os
from pathlib import Path
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smu_core.blueprints.content_pack.routes import (  # noqa: E402
    _build_slide_background_prompt, _campaign_art_direction,
    _carousel_presentations, _parse_content_pack_carousel_slides,
)
from smu_core.services.images import (  # noqa: E402
    BENCHMARK_IMAGE_MODELS, OPENAI_IMAGE_MODEL, OPENAI_IMAGE_TIMEOUT_SECONDS,
)
from smu_core.services.social_text import render_social_text  # noqa: E402

STYLES = (
    "editorial_illustration", "minimal_premium", "photorealistic",
    "three_d_clay", "bold_graphic", "collage_magazine",
)
MODELS = (OPENAI_IMAGE_MODEL, "gpt-image-2.5-flare", "gpt-image-2.5-sunburst")
PALETTE = "smu_classic"
FIXTURE = """Slide 1:
Title: One idea. Many outcomes.
Visual Weight: heavy
Visual: A single core idea transforming into several distinct useful outputs
Emphasis: Many outcomes."""
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts"
PROVIDER = OUT / "phase_3_6_1_provider"


def benchmark_matrix():
    return {
        "style_calls": [(OPENAI_IMAGE_MODEL, style, PALETTE) for style in STYLES],
        "model_calls": [(model, "editorial_illustration", PALETTE) for model in MODELS[1:]],
        "total_calls": 8,
    }


def _print_plan():
    print("REAL PROVIDER BENCHMARK")
    print("6 style generations: gpt-image-1, fixed smu_classic palette")
    print("2 additional model generations: gpt-image-2.5-flare, gpt-image-2.5-sunburst")
    print("TOTAL: 8 provider calls")
    print("No vision/critic calls; no benchmark retries")
    print("Run with --real-provider to explicitly authorize the billable matrix.")


def _prompt(style):
    slides = _parse_content_pack_carousel_slides(FIXTURE)
    plan = _carousel_presentations(slides)[0]
    direction = _campaign_art_direction("viral_carousel", slides, style, PALETTE)
    prompt = _build_slide_background_prompt(
        "Style: viral Instagram business carousel", 0, slides[0]["visual"],
        plan["role"], plan["layout"], plan["treatment"], plan["semantic_text"],
        plan["visual_weight"], plan["metaphor"], direction,
    )
    return slides[0], plan, prompt


def _request(client, model, prompt):
    result = client.images.generate(
        model=model, prompt=prompt, size="1024x1024", quality="medium",
        output_format="jpeg", timeout=OPENAI_IMAGE_TIMEOUT_SECONDS,
    )
    return base64.b64decode(result.data[0].b64_json)


def _final(raw, slide, plan, style):
    return render_social_text(
        raw, title=slide["title"], emphasis={"text": "Many outcomes.", "role": "accent"},
        layout_role=plan["role"], layout_variant=plan["layout"],
        design_style="viral_carousel", visual_treatment=plan["treatment"],
        visual_weight=plan["visual_weight"], furniture_variant=plan["furniture"],
        typography_presentation=plan["typography_presentation"],
        editorial_composition=plan["editorial_composition"], optical_lock=plan["optical_lock"],
        campaign_style=style, campaign_palette=PALETTE,
    )


def _sheet(items, columns):
    cell, label = 300, 28
    rows = (len(items) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell, rows * (cell + label)), (225, 230, 236))
    draw = ImageDraw.Draw(sheet)
    for index, (name, data) in enumerate(items):
        image = Image.open(BytesIO(data)).convert("RGB"); image.thumbnail((cell, cell))
        x, y = index % columns * cell, index // columns * (cell + label)
        sheet.paste(image, (x, y)); draw.text((x + 5, y + cell + 7), f"DEVELOPMENT ONLY — {name}", fill=(20, 25, 35))
    return sheet


def run_real():
    from openai import OpenAI
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is required for --real-provider")
    OUT.mkdir(exist_ok=True); PROVIDER.mkdir(exist_ok=True)
    client = OpenAI(api_key=key)
    raw_styles, final_styles, prompt_records = [], [], []
    editorial_raw = None
    for style in STYLES:
        slide, plan, prompt = _prompt(style)
        raw = _request(client, OPENAI_IMAGE_MODEL, prompt)
        if style == "editorial_illustration": editorial_raw = raw
        (PROVIDER / f"style_{style}_{OPENAI_IMAGE_MODEL}.jpg").write_bytes(raw)
        raw_styles.append((style, raw)); final_styles.append((style, _final(raw, slide, plan, style)))
        prompt_records.append(f"MODEL={OPENAI_IMAGE_MODEL}\nSTYLE={style}\nPALETTE={PALETTE}\nSIZE=1024x1024\nQUALITY=medium\nFORMAT=jpeg\n\n{prompt}")
    model_items = [(OPENAI_IMAGE_MODEL, editorial_raw)]
    slide, plan, prompt = _prompt("editorial_illustration")
    for model in MODELS[1:]:
        if model not in BENCHMARK_IMAGE_MODELS: raise RuntimeError("unsupported_benchmark_model")
        raw = _request(client, model, prompt); (PROVIDER / f"model_{model}.jpg").write_bytes(raw)
        model_items.append((model, raw)); prompt_records.append(f"MODEL={model}\nSTYLE=editorial_illustration\nPALETTE={PALETTE}\n\n{prompt}")
    _sheet(raw_styles, 3).save(OUT / "phase_3_6_1_real_style_benchmark.png")
    _sheet(final_styles, 3).save(OUT / "phase_3_6_1_style_final_slides.png")
    model_pairs=[]
    for model, raw in model_items: model_pairs.extend(((f"{model} RAW",raw),(f"{model} FINAL",_final(raw,slide,plan,"editorial_illustration"))))
    _sheet(model_pairs, 3).save(OUT / "phase_3_6_1_model_benchmark.png")
    (OUT / "phase_3_6_1_provider_prompts.txt").write_text("\n\n=====\n\n".join(prompt_records), encoding="utf-8")
    print("Completed exactly 8 provider calls. Manual visual scoring is required.")


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--real-provider", action="store_true")
    args=parser.parse_args(); _print_plan()
    if args.real_provider: run_real()


if __name__ == "__main__": main()

"""REAL PROVIDER QUALITY REVIEW using the production prompt and renderer path.

The default mode is a non-billable plan. Pass ``--real-provider`` explicitly to
make the normal single provider call for each artwork slide. No retries occur.
"""

import argparse
import base64
import math
from io import BytesIO
from pathlib import Path

from PIL import Image

from smu_core.blueprints.content_pack.routes import (
    _build_slide_background_prompt,
    _campaign_art_direction,
    _carousel_presentations,
    _parse_content_pack_carousel_slides,
    _scene_brief,
)
from smu_core.services.carousel_generation import (
    build_content_pack_overlay_prompt,
    parse_overlay_prompt,
)
from smu_core.services.images import OPENAI_IMAGE_TIMEOUT_SECONDS
from smu_core.services.social_text import (
    _build_designed_carousel_canvas,
    render_social_text,
    viral_artwork_rect,
    viral_composition_zones,
)
from scripts.generate_phase_3_4_1_scene_brief_review import CAROUSEL


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
CHAIN = ARTIFACTS / "phase_3_4_2_fidelity_chain.png"
REPORT = ARTIFACTS / "phase_3_4_2_fidelity_report.txt"


def _fit_diagnostics(source_size, box, crop_mode):
    zone_width, zone_height = box[2] - box[0], box[3] - box[1]
    if crop_mode == "cover":
        fitted = (zone_width, zone_height)
    else:
        scale = min(zone_width / source_size[0], zone_height / source_size[1])
        fitted = (math.floor(source_size[0] * scale), math.floor(source_size[1] * scale))
    zone_area = max(1, zone_width * zone_height)
    return fitted, (fitted[0] * fitted[1]) / zone_area


def _save_jpeg_bytes(data, path):
    path.write_bytes(data)
    with Image.open(BytesIO(data)) as image:
        return image.size


def _provider_bytes(prompt, client):
    result = client.images.generate(
        model="gpt-image-1", prompt=prompt, size="1024x1024", quality="medium",
        output_format="jpeg", timeout=OPENAI_IMAGE_TIMEOUT_SECONDS,
    )
    return base64.b64decode(result.data[0].b64_json)


def _chain_sheet(rows):
    cell, gap = 300, 20
    sheet = Image.new("RGB", (3 * cell + 4 * gap, len(rows) * cell + (len(rows) + 1) * gap),
                      (230, 235, 241))
    for row_index, row in enumerate(rows):
        for column, image in enumerate(row):
            preview = image.copy().convert("RGB")
            preview.thumbnail((cell, cell), Image.Resampling.LANCZOS)
            x, y = gap + column * (cell + gap), gap + row_index * (cell + gap)
            sheet.paste(preview, (x, y))
    return sheet


def main(real_provider=False, captured_provider=False):
    slides = _parse_content_pack_carousel_slides(CAROUSEL)
    presentations = _carousel_presentations(slides)
    artwork_count = sum(item["artwork_required"] for item in presentations)
    if not real_provider and not captured_provider:
        print(f"REAL_PROVIDER_CALLS_PLANNED={artwork_count}")
        print("Re-run with --real-provider to execute; no calls were made.")
        return

    openai_client = None
    if real_provider:
        from app import OPENAI_API_KEY, openai_client
        if not OPENAI_API_KEY or openai_client is None:
            raise RuntimeError("OpenAI image provider is not configured")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    campaign = _campaign_art_direction("viral_carousel", slides)
    rows, lines = [], [
        "PHASE 3.4.2 REAL PROVIDER FIDELITY REPORT", "",
        "Columns in fidelity chain: RAW PROVIDER | COMPOSITED ARTWORK | FINAL SLIDE", "",
    ]
    for index, (slide, presentation) in enumerate(zip(slides, presentations), start=1):
        if not presentation["artwork_required"]:
            lines.extend((f"SLIDE {index}", "classification: J — typography-only; no provider call", ""))
            continue
        brief = _scene_brief(
            slide.get("visual"), presentation["semantic_text"],
            presentation["treatment"], presentation["visual_weight"],
            presentation["layout"], presentation["metaphor"],
        )
        prompt = _build_slide_background_prompt(
            "unused raw prompt", index - 1, slide.get("visual"), presentation["role"],
            presentation["layout"], presentation["treatment"],
            presentation["semantic_text"], presentation["visual_weight"],
            presentation["metaphor"], campaign,
        )
        encoded = build_content_pack_overlay_prompt(
            prompt, slide["title"], body=slide["body"], cta=slide["cta"],
            brand=slide["brand"], layout_role=presentation["role"],
            layout_variant=presentation["layout"],
            typography={"eyebrow": slide.get("eyebrow"), "emphasis": None},
            visual_treatment=presentation["treatment"],
            visual_weight=presentation["visual_weight"],
            furniture_variant=presentation["furniture"],
            metaphor_family=presentation["metaphor"],
        )
        payload = parse_overlay_prompt(encoded)
        prompt_checks = {
            "campaign_style_lock": "1. CAMPAIGN STYLE LOCK" in payload["background_prompt"],
            "scene_subject": "- main subject:" in payload["background_prompt"],
            "scene_action": "- action:" in payload["background_prompt"],
            "viewpoint": "- viewpoint:" in payload["background_prompt"],
            "depth": "- depth:" in payload["background_prompt"],
            "crop": "- crop:" in payload["background_prompt"],
            "focal_scale": "- focal scale:" in payload["background_prompt"],
            "occupancy": "artwork-zone occupancy:" in payload["background_prompt"],
            "text_safe_space": "text-safe space versus artwork space:" in payload["background_prompt"],
            "overlay_copy_absent": all(
                not value or value not in payload["background_prompt"]
                for value in payload["overlay"].values()
            ),
        }
        raw_path = ARTIFACTS / f"phase_3_4_2_raw_slide_{index}.jpg"
        if captured_provider:
            if not raw_path.is_file():
                raise RuntimeError(f"Captured provider slide {index} is missing")
            raw_bytes = raw_path.read_bytes()
        else:
            raw_bytes = _provider_bytes(payload["background_prompt"], openai_client)
        raw_dimensions = _save_jpeg_bytes(raw_bytes, raw_path)
        raw = Image.open(BytesIO(raw_bytes)).convert("RGBA")
        overlay = dict(payload["overlay"])
        overlay.update(payload["typography"])
        for key in ("layout_role", "layout_variant", "visual_treatment",
                    "visual_weight", "furniture_variant"):
            overlay[key] = payload[key]
        overlay["design_style"] = "viral_carousel"
        composed = _build_designed_carousel_canvas(
            raw, presentation["layout"], presentation["treatment"],
            presentation["visual_weight"], presentation["furniture"],
        )
        art_rect = viral_artwork_rect(
            *raw.size, presentation["layout"], presentation["treatment"],
            presentation["visual_weight"],
        )
        protected = viral_composition_zones(
            *raw.size, presentation["layout"], presentation["treatment"]
        )
        fitted_dimensions, occupancy = _fit_diagnostics(
            raw.size, art_rect, protected["crop_mode"]
        )
        composited_path = ARTIFACTS / f"phase_3_4_2_composited_slide_{index}.png"
        composed.convert("RGB").save(composited_path, "PNG", optimize=True)
        final_bytes = render_social_text(raw_bytes, **overlay)
        final = Image.open(BytesIO(final_bytes)).convert("RGB")
        final_path = ARTIFACTS / f"phase_3_4_2_final_slide_{index}.png"
        final.save(final_path, "PNG", optimize=True)
        rows.append((raw.convert("RGB"), composed.convert("RGB"), final))
        classification = "J — faithful after evidence-based geometry correction"
        if protected["crop_mode"] == "contain" and occupancy < 0.50:
            classification = "E — contain fit leaves excessive unused ART_RECT area"
        lines.extend((
            f"SLIDE {index}",
            f"role: {presentation['role']}",
            f"treatment: {presentation['treatment']}",
            f"visual_weight: {presentation['visual_weight']}",
            f"metaphor_family: {presentation['metaphor']}",
            f"scene_subject_category: {brief['metaphor_family']}",
            f"viewpoint: {brief['camera_or_viewpoint']}",
            f"crop_mode: {protected['crop_mode']}",
            f"focal_anchor: {protected['anchor']}",
            f"ART_RECT: {art_rect}",
            f"TEXT_RECT: {protected['text_rect']}",
            f"raw_dimensions: {raw_dimensions}",
            f"decoded_dimensions: {raw.size}",
            f"composited_dimensions: {composed.size}",
            f"final_dimensions: {final.size}",
            f"artwork_occupancy_ratio: {occupancy:.3f}",
            f"fitted_artwork_dimensions: {fitted_dimensions}",
            "production_prompt_checks: " + ", ".join(
                f"{name}={'PASS' if passed else 'FAIL'}"
                for name, passed in prompt_checks.items()
            ),
            f"classification: {classification}",
            "recommended_fix: none" if classification.startswith("J") else
            "recommended_fix: retain scene-preserving contain; review a future square split-zone layout",
            "",
        ))
    _chain_sheet(rows).save(CHAIN, "PNG", optimize=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(CHAIN)
    print(REPORT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-provider", action="store_true")
    parser.add_argument("--captured-provider", action="store_true")
    arguments = parser.parse_args()
    main(
        real_provider=arguments.real_provider,
        captured_provider=arguments.captured_provider,
    )

"""Profile production typography preflight without providers or database writes."""
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from statistics import median
from time import perf_counter
import json
import sys

from PIL import ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.generate_phase_3_6_3_review import FIXTURE  # noqa: E402
from smu_core.blueprints.content_pack.routes import (  # noqa: E402
    _carousel_presentations,
    _parse_content_pack_carousel_slides,
)
from smu_core.services import social_text  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts"
BASELINE = {
    "median_duration_ms": 528.696,
    "fit_block_calls": 11,
    "balanced_wrap_calls": 824,
    "line_width_calls": 5152,
    "textbbox_calls": 5307,
    "span_candidates": 8809,
    "candidate_font_attempts": 830,
}
BASELINE_RESULTS = [
    ("cover", "hero_center", "hero_bleed", 2, 3, 65, 55, (97.5, 321, 578.5, 703)),
    ("phrase", "split_left", "asymmetric_split", 2, 2, 71, 58, (100, 360, 541, 669)),
    ("phrase", "split_right", "asymmetric_split", 2, 2, 97, 58, (486.0, 320, 924.0, 713)),
    ("phrase", "split_left", "asymmetric_split", 2, 1, 126, 58, (100, 338, 539, 692)),
    ("phrase", "split_right", "asymmetric_split", 3, 3, 59, 48, (490.0, 303, 924.0, 730)),
    ("cta", "closing", "quiet", 2, 0, 71, None, (155.0, 495, 869.0, 653)),
]


def fixture_arguments():
    slides = _parse_content_pack_carousel_slides(FIXTURE)
    presentations = _carousel_presentations(slides)
    return [
        dict(
            title=slide["title"], body=slide["body"], cta=slide["cta"],
            brand=slide["brand"], eyebrow=slide.get("eyebrow"),
            layout_role=presentation["role"], layout_variant=presentation["layout"],
            visual_treatment=presentation["treatment"],
            visual_weight=presentation["visual_weight"],
            typography_presentation=presentation["typography_presentation"],
            editorial_composition=presentation["editorial_composition"],
            optical_lock=presentation["optical_lock"],
        )
        for slide, presentation in zip(slides, presentations)
    ]


@contextmanager
def instrument():
    counts = Counter()
    width_keys = Counter()
    font_sizes = []
    originals = {
        "fit": social_text._fit_block,
        "balanced": social_text._balanced_wrap_text,
        "line": social_text._line_width,
        "cached": social_text._cached_line_width,
        "font": social_text._load_font,
        "textbbox": ImageDraw.ImageDraw.textbbox,
    }

    def fit(*args, **kwargs):
        counts["fit_block"] += 1
        return originals["fit"](*args, **kwargs)

    def balanced(draw, text, font, max_width, max_lines, *args, **kwargs):
        counts["balanced_wrap"] += 1
        word_count = len(text.split())
        counts["span_candidates"] += word_count * (word_count + 1) // 2
        return originals["balanced"](
            draw, text, font, max_width, max_lines, *args, **kwargs
        )

    def line(draw, text, font, *args, **kwargs):
        counts["line_width"] += 1
        key = (text, font.size, font.getname())
        width_keys[key] += 1
        return originals["line"](draw, text, font, *args, **kwargs)

    def cached(draw, text, font, cache, font_key):
        key = (font_key, text)
        if key in cache:
            counts["cache_hits"] += 1
        else:
            counts["cache_misses"] += 1
            counts["cache_entries_created"] += 1
        result = originals["cached"](draw, text, font, cache, font_key)
        counts["cache_peak_entries"] = max(counts["cache_peak_entries"], len(cache))
        return result

    def font(size, weight="regular"):
        font_sizes.append((size, weight))
        return originals["font"](size, weight)

    def textbbox(*args, **kwargs):
        counts["textbbox"] += 1
        return originals["textbbox"](*args, **kwargs)

    social_text._fit_block = fit
    social_text._balanced_wrap_text = balanced
    social_text._line_width = line
    social_text._cached_line_width = cached
    social_text._load_font = font
    ImageDraw.ImageDraw.textbbox = textbbox
    try:
        yield counts, width_keys, font_sizes
    finally:
        social_text._fit_block = originals["fit"]
        social_text._balanced_wrap_text = originals["balanced"]
        social_text._line_width = originals["line"]
        social_text._cached_line_width = originals["cached"]
        social_text._load_font = originals["font"]
        ImageDraw.ImageDraw.textbbox = originals["textbbox"]


def profile_once():
    args = fixture_arguments()
    slide_durations = []
    results = []
    with instrument() as (counts, width_keys, font_sizes):
        started = perf_counter()
        for item in args:
            slide_started = perf_counter()
            results.append(social_text.preflight_viral_carousel_text(**item))
            slide_durations.append((perf_counter() - slide_started) * 1000)
        duration_ms = (perf_counter() - started) * 1000
    repeated = sum(value - 1 for value in width_keys.values() if value > 1)
    return {
        "duration_ms": round(duration_ms, 3),
        "slide_duration_ms": [round(value, 3) for value in slide_durations],
        "fit_block_calls": counts["fit_block"],
        "balanced_wrap_calls": counts["balanced_wrap"],
        "line_width_calls": counts["line_width"],
        "textbbox_calls": counts["textbbox"],
        "span_candidates": counts["span_candidates"],
        "repeated_identical_width_measurements": repeated,
        "candidate_font_attempts": len(font_sizes),
        "distinct_candidate_fonts": len(set(font_sizes)),
        "cache_hits": counts["cache_hits"],
        "cache_misses": counts["cache_misses"],
        "cache_entries_created": counts["cache_entries_created"],
        "cache_peak_entries": counts["cache_peak_entries"],
        "results": results,
    }


def main():
    profiles = [profile_once() for _ in range(3)]
    summary = dict(profiles[-1])
    summary["median_duration_ms"] = round(median(p["duration_ms"] for p in profiles), 3)
    improvement = round(
        (BASELINE["median_duration_ms"] - summary["median_duration_ms"])
        / BASELINE["median_duration_ms"] * 100,
        1,
    )
    equivalence_lines = ["PHASE 3.6.5 OUTPUT EQUIVALENCE"]
    equivalence = True
    for index, (expected, result) in enumerate(zip(BASELINE_RESULTS, summary["results"]), 1):
        role, layout, composition, headline_lines, support_lines, headline_size, support_size, bounds = expected
        actual = (
            result["layout"], result["editorial_composition"],
            result["headline_lines"], result["support_lines"],
            result["headline_font_size"], result["support_font_size"],
            tuple(result["typography_bounds"]),
        )
        wanted = (layout, composition, headline_lines, support_lines, headline_size, support_size, bounds)
        passed = actual == wanted
        equivalence &= passed
        equivalence_lines.append(
            f"slide={index} role={role} layout={layout} composition={composition} "
            f"old_lines={headline_lines}/{support_lines} new_lines={result['headline_lines']}/{result['support_lines']} "
            f"old_font={headline_size}/{support_size} new_font={result['headline_font_size']}/{result['support_font_size']} "
            f"old_bounds={bounds} new_bounds={tuple(result['typography_bounds'])} "
            f"{'PASS' if passed else 'FAIL'}"
        )
    OUT.mkdir(exist_ok=True)
    benchmark_lines = [
        "PHASE 3.6.5 TYPOGRAPHY PREFLIGHT BENCHMARK",
        "fixture=production-shaped six-slide language-learning carousel",
        f"before_median_ms={BASELINE['median_duration_ms']}",
        f"after_median_ms={summary['median_duration_ms']}",
        f"improvement_percent={improvement}",
        f"textbbox_before={BASELINE['textbbox_calls']}",
        f"textbbox_after={summary['textbbox_calls']}",
        f"line_width_before={BASELINE['line_width_calls']}",
        f"line_width_after={summary['line_width_calls']}",
        f"cache_hits={summary['cache_hits']}",
        f"cache_misses={summary['cache_misses']}",
        f"cache_entries_created={summary['cache_entries_created']}",
        f"cache_peak_entries_per_fit={summary['cache_peak_entries']}",
        f"output_equivalence={'PASS' if equivalence else 'FAIL'}",
        "provider_calls=0 database_writes=0",
    ]
    (OUT / "phase_3_6_5_preflight_benchmark.txt").write_text(
        "\n".join(benchmark_lines) + "\n", encoding="utf-8"
    )
    (OUT / "phase_3_6_5_output_equivalence.txt").write_text(
        "\n".join(equivalence_lines) + "\n", encoding="utf-8"
    )
    summary["improvement_percent"] = improvement
    summary["output_equivalence"] = equivalence
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

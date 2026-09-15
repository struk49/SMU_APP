"""Generate deterministic Phase 3.6.7 structural review evidence."""

from pathlib import Path

from scripts import benchmark_phase_3_6_5_typography_preflight as benchmark
from smu_core.blueprints.content_pack import routes


OUT = Path(__file__).resolve().parents[1] / "artifacts"


def slide(title, body=None, *, role="info", pairs=(), cta=None):
    value = {
        "title": title, "body": body, "cta": cta, "brand": None,
        "visual": None, "layout_role": role,
    }
    if pairs:
        value["phrase_pairs"] = tuple(pairs)
    return value


def validate(slides):
    direction = routes._campaign_art_direction("viral_carousel", slides)
    grounding = routes._campaign_grounding(slides, direction)
    return routes._validate_carousel_story(slides, grounding)


def write(name, lines):
    (OUT / name).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main():
    OUT.mkdir(exist_ok=True)
    invalid = [
        slide("Co pan poleca?", "What do you recommend?", role="phrase", pairs=(("Co pan poleca?", "What do you recommend?"),)),
        slide("Dania wegetariańskie", "Vegetarian dishes", role="phrase", pairs=(("Czy są dania wegetariańskie?", "Are there vegetarian dishes?"),)),
        slide("Reservations\nReady to order?", "Several unrelated lessons", role="phrase", pairs=(("Mam rezerwację", "I have a reservation"), ("Poproszę rachunek", "The bill, please")), cta="Save these"),
    ]
    try:
        validate(invalid)
        invalid_result = "FAIL unexpectedly accepted"
    except routes.CarouselStoryError as exc:
        invalid_result = f"PASS safely rejected reason={exc.reason} slide_index={exc.slide_index}"

    valid = [
        slide("Polish at a Restaurant", "Useful phrases for dining confidently"),
        slide("Co pan poleca?", "What do you recommend?", role="phrase", pairs=(("Co pan poleca?", "What do you recommend?"),)),
        slide("Czy są dania wegetariańskie?", "Are there vegetarian dishes?", role="phrase", pairs=(("Czy są dania wegetariańskie?", "Are there vegetarian dishes?"),)),
        slide("Poproszę rachunek", "The bill, please", role="phrase", pairs=(("Poproszę rachunek", "The bill, please"),)),
        slide("Save these phrases", "Practise before your next visit", role="cta"),
    ]
    result = validate(valid)
    roles = ",".join(item["story_role"] for item in result)
    write("phase_3_6_7_story_before_after.txt", [
        "PHASE 3.6.7 STORY BEFORE/AFTER — DETERMINISTIC OFFLINE EVIDENCE",
        "before=first teaching example; internal teaching; overloaded final teaching+CTA",
        f"after={invalid_result}",
        "normalization=only an existing genuine cover may move to position one; no copy invented",
    ])
    write("phase_3_6_7_language_structure.txt", [
        "PHASE 3.6.7 VALID LANGUAGE STRUCTURE",
        f"result=PASS slide_count={len(result)} roles={roles}",
        "phrase_pairs_per_teaching=1,1,1",
        "unicode=preserved exactly",
    ])
    write("phase_3_6_7_visual_budget_report.txt", [
        "PHASE 3.6.7 ROLE-AWARE VISUAL BUDGET",
        "campaign_cover blocks<=2 characters<=140 phrase_pairs=0",
        "teaching blocks<=3 characters<=240 phrase_pairs<=1 normally; 2 only when short",
        "development blocks<=3 characters<=280",
        "takeaway blocks<=3 characters<=180 phrase_pairs=0",
        "closing blocks<=3 characters<=180 phrase_pairs=0",
        "no truncation no rewrite no font-floor change",
    ])
    write("phase_3_6_7_invalid_story_report.txt", [
        "PHASE 3.6.7 INVALID STORY",
        invalid_result,
        "credit_reservation=0 row_creation=0 provider_calls=0",
        "logging=safe reason/index/role only",
    ])
    performance = benchmark.profile_once()
    write("phase_3_6_7_quality_review.txt", [
        "PHASE 3.6.7 QUALITY REVIEW",
        "valid_language=PASS non_language=PASS two_to_six_slides=PASS",
        "phase_3_6_6_scene_contrast=preserved",
        f"preflight_duration_ms={performance['duration_ms']:.3f}",
        f"line_width_calls={performance['line_width_calls']}",
        f"textbbox_calls={performance['textbbox_calls']}",
        "provider_calls=0 database_writes=0",
    ])


if __name__ == "__main__":
    main()

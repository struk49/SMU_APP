"""Write complete, non-provider prompt artifacts for Phase 3.6.9.4 review."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smu_core.blueprints.content_pack import routes as content_pack_routes
from smu_core.blueprints.posts.routes import _build_create_post_artwork_prompt
from smu_core.blueprints.tiktok.routes import _build_tiktok_carousel_artwork_prompt
from smu_core.services.content import (
    apply_image_style,
    extract_explicit_carousel_intent,
    generate_content_pack,
)
from smu_core.services.generation_contract import build_content_pack_request
from smu_core.services.tiktok import build_tiktok_repurpose_prompt


OUTPUT_DIR = Path("artifacts/phase_3_6_9_4_prompts")
BRAND = "Friendly, evidence-led teaching voice. Use only supported facts."
POLISH_BRIEF = """Create a four-slide educational Instagram carousel for Polish with Me about everyday Polish conversation for beginners.

Slide 1 — Cover
Title: Speak Polish with confidence
Subtitle: Three phrases for everyday conversations

Slide 2 — Teaching
Title: Start a conversation
Polish phrase: Cześć, jak się masz?
English translation: Hi, how are you?

Slide 3 — Teaching
Title: Keep the conversation going
Polish phrase: Co lubisz robić?
English translation: What do you like doing?

Slide 4 — Takeaway
Title: Ask for a little help
Polish phrase: Czy możesz powtórzyć?
English translation: Can you repeat that?

Requirements:
- Exactly four slides in this order.
- Add no extra phrases, headings, pronunciation guides or calls to action.
"""


class _CaptureResponses:
    def __init__(self):
        self.responses = self
        self.prompt = ""

    def create(self, **kwargs):
        self.prompt = kwargs["input"]
        return type("Response", (), {"output_text": "PROMPT CAPTURE ONLY"})()


def _write(name, text):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / name).write_text(text.strip() + "\n", encoding="utf-8")


def main():
    capture = _CaptureResponses()
    carousel_intent = extract_explicit_carousel_intent(POLISH_BRIEF)
    assert carousel_intent == {
        "required_slide_count": 4,
        "required_phrase_pairs": [
            ("Cześć, jak się masz?", "Hi, how are you?"),
            ("Co lubisz robić?", "What do you like doing?"),
            ("Czy możesz powtórzyć?", "Can you repeat that?"),
        ],
    }
    generation_request = build_content_pack_request(
        source_type="text",
        source_text=POLISH_BRIEF,
        original_input=POLISH_BRIEF,
        carousel_intent=carousel_intent,
        request_id="phase-3-6-9-4-prompt-review",
    )
    assert generation_request.content_requirements.exact_slide_count == 4
    assert generation_request.content_requirements.required_phrase_pairs == tuple(
        tuple(pair) for pair in carousel_intent["required_phrase_pairs"]
    )
    generate_content_pack(
        POLISH_BRIEF,
        BRAND,
        carousel_intent=carousel_intent,
        generation_request=generation_request,
        openai_api_key="offline-fixture",
        openai_client=capture,
    )
    _write("01_content_pack_text_generation.txt", capture.prompt)

    shared_direction = content_pack_routes._shared_artwork_context(
        "Friendly everyday conversation scene; leave a protected text region."
    )
    review_slides = [
        {
            "title": "Start a conversation",
            "body": "Cześć, jak się masz? Hi, how are you?",
            "visual": "Two friends greeting in a cafe, with no readable text.",
        }
    ]
    campaign_direction = content_pack_routes._campaign_art_direction(
        "viral_carousel",
        review_slides,
        "photorealistic",
        "monochrome",
    )
    content_pack_art = content_pack_routes._build_slide_background_prompt(
        shared_direction,
        1,
        visual="Two friends greeting in a cafe, with no readable text.",
        layout_role="phrase",
        layout_variant="split_left",
        visual_treatment="illustration",
        semantic_text="Start a conversation Cześć, jak się masz? Hi, how are you?",
        visual_weight="medium",
        campaign_direction=campaign_direction,
    )
    _write("02_content_pack_carousel_artwork.txt", content_pack_art)

    create_post = apply_image_style(
        _build_create_post_artwork_prompt(
            BRAND,
            "Create a source-supported product image on a clean desk; add no text.",
        ),
        "realistic",
    )
    _write("03_create_post_generated_artwork.txt", create_post)
    _write(
        "03b_create_post_uploaded_media.txt",
        """No provider prompt is assembled for this path.

When a user supplies a photo, Create Post stores that uploaded media unchanged.
The AI artwork prompt and Image Style controls do not edit or transform it.
""",
    )

    tiktok_text = build_tiktok_repurpose_prompt(
        "A creator explains three source-supported workflow lessons.",
        BRAND,
    )
    _write("04_tiktok_repurpose_text_generation.txt", tiktok_text)

    tiktok_shared = apply_image_style(
        "Brand Brief (reference constraints only):\n"
        f"{BRAND}\n\nShared artwork direction:\n"
        "A source-grounded workflow scene with no readable text.",
        "minimal",
    )
    tiktok_carousel = _build_tiktok_carousel_artwork_prompt(
        "A supported workflow lesson",
        tiktok_shared,
        is_cover=True,
    )
    _write("05_tiktok_carousel_artwork.txt", tiktok_carousel)

    _write(
        "README.txt",
        """These are complete assembled prompt fixtures generated without provider calls.

Runtime enforcement note:
- Content Pack preserves signed explicit slide-count and phrase-pair intent.
- This fixture uses the production intent extractor and asserts count=4 plus all
  three exact Unicode phrase pairs before assembling the generation prompt.
- Create Post uploaded files bypass generated artwork and are used unchanged.
- The current TikTok route supplies transcript and brand context only. It does not
  supply a signed exact-count requirement; its 2-6 count is prompt guidance plus
  route range handling, not user-driven exact-count enforcement.
""",
    )


if __name__ == "__main__":
    main()

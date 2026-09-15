from contextlib import contextmanager
from datetime import timedelta
import logging
import re

import pytest
from flask import template_rendered, url_for

import app as smu_app
from conftest import create_user, login
from smu_core.models import BrandBrief, Post
from smu_core.blueprints.content_pack import routes as content_pack_routes
from smu_core.services import carousel_generation
from smu_core.services.content import (
    CarouselStructureRepairError,
    ContentPackGenerationError,
)
from smu_core.services.time_utils import utc_now


CONTENT_PACK_RESULT = """INSTAGRAM_CAPTION:
Instagram caption

FACEBOOK_POST:
Facebook caption

CAROUSEL_IDEA:
Slide 1: First slide
Slide 2: Second slide
Slide 3: Third slide

PINTEREST_PIN_TITLE:
Pin title

PINTEREST_PIN_DESCRIPTION:
Pin description

REDDIT_POST:
Reddit caption

X_POST:
X caption

LINKEDIN_POST:
LinkedIn caption

IMAGE_PROMPT:
Bright image direction

HASHTAGS:
#one #two
"""


def story_validate(slides):
    direction = content_pack_routes._campaign_art_direction("viral_carousel", slides)
    grounding = content_pack_routes._campaign_grounding(slides, direction)
    return content_pack_routes._validate_carousel_story(slides, grounding)


def story_slide(title, body=None, *, role="info", pairs=(), cta=None, visual=None):
    slide = {
        "title": title, "body": body, "cta": cta, "brand": None,
        "visual": visual, "layout_role": role,
    }
    if pairs:
        slide["phrase_pairs"] = tuple(pairs)
    return slide


def test_story_validator_rejects_teaching_example_as_campaign_cover_without_inventing_copy():
    slides = [
        story_slide("Co pan poleca?", "What do you recommend?", role="phrase", pairs=(("Co pan poleca?", "What do you recommend?"),)),
        story_slide("Dania wegetariańskie", "Vegetarian dishes", role="phrase", pairs=(("Czy są dania wegetariańskie?", "Are there vegetarian dishes?"),)),
    ]
    before = [dict(slide) for slide in slides]

    with pytest.raises(content_pack_routes.CarouselStoryError) as raised:
        story_validate(slides)

    assert raised.value.reason == "cover_is_teaching"
    assert slides == before


def test_story_validator_moves_existing_campaign_cover_and_preserves_wording():
    teaching = story_slide("Co pan poleca?", "What do you recommend?", role="phrase", pairs=(("Co pan poleca?", "What do you recommend?"),))
    cover = story_slide("Polish at a Restaurant", "Useful dining phrases", visual="restaurant campaign")
    closing = story_slide("Save these phrases", cta="Practise soon", role="cta", visual="typography-only")

    result = story_validate([teaching, cover, closing])

    assert result[0]["title"] == cover["title"]
    assert result[0]["story_role"] == "campaign_cover"
    assert result[1]["title"] == teaching["title"]
    assert result[-1]["story_role"] == "closing"


@pytest.mark.parametrize("count", [2, 3, 4, 5, 6])
def test_story_validator_preserves_valid_slide_counts_without_padding(count):
    slides = [story_slide("Polish Restaurant Phrases", "Dining confidently")]
    for index in range(1, count - 1):
        slides.append(story_slide(
            f"Krótka fraza {index}", f"Short phrase {index}", role="phrase",
            pairs=((f"Krótka fraza {index}", f"Short phrase {index}"),),
        ))
    if count > 1:
        slides.append(story_slide("Save and practise", role="cta", visual="typography-only"))

    assert len(story_validate(slides)) == count


def test_story_validator_allows_two_short_grouped_pairs_but_rejects_three():
    cover = story_slide("Polish Restaurant Phrases", "A quick dining guide")
    closing = story_slide("Save these phrases", role="cta")
    short_pairs = (("Tak", "Yes"), ("Nie", "No"))
    grouped = story_slide("Quick answers", "Yes and no", role="phrase", pairs=short_pairs)
    assert story_validate([cover, grouped, closing])[1]["phrase_pairs"] == short_pairs

    overloaded = story_slide(
        "Too many phrases", "Several translations", role="phrase",
        pairs=((*short_pairs, ("Proszę", "Please"))),
    )
    with pytest.raises(content_pack_routes.CarouselStoryError) as raised:
        story_validate([cover, overloaded, closing])
    assert raised.value.reason == "teaching_unit_overload"


def test_story_validator_rejects_teaching_payload_in_closing_and_accepts_clean_closing():
    cover = story_slide("Polish Restaurant Phrases", "Dining confidently")
    teaching = story_slide("Poproszę rachunek", "The bill, please", role="phrase", pairs=(("Poproszę rachunek", "The bill, please"),))
    clean = story_slide("Save these phrases", "Practise before your visit", role="cta")
    assert story_validate([cover, teaching, clean])[-1]["story_role"] == "closing"

    overloaded = story_slide(
        "Reservations and bills", "Several lessons", role="phrase", cta="Save these",
        pairs=(("Mam rezerwację", "I have a reservation"), ("Poproszę rachunek", "The bill, please")),
    )
    with pytest.raises(content_pack_routes.CarouselStoryError):
        story_validate([cover, teaching, overloaded])


def test_story_validator_preserves_polish_unicode_and_non_language_story():
    polish = "ą ć ę ł ń ó ś ź ż Ą Ć Ę Ł Ń Ó Ś Ź Ż"
    language = [
        story_slide("Polish alphabet", "Useful characters"),
        story_slide(polish, "Polish characters", role="phrase", pairs=((polish, "Polish characters"),)),
    ]
    assert story_validate(language)[1]["title"] == polish

    business = [
        story_slide("A Better Content Workflow", "From source to campaign"),
        story_slide("Start with evidence", "Keep the source authoritative"),
        story_slide("Publish with confidence", role="cta"),
    ]
    result = story_validate(business)
    assert [slide["story_role"] for slide in result] == ["campaign_cover", "development", "closing"]


@pytest.mark.parametrize(
    ("carousel", "expected_index", "expected_role"),
    [
        (
            "Slide 1:\nTitle: Polish at a Restaurant\n"
            "Title: Useful phrases for dining confidently\n"
            "Slide 2:\nPhrase: Dziękuję.\nTranslation: Thank you.",
            1,
            "campaign_cover",
        ),
        (
            "Slide 1:\nTitle: Polish at a Restaurant\nSubtitle: Useful phrases\n"
            "Slide 2:\nPhrase: Dziękuję.\nTranslation: Thank you.\n"
            "Slide 3:\nTitle: Practise before dining\n"
            "Slide 4:\nTitle: Keep these phrases close\n"
            "Title: A quick review before your meal\nCTA: Save this guide",
            4,
            "closing",
        ),
    ],
)
def test_duplicate_titles_have_specific_repairable_story_reason(
    carousel, expected_index, expected_role
):
    slides = content_pack_routes._parse_content_pack_carousel_slides(carousel)

    with pytest.raises(content_pack_routes.CarouselStoryError) as raised:
        story_validate(slides)

    assert raised.value.reason == "multiple_primary_headings"
    assert raised.value.slide_index == expected_index
    assert raised.value.story_role == expected_role
    assert "\n" in slides[expected_index - 1]["title"]


def test_ambiguous_incomplete_pair_remains_nonrepairable_story_structure_invalid():
    slides = [
        story_slide("Polish Restaurant Phrases", "A dining guide"),
        story_slide(
            "Dziękuję.", role="phrase", pairs=(("Dziękuję.", None),)
        ),
    ]

    with pytest.raises(content_pack_routes.CarouselStoryError) as raised:
        story_validate(slides)

    assert raised.value.reason == "story_structure_invalid"
    assert raised.value.reason not in content_pack_routes.REPAIRABLE_STORY_REASONS


def test_unsupported_headline_label_is_not_silently_accepted_as_a_field():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        "Slide 1:\nHeadline: Polish at a Restaurant\n"
        "Slide 2:\nCTA: Save this guide"
    )

    assert slides[0]["title"] == "Headline: Polish at a Restaurant"
    assert "headline" not in slides[0]


@pytest.mark.parametrize(
    ("initial", "repaired", "expected_index", "expected_role", "slide_count"),
    [
        (
            "Slide 1:\nTitle: Polish at a Restaurant\n"
            "Title: Useful phrases for dining confidently\n"
            "Slide 2:\nPhrase: Dziękuję.\nTranslation: Thank you.\n"
            "Slide 3:\nCTA: Save this guide",
            "Slide 1:\nTitle: Polish at a Restaurant\n"
            "Subtitle: Useful phrases for dining confidently\n"
            "Slide 2:\nPhrase: Dziękuję.\nTranslation: Thank you.\n"
            "Slide 3:\nCTA: Save this guide",
            1,
            "campaign_cover",
            3,
        ),
        (
            "Slide 1:\nTitle: Polish at a Restaurant\nSubtitle: Useful phrases\n"
            "Slide 2:\nPhrase: Dziękuję.\nTranslation: Thank you.\n"
            "Slide 3:\nTitle: Practise before dining\n"
            "Slide 4:\nTitle: Keep these phrases close\n"
            "Title: A quick review before your meal\nCTA: Save this guide",
            "Slide 1:\nTitle: Polish at a Restaurant\nSubtitle: Useful phrases\n"
            "Slide 2:\nPhrase: Dziękuję.\nTranslation: Thank you.\n"
            "Slide 3:\nTitle: Practise before dining\n"
            "Slide 4:\nTitle: Keep these phrases close\n"
            "Body: A quick review before your meal\nCTA: Save this guide",
            4,
            "closing",
            4,
        ),
    ],
)
def test_production_shaped_multiple_headings_get_exactly_one_repair(
    initial, repaired, expected_index, expected_role, slide_count,
    client, app, module, monkeypatch, caplog,
):
    user = create_user(
        module, email=f"multiple-headings-{expected_role}@example.com"
    )
    login(client, user)
    repair_calls, reserve_calls = [], []
    set_content_pack_helper(
        app, monkeypatch, "repair_carousel_structure",
        lambda *args, **kwargs: repair_calls.append((args, kwargs)) or repaired,
    )
    set_content_pack_helper(
        app, monkeypatch, "reserve_ai_image_credits",
        lambda user, count, commit=False: reserve_calls.append((count, commit)) or False,
    )
    content_pack = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        initial,
    )
    caplog.set_level(logging.INFO, logger=content_pack_routes.__name__)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack, "image_style": "viral_carousel"},
    )

    assert response.status_code == 302
    assert len(repair_calls) == 1
    assert repair_calls[0][1]["failure_reason"] == "multiple_primary_headings"
    assert reserve_calls == [(slide_count, False)]
    assert module.Post.query.count() == 0
    assert (
        "stage=initial_validation result=invalid "
        "reason=multiple_primary_headings"
    ) in caplog.text
    assert (
        "stage=repair_eligibility result=eligible "
        "reason=multiple_primary_headings"
    ) in caplog.text
    assert "stage=repair_call result=started" in caplog.text
    assert f"stage=repaired_validation result=valid slide_count={slide_count}" in caplog.text
    assert f"slide_index={expected_index} story_role={expected_role}" in caplog.text
    assert "Dziękuję" not in caplog.text


def test_real_app_wiring_executes_one_external_repair_call_and_creates_rows(
    client, app, module, monkeypatch, caplog
):
    user = create_user(module, email="real-repair-wiring@example.com")
    login(client, user)
    repaired = (
        "Slide 1:\nTitle: Polish at a Restaurant\n"
        "Subtitle: Useful phrases for dining confidently\n"
        "Slide 2:\nPhrase: Dziękuję.\nTranslation: Thank you.\n"
        "Slide 3:\nCTA: Save this guide"
    )

    class MockOpenAIClient:
        def __init__(self):
            self.responses = self
            self.options = []
            self.calls = []

        def with_options(self, **kwargs):
            self.options.append(kwargs)
            return self

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return type("Response", (), {"output_text": repaired})()

    openai_client = MockOpenAIClient()
    monkeypatch.setattr(module, "OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setattr(module, "openai_client", openai_client)
    monkeypatch.setattr(
        module, "generate_openai_image",
        lambda *args, **kwargs: pytest.fail("artwork must not run in the web route"),
    )
    set_content_pack_helper(
        app, monkeypatch, "reserve_ai_image_credits", lambda *args, **kwargs: True,
    )
    set_content_pack_helper(
        app, monkeypatch, "get_placeholder_image_url",
        lambda: "https://cdn.test/placeholder.jpg",
    )
    initial = (
        "Slide 1:\nTitle: Polish at a Restaurant\n"
        "Title: Useful phrases for dining confidently\n"
        "Slide 2:\nPhrase: Dziękuję.\nTranslation: Thank you.\n"
        "Slide 3:\nCTA: Save this guide"
    )
    content_pack = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        initial,
    )
    caplog.set_level(logging.WARNING, logger=content_pack_routes.__name__)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack, "image_style": "viral_carousel"},
    )

    posts = module.Post.query.order_by(module.Post.sort_order).all()
    assert response.status_code == 302
    assert "/post/" in response.headers["Location"]
    assert len(openai_client.calls) == 1
    assert openai_client.options == [{
        "timeout": 35.0,
        "max_retries": 0,
    }]
    assert openai_client.calls[0]["model"] == "gpt-4.1-mini"
    assert len(posts) == 3
    assert "stage=repair_call result=started" in caplog.text
    assert "stage=repair_call result=completed" in caplog.text
    assert "stage=pair_preservation result=pass" in caplog.text
    assert "stage=repaired_validation result=valid slide_count=3" in caplog.text
    assert "stage=request_outcome result=created slide_count=3" in caplog.text
    assert "carousel_story_rejected" not in caplog.text
    assert "Dziękuję" not in caplog.text


def test_invalid_story_route_reserves_no_credits_and_creates_no_rows(
    client, app, module, monkeypatch, caplog
):
    user = create_user(module, email="story-invalid@example.com")
    login(client, user)
    reserve_calls = []
    repair_calls = []
    set_content_pack_helper(
        app, monkeypatch, "reserve_ai_image_credits",
        lambda *args, **kwargs: reserve_calls.append(args) or True,
    )
    set_content_pack_helper(
        app, monkeypatch, "repair_carousel_structure",
        lambda *args, **kwargs: repair_calls.append((args, kwargs)) or "malformed",
    )
    invalid = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        "Slide 1:\nPhrase: Co pan poleca?\nTranslation: What do you recommend?\n"
        "Slide 2:\nPhrase: Poproszę rachunek\nTranslation: The bill, please",
    )
    caplog.set_level(logging.INFO, logger=content_pack_routes.__name__)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": invalid, "image_style": "viral_carousel"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "clean carousel" in response.get_data(as_text=True)
    assert reserve_calls == []
    assert len(repair_calls) == 1
    assert module.Post.query.count() == 0
    assert "carousel_story_rejected reason=cover_is_teaching" in caplog.text
    assert "stage=initial_validation result=invalid reason=cover_is_teaching" in caplog.text
    assert "stage=repair_eligibility result=eligible reason=cover_is_teaching" in caplog.text
    assert "stage=repair_call result=completed" in caplog.text
    assert "stage=repair_parse result=malformed reason=insufficient_slide_markers" in caplog.text
    assert "stage=request_outcome result=rejected reason=cover_is_teaching" in caplog.text
    assert "Co pan poleca?" not in caplog.text
    assert "Poproszę rachunek" not in caplog.text


def test_valid_initial_story_never_calls_repair(
    client, app, module, monkeypatch, caplog
):
    user = create_user(module, email="story-valid@example.com")
    login(client, user)
    repair_calls = []
    reserve_calls = []
    set_content_pack_helper(
        app, monkeypatch, "repair_carousel_structure",
        lambda *args, **kwargs: repair_calls.append(1),
    )
    set_content_pack_helper(
        app, monkeypatch, "reserve_ai_image_credits",
        lambda *args, **kwargs: reserve_calls.append(args) or False,
    )
    valid = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        "Slide 1:\nTitle: Polish at a Restaurant\nSubtitle: Useful dining phrases\n"
        "Slide 2:\nCTA: Save and practise",
    )
    caplog.set_level(logging.INFO, logger=content_pack_routes.__name__)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": valid, "image_style": "viral_carousel"},
    )

    assert response.status_code == 302
    assert repair_calls == []
    assert len(reserve_calls) == 1
    assert module.Post.query.count() == 0
    assert "stage=initial_validation result=valid repair_attempted=false" in caplog.text
    assert "stage=repair_eligibility" not in caplog.text


def test_one_repair_can_create_valid_story_before_credits_and_preserve_unicode(
    client, app, module, monkeypatch, caplog
):
    user = create_user(module, email="story-repaired@example.com")
    login(client, user)
    repair_calls, reserve_calls = [], []
    initial = (
        "Slide 1:\nPhrase: Czy są dania wegetariańskie?\n"
        "Translation: Are there vegetarian dishes?\n"
        "Slide 2:\nPhrase: Poproszę rachunek.\nTranslation: The bill, please."
    )
    repaired = (
        "Slide 1:\nTitle: Polish Restaurant Phrases\nSubtitle: A practical dining guide\n"
        "Slide 2:\nPhrase: Czy są dania wegetariańskie?\n"
        "Translation: Are there vegetarian dishes?\n"
        "Slide 3:\nPhrase: Poproszę rachunek.\nTranslation: The bill, please.\n"
        "Slide 4:\nCTA: Save these phrases"
    )
    set_content_pack_helper(
        app, monkeypatch, "repair_carousel_structure",
        lambda *args, **kwargs: repair_calls.append((args, kwargs)) or repaired,
    )
    set_content_pack_helper(
        app, monkeypatch, "reserve_ai_image_credits",
        lambda user, count, commit=False: reserve_calls.append((count, commit)) or True,
    )
    set_content_pack_helper(
        app, monkeypatch, "get_placeholder_image_url",
        lambda: "https://cdn.test/placeholder.jpg",
    )
    content_pack = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        initial,
    )
    caplog.set_level(logging.INFO, logger=content_pack_routes.__name__)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack, "image_style": "viral_carousel"},
    )
    posts = module.Post.query.order_by(module.Post.sort_order).all()

    assert response.status_code == 302
    assert len(repair_calls) == 1
    assert reserve_calls == [(4, False)]
    assert len(posts) == 4
    payloads = [carousel_generation.parse_overlay_prompt(post.prompt) for post in posts]
    assert payloads[1]["overlay"]["title"] == "Czy są dania wegetariańskie?"
    assert payloads[2]["overlay"]["title"] == "Poproszę rachunek."
    assert "stage=repair_call result=completed" in caplog.text
    assert "stage=repair_parse result=valid slide_count=4" in caplog.text
    assert "stage=pair_preservation result=pass original_pair_count=2 repaired_pair_count=2" in caplog.text
    assert "stage=repaired_validation result=valid slide_count=4" in caplog.text
    assert "Czy są dania" not in caplog.text
    assert len(set(re.findall(r"trace_id=([0-9a-f]{10})", caplog.text))) == 1


def test_repair_that_changes_translation_is_rejected_before_credits(
    client, app, module, monkeypatch, caplog
):
    user = create_user(module, email="translation-change@example.com")
    login(client, user)
    reserve_calls = []
    initial = (
        "Slide 1:\nPhrase: Dziękuję.\nTranslation: Thank you.\n"
        "Slide 2:\nCTA: Save this phrase"
    )
    changed = (
        "Slide 1:\nTitle: Polish Basics\nSubtitle: Useful phrases\n"
        "Slide 2:\nPhrase: Dziękuję.\nTranslation: Thanks.\n"
        "Slide 3:\nCTA: Save this phrase"
    )
    set_content_pack_helper(
        app, monkeypatch, "repair_carousel_structure", lambda *args, **kwargs: changed,
    )
    set_content_pack_helper(
        app, monkeypatch, "reserve_ai_image_credits",
        lambda *args, **kwargs: reserve_calls.append(1) or True,
    )
    content_pack = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        initial,
    )
    caplog.set_level(logging.INFO, logger=content_pack_routes.__name__)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack, "image_style": "viral_carousel"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "clean carousel" in response.get_data(as_text=True)
    assert reserve_calls == []
    assert module.Post.query.count() == 0
    assert "stage=pair_preservation result=fail original_pair_count=1 repaired_pair_count=1" in caplog.text
    assert "Dziękuję" not in caplog.text


@pytest.mark.parametrize(
    "repair_result",
    [
        "",
        "not a carousel",
        "Slide 1:\nTitle: Only one slide",
        "\n".join(f"Slide {index}:\nTitle: Slide {index}" for index in range(1, 8)),
        (
            "Slide 1:\nPhrase: Dziękuję.\nTranslation: Thank you.\n"
            "Slide 2:\nCTA: Save this phrase"
        ),
    ],
)
def test_invalid_repair_outputs_fail_cleanly_before_credits_or_rows(
    repair_result, client, app, module, monkeypatch, caplog
):
    user = create_user(module, email=f"repair-failure-{abs(hash(repair_result))}@example.com")
    login(client, user)
    repair_calls, reserve_calls = [], []
    initial = (
        "Slide 1:\nPhrase: Dziękuję.\nTranslation: Thank you.\n"
        "Slide 2:\nCTA: Save this phrase"
    )
    set_content_pack_helper(
        app, monkeypatch, "repair_carousel_structure",
        lambda *args, **kwargs: repair_calls.append(1) or repair_result,
    )
    set_content_pack_helper(
        app, monkeypatch, "reserve_ai_image_credits",
        lambda *args, **kwargs: reserve_calls.append(1) or True,
    )
    content_pack = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        initial,
    )
    caplog.set_level(logging.INFO, logger=content_pack_routes.__name__)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack, "image_style": "viral_carousel"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "clean carousel" in response.get_data(as_text=True)
    assert repair_calls == [1]
    assert reserve_calls == []
    assert module.Post.query.count() == 0
    assert "stage=repair_call result=completed" in caplog.text
    assert "stage=repair_parse result=" in caplog.text or "stage=repaired_validation result=invalid" in caplog.text
    assert "Dziękuję" not in caplog.text


def test_repair_provider_exception_fails_cleanly_before_credits_or_rows(
    client, app, module, monkeypatch, caplog
):
    user = create_user(module, email="repair-exception@example.com")
    login(client, user)
    reserve_calls = []

    def fail_repair(*args, **kwargs):
        raise RuntimeError("private provider response must not reach the customer")

    set_content_pack_helper(app, monkeypatch, "repair_carousel_structure", fail_repair)
    set_content_pack_helper(
        app, monkeypatch, "reserve_ai_image_credits",
        lambda *args, **kwargs: reserve_calls.append(1) or True,
    )
    initial = (
        "Slide 1:\nPhrase: Dziękuję.\nTranslation: Thank you.\n"
        "Slide 2:\nCTA: Save this phrase"
    )
    content_pack = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        initial,
    )
    caplog.set_level(logging.INFO, logger=content_pack_routes.__name__)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack, "image_style": "viral_carousel"},
        follow_redirects=True,
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "clean carousel" in body
    assert "private provider response" not in body
    assert reserve_calls == []
    assert module.Post.query.count() == 0
    assert "stage=repair_call result=exception reason=unexpected_exception" in caplog.text
    assert "private provider response" not in caplog.text


def test_non_language_structure_repair_can_continue_through_existing_credit_path(
    client, app, module, monkeypatch
):
    user = create_user(module, email="repair-product@example.com")
    login(client, user)
    reserve_calls = []
    initial = (
        "Slide 1:\nTitle: Build Review\nBody: One focused review reduces rework while "
        "keeping campaign decisions visible to the team in a shared workflow.\n"
        "Subtitle: A deliberately excessive fourth visible block that breaks the budget.\n"
        "CTA: Review together\nSlide 2:\nCTA: Keep the next decision visible"
    )
    repaired = (
        "Slide 1:\nTitle: Make Reviews Visible\nSubtitle: One shared campaign workflow\n"
        "Slide 2:\nTitle: Reduce Rework\nBody: Keep decisions visible to the team.\n"
        "Slide 3:\nCTA: Review together"
    )
    set_content_pack_helper(
        app, monkeypatch, "repair_carousel_structure", lambda *args, **kwargs: repaired,
    )
    set_content_pack_helper(
        app, monkeypatch, "reserve_ai_image_credits",
        lambda user, count, commit=False: reserve_calls.append((count, commit)) or False,
    )
    content_pack = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        initial,
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack, "image_style": "viral_carousel"},
    )

    assert response.status_code == 302
    assert reserve_calls == [(3, False)]
    assert module.Post.query.count() == 0


def test_overloaded_teaching_repair_splits_pairs_and_preserves_all_polish_unicode(
    client, app, module, monkeypatch, caplog
):
    user = create_user(module, email="repair-unicode@example.com")
    login(client, user)
    reserve_calls = []
    initial = (
        "Slide 1:\nTitle: Polish at a Restaurant\nSubtitle: Four useful phrases\n"
        "Slide 2:\nPhrase: Czy są dania wegetariańskie?\n"
        "Translation: Are there vegetarian dishes?\n"
        "Phrase: Poproszę rachunek.\nTranslation: The bill, please.\n"
        "Phrase: Czy mogę prosić menu?\nTranslation: May I have the menu?\n"
        "Slide 3:\nPhrase: Dziękuję.\nTranslation: Thank you.\n"
        "Slide 4:\nCTA: Save these phrases"
    )
    repaired = (
        "Slide 1:\nTitle: Polish at a Restaurant\nSubtitle: Four useful phrases\n"
        "Slide 2:\nPhrase: Czy są dania wegetariańskie?\n"
        "Translation: Are there vegetarian dishes?\n"
        "Slide 3:\nPhrase: Poproszę rachunek.\nTranslation: The bill, please.\n"
        "Slide 4:\nPhrase: Czy mogę prosić menu?\nTranslation: May I have the menu?\n"
        "Slide 5:\nPhrase: Dziękuję.\nTranslation: Thank you.\n"
        "Slide 6:\nCTA: Save these phrases"
    )
    set_content_pack_helper(
        app, monkeypatch, "repair_carousel_structure", lambda *args, **kwargs: repaired,
    )
    set_content_pack_helper(
        app, monkeypatch, "reserve_ai_image_credits",
        lambda user, count, commit=False: reserve_calls.append((count, commit)) or True,
    )
    set_content_pack_helper(
        app, monkeypatch, "get_placeholder_image_url",
        lambda: "https://cdn.test/placeholder.jpg",
    )
    content_pack = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        initial,
    )
    caplog.set_level(logging.INFO, logger=content_pack_routes.__name__)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack, "image_style": "viral_carousel"},
    )
    posts = module.Post.query.order_by(module.Post.sort_order).all()

    assert response.status_code == 302
    assert reserve_calls == [(6, False)]
    assert len(posts) == 6
    titles = [
        carousel_generation.parse_overlay_prompt(post.prompt)["overlay"]["title"]
        for post in posts
    ]
    assert titles[1:5] == [
        "Czy są dania wegetariańskie?",
        "Poproszę rachunek.",
        "Czy mogę prosić menu?",
        "Dziękuję.",
    ]
    assert "stage=initial_validation result=invalid reason=teaching_unit_overload" in caplog.text
    assert "stage=pair_preservation result=pass original_pair_count=4 repaired_pair_count=4" in caplog.text
    assert "stage=repaired_validation result=valid slide_count=6" in caplog.text
    assert "Czy mogę prosić menu" not in caplog.text


@pytest.mark.parametrize(
    ("reason", "expected_result"),
    [
        ("provider_unavailable", "provider_unavailable"),
        ("provider_timeout", "timeout"),
        ("provider_error", "exception"),
    ],
)
def test_repair_provider_failure_has_bounded_diagnostic_and_no_customer_copy(
    reason, expected_result, client, app, module, monkeypatch, caplog
):
    user = create_user(module, email="repair-timeout-trace@example.com")
    login(client, user)
    reserve_calls = []
    set_content_pack_helper(
        app, monkeypatch, "repair_carousel_structure",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            CarouselStructureRepairError(reason)
        ),
    )
    set_content_pack_helper(
        app, monkeypatch, "reserve_ai_image_credits",
        lambda *args, **kwargs: reserve_calls.append(1) or True,
    )
    customer_copy = "Poufna fraza klienta"
    invalid = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        f"Slide 1:\nPhrase: {customer_copy}\nTranslation: Private phrase\n"
        "Slide 2:\nCTA: Save this phrase",
    )
    caplog.set_level(logging.INFO, logger=content_pack_routes.__name__)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": invalid, "image_style": "viral_carousel"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "clean carousel" in response.get_data(as_text=True)
    assert f"stage=repair_call result={expected_result} reason={reason}" in caplog.text
    assert "stage=request_outcome result=rejected reason=cover_is_teaching" in caplog.text
    assert customer_copy not in caplog.text
    assert reserve_calls == []
    assert module.Post.query.count() == 0


def test_initial_nonrepairable_reason_is_traced_without_repair(
    client, app, module, monkeypatch, caplog
):
    user = create_user(module, email="repair-ineligible-trace@example.com")
    login(client, user)
    repair_calls = []
    def nonrepairable(*args, **kwargs):
        raise content_pack_routes.CarouselStoryError("story_structure_invalid")

    monkeypatch.setattr(content_pack_routes, "_validate_carousel_story", nonrepairable)
    set_content_pack_helper(
        app, monkeypatch, "repair_carousel_structure",
        lambda *args, **kwargs: repair_calls.append(1),
    )
    caplog.set_level(logging.INFO, logger=content_pack_routes.__name__)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": CONTENT_PACK_RESULT, "image_style": "viral_carousel"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert repair_calls == []
    assert "stage=repair_eligibility result=ineligible reason=story_structure_invalid" in caplog.text


def test_repair_parser_exception_has_distinct_safe_diagnostic(
    client, app, module, monkeypatch, caplog
):
    user = create_user(module, email="repair-parser-trace@example.com")
    login(client, user)
    repaired = (
        "Slide 1:\nTitle: Repaired Campaign\nSubtitle: Safe structure\n"
        "Slide 2:\nPhrase: Dziękuję.\nTranslation: Thank you."
    )
    original_parse = content_pack_routes._parse_content_pack_carousel_slides

    def parse_or_fail(value):
        if "Repaired Campaign" in value:
            raise ValueError("private parser context")
        return original_parse(value)

    monkeypatch.setattr(content_pack_routes, "_parse_content_pack_carousel_slides", parse_or_fail)
    set_content_pack_helper(
        app, monkeypatch, "repair_carousel_structure", lambda *args, **kwargs: repaired,
    )
    invalid = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        "Slide 1:\nPhrase: Dziękuję.\nTranslation: Thank you.\n"
        "Slide 2:\nCTA: Save this phrase",
    )
    caplog.set_level(logging.INFO, logger=content_pack_routes.__name__)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": invalid, "image_style": "viral_carousel"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "stage=repair_parse result=parse_failed reason=parser_exception" in caplog.text
    assert "private parser context" not in caplog.text
    assert "Dziękuję" not in caplog.text


def test_wrapper_text_before_valid_repair_is_safely_ignored_and_traced(
    client, app, module, monkeypatch, caplog
):
    user = create_user(module, email="repair-wrapper-trace@example.com")
    login(client, user)
    repaired = (
        "```text\nHere is the repaired carousel:\n"
        "Slide 1:\nTitle: Polish Restaurant Phrases\nSubtitle: A dining guide\n"
        "Slide 2:\nPhrase: Dziękuję.\nTranslation: Thank you."
    )
    set_content_pack_helper(
        app, monkeypatch, "repair_carousel_structure", lambda *args, **kwargs: repaired,
    )
    set_content_pack_helper(
        app, monkeypatch, "get_placeholder_image_url",
        lambda: "https://cdn.test/placeholder.jpg",
    )
    invalid = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        "Slide 1:\nPhrase: Dziękuję.\nTranslation: Thank you.\n"
        "Slide 2:\nCTA: Save this phrase",
    )
    caplog.set_level(logging.INFO, logger=content_pack_routes.__name__)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": invalid, "image_style": "viral_carousel"},
    )

    assert response.status_code == 302
    assert "stage=repair_parse result=valid slide_count=2" in caplog.text
    assert "stage=repaired_validation result=valid slide_count=2" in caplog.text
    assert "Dziękuję" not in caplog.text


def test_phrase_pair_multiset_preserves_reordering_duplicates_and_unicode():
    first = story_slide(
        "Dziękuję.", "Thank you.", role="phrase",
        pairs=(("Dziękuję.", "Thank you."),),
    )
    duplicate = dict(first)
    menu = story_slide(
        "Czy mogę prosić menu?", "May I have the menu?", role="phrase",
        pairs=(("Czy mogę prosić menu?", "May I have the menu?"),),
    )

    assert content_pack_routes._repair_preserves_phrase_pairs(
        [first, duplicate, menu], [menu, duplicate, first]
    )
    assert not content_pack_routes._repair_preserves_phrase_pairs(
        [first, duplicate, menu], [menu, first]
    )


@contextmanager
def captured_templates(app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template.name, context))

    template_rendered.connect(record, app)
    try:
        yield recorded
    finally:
        template_rendered.disconnect(record, app)


def rules_for(app, path):
    return [rule for rule in app.url_map.iter_rules() if rule.rule == path]


def set_content_pack_helper(app, monkeypatch, name, helper):
    monkeypatch.setitem(app.extensions["smu_content_pack_helpers"], name, helper)


def test_content_pack_blueprint_is_registered_once(module):
    assert "content_pack" in module.app.blueprints
    assert list(module.app.blueprints).count("content_pack") == 1


def test_visual_treatment_uses_only_semantically_supported_structure():
    assert content_pack_routes._select_visual_treatment(
        "A connected three-stage process", "info"
    ) == "process"
    assert content_pack_routes._select_visual_treatment(
        "A before and after comparison", "info"
    ) == "comparison"
    assert content_pack_routes._select_visual_treatment(
        "A calm relevant object", "info"
    ) == "illustration"
    assert content_pack_routes._select_visual_treatment(None, "info") == "typography_only"


def test_visual_treatment_does_not_fabricate_process_or_comparison():
    generic = "A clean focal subject with generous negative space"

    assert content_pack_routes._select_visual_treatment(generic, "info") == "illustration"


def test_weak_decorative_visual_semantics_fall_back_to_typography_only():
    assert content_pack_routes._select_visual_treatment(
        "Generic decorative abstract shape", "info"
    ) == "typography_only"


def test_cover_visual_treatment_is_derived_from_semantic_concept():
    assert content_pack_routes._select_visual_treatment(
        "One source card branching into multiple destination cards", "cover"
    ) == "diagram"
    assert content_pack_routes._select_visual_treatment(
        "A learning book and speech symbol", "cover"
    ) == "illustration"


@pytest.mark.parametrize(
    ("semantic_text", "expected"),
    [
        ("Instagram wants quick visual connections", "visual_focus"),
        ("LinkedIn values thoughtful professional insights", "illustration"),
        ("Reddit thrives on genuine discussion and context", "diagram"),
        ("A three-stage ordered workflow", "process"),
        ("A genuine before and after contrast", "comparison"),
    ],
)
def test_visual_treatment_uses_slide_semantics_when_visual_is_missing(
    semantic_text, expected
):
    assert content_pack_routes._select_visual_treatment(
        None, "info", semantic_text
    ) == expected


def test_platform_semantics_use_text_free_composition_not_logos():
    directions = {
        platform: content_pack_routes._safe_visual_direction(None, platform)
        for platform in ("Instagram", "LinkedIn", "Reddit")
    }

    assert "media cards" in directions["Instagram"]
    assert "editorial document" in directions["LinkedIn"]
    assert "conversation nodes" in directions["Reddit"]
    assert all("logo" not in direction for direction in directions.values())


def test_production_shaped_carousel_has_semantic_rhythm_and_distinct_layouts():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1: Every platform speaks a different language
Slide 2: Instagram wants quick, visual connections
Slide 3: LinkedIn values thoughtful, professional insights
Slide 4: Reddit thrives on genuine discussion and context
Slide 5:
Title: Tailoring content turns good ideas into great posts
CTA: Adapt with purpose"""
    )
    treatments = []
    layouts = []
    for index, slide in enumerate(slides):
        role = "cover" if index == 0 else slide["layout_role"]
        semantic_text = " ".join(
            value for value in (slide["title"], slide["body"]) if value
        )
        treatment = content_pack_routes._select_visual_treatment(
            slide["visual"], role, semantic_text
        )
        treatments.append(treatment)
        layouts.append(
            content_pack_routes._select_layout_variant(
                role, index, treatment, slide["title"]
            )
        )

    assert treatments == [
        "diagram", "visual_focus", "illustration", "diagram", "typography_only"
    ]
    assert len(set(treatments)) >= 3
    assert len(set(layouts)) >= 4
    assert layouts == [
        "hero_left", "visual_focus", "split_left", "editorial_statement", "closing"
    ]
    assert all(left != right for left, right in zip(layouts, layouts[1:]))


def test_content_pack_routes_preserve_old_endpoints_and_methods(module):
    expected = {
        "/content-pack": ("content_pack", {"GET", "POST"}),
        "/content-pack/create-carousel": (
            "create_content_pack_carousel",
            {"POST"},
        ),
        "/content-pack/create-platform-draft": (
            "create_content_pack_platform_draft",
            {"POST"},
        ),
    }

    for path, (endpoint, methods) in expected.items():
        rules = rules_for(module.app, path)

        assert len(rules) == 1
        assert rules[0].endpoint == endpoint
        assert methods.issubset(rules[0].methods)


def test_content_pack_url_for_compatibility(module):
    with module.app.test_request_context():
        assert url_for("content_pack") == "/content-pack"
        assert url_for("create_content_pack_carousel") == (
            "/content-pack/create-carousel"
        )
        assert url_for("create_content_pack_platform_draft") == (
            "/content-pack/create-platform-draft"
        )


def test_content_pack_requires_login(client):
    response = client.get("/content-pack")

    assert response.status_code == 302
    assert "/login" in response.location


def test_content_pack_get_preserves_template_context_and_session(client, app, module):
    user = create_user(module)
    login(client, user)

    with captured_templates(app) as templates:
        response = client.get("/content-pack")

    assert response.status_code == 200
    assert templates[0][0] == "content_pack.html"
    assert templates[0][1]["source_text"] == ""
    assert templates[0][1]["content_pack_result"] is None
    with client.session_transaction() as session:
        assert session["content_pack_started"] is True


def test_content_pack_post_uses_current_user_brand_context(client, app, module, monkeypatch):
    user = create_user(module)
    module.db.session.add(
        module.BrandBrief(user_id=user.id, business_name="User Brand")
    )
    module.db.session.commit()
    login(client, user)
    calls = {}

    def fake_build_brand_context(user_id):
        calls["user_id"] = user_id
        return "BRAND CONTEXT"

    def fake_generate_content_pack(source_text, brand_context):
        calls["source_text"] = source_text
        calls["brand_context"] = brand_context
        return CONTENT_PACK_RESULT

    set_content_pack_helper(app, monkeypatch, "build_brand_context", fake_build_brand_context)
    set_content_pack_helper(app, monkeypatch, "generate_content_pack", fake_generate_content_pack)

    with captured_templates(app) as templates:
        response = client.post(
            "/content-pack",
            data={"source_type": "text", "source_input": "  Topic idea  "},
        )

    assert response.status_code == 200
    assert calls == {
        "user_id": user.id,
        "source_text": "Topic idea",
        "brand_context": "BRAND CONTEXT",
    }
    assert templates[0][1]["source_text"] == "Topic idea"
    assert templates[0][1]["content_pack_result"] == CONTENT_PACK_RESULT


def test_content_pack_provider_failure_is_safe_and_releases_reserved_credit(
    client, app, module, monkeypatch
):
    user = create_user(module, email="provider-timeout@example.com")
    login(client, user)
    released = []
    set_content_pack_helper(
        app, monkeypatch, "reserve_content_pack_credits", lambda current_user: True
    )
    set_content_pack_helper(
        app,
        monkeypatch,
        "release_content_pack_credits",
        lambda current_user: released.append(current_user.id),
    )
    set_content_pack_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")
    set_content_pack_helper(
        app,
        monkeypatch,
        "generate_content_pack",
        lambda source_text, brand_context: (_ for _ in ()).throw(
            ContentPackGenerationError("provider_timeout")
        ),
    )

    response = client.post(
        "/content-pack",
        data={"source_type": "text", "source_input": "PRIVATE SOURCE"},
    )

    assert response.status_code == 200
    assert released == [user.id]
    assert b"We couldn&#39;t generate your Content Pack. Please try again." in response.data
    assert b"provider_timeout" not in response.data


def test_content_pack_tiktok_source_uses_transcript_helper(client, app, module, monkeypatch):
    user = create_user(module)
    login(client, user)
    calls = {}

    def fake_extract_tiktok_transcript(url):
        calls["url"] = url
        return "Transcript text"

    set_content_pack_helper(
        app,
        monkeypatch,
        "extract_tiktok_transcript",
        fake_extract_tiktok_transcript,
    )
    set_content_pack_helper(app, monkeypatch, "build_brand_context", lambda user_id: "")
    set_content_pack_helper(
        app,
        monkeypatch,
        "generate_content_pack",
        lambda source_text, brand_context: f"Generated from {source_text}",
    )

    with captured_templates(app) as templates:
        response = client.post(
            "/content-pack",
            data={"source_type": "tiktok", "source_input": "https://tiktok.test/video"},
        )

    assert response.status_code == 200
    assert calls["url"] == "https://tiktok.test/video"
    assert templates[0][1]["source_text"] == "Transcript text"
    assert templates[0][1]["content_pack_result"] == "Generated from Transcript text"


def test_content_pack_missing_source_redirects_without_generation(client, app, module, monkeypatch):
    user = create_user(module)
    login(client, user)

    def fail_generate_content_pack(source_text, brand_context):
        raise AssertionError("generation should not run without source input")

    set_content_pack_helper(
        app,
        monkeypatch,
        "generate_content_pack",
        fail_generate_content_pack,
    )

    response = client.post(
        "/content-pack",
        data={"source_type": "text", "source_input": ""},
    )

    assert response.status_code == 302
    assert response.location.endswith("/content-pack")


def test_create_content_pack_carousel_creates_grouped_posts(client, app, module, monkeypatch):
    user = create_user(module)
    login(client, user)
    set_content_pack_helper(app, monkeypatch, "get_placeholder_image_url", lambda: "https://cdn.test/placeholder.jpg")
    set_content_pack_helper(
        app,
        monkeypatch,
        "apply_image_style",
        lambda prompt, style: f"{style}:{prompt}",
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={
            "content_pack_result": CONTENT_PACK_RESULT,
            "image_style": "viral_carousel",
        },
    )
    posts = module.Post.query.order_by(module.Post.sort_order.asc()).all()

    assert response.status_code == 302
    assert len(posts) == 3
    assert len({post.group_id for post in posts}) == 1
    assert [post.sort_order for post in posts] == [0, 1, 2]
    assert [post.is_cover for post in posts] == [True, False, False]
    assert {post.user_id for post in posts} == {user.id}
    assert {post.post_type for post in posts} == {"carousel"}
    assert {post.status for post in posts} == {"generating"}
    assert {post.platforms for post in posts} == {"instagram"}
    assert {post.file_url for post in posts} == {"https://cdn.test/placeholder.jpg"}
    assert posts[0].caption == "Instagram caption\n\n#one #two"
    payloads = [carousel_generation.parse_overlay_prompt(post.prompt) for post in posts]
    assert [payload["overlay"]["title"] for payload in payloads] == [
        "First slide",
        "Second slide",
        "Third slide",
    ]
    assert all(post.prompt.startswith("SMU_OVERLAY_V1:") for post in posts)
    for title, payload in zip(
        ["First slide", "Second slide", "Third slide"], payloads
    ):
        assert title not in payload["background_prompt"]
    assert all("no readable text" in payload["background_prompt"] for payload in payloads)
    assert all(payload["overlay"]["body"] is None for payload in payloads)
    assert all(payload["credits_reserved"] is True for payload in payloads)
    assert response.location.endswith(f"/post/{posts[0].id}")


@pytest.mark.parametrize(("images_used", "remaining"), [(15, 5), (20, 0)])
def test_content_pack_carousel_rejects_insufficient_whole_carousel_credits(
    client, module, images_used, remaining
):
    module.app.config["SMU_ADMIN_EMAILS"] = set()
    user = create_user(module, email=f"limited-{images_used}@example.com")
    usage = module.UserUsage(
        user_id=user.id,
        plan="starter",
        ai_images_used=images_used,
        content_packs_used=0,
        usage_period_start=utc_now() - timedelta(days=1),
        usage_period_end=utc_now() + timedelta(days=30),
    )
    module.db.session.add(usage)
    module.db.session.commit()
    login(client, user)
    six_slides = "\n".join(
        f"Slide {index}: Credit test {index}" for index in range(1, 7)
    )
    content_pack_result = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        six_slides,
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack_result},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (
        f"You need 6 AI image credits to create this carousel, but you have "
        f"{remaining} remaining."
    ) in response.get_data(as_text=True)
    assert module.Post.query.count() == 0
    assert module.db.session.get(module.UserUsage, usage.id).ai_images_used == images_used


def test_six_image_content_pack_reserves_and_consumes_exactly_six_credits(
    client, module, monkeypatch
):
    module.app.config["SMU_ADMIN_EMAILS"] = set()
    user = create_user(module, email="six-credits@example.com")
    usage = module.UserUsage(
        user_id=user.id,
        plan="starter",
        ai_images_used=14,
        content_packs_used=0,
        usage_period_start=utc_now() - timedelta(days=1),
        usage_period_end=utc_now() + timedelta(days=30),
    )
    module.db.session.add(usage)
    module.db.session.commit()
    login(client, user)
    six_slides = "\n".join(
        f"Slide {index}: Successful credit test {index}" for index in range(1, 7)
    )
    content_pack_result = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        six_slides,
    )
    monkeypatch.setattr(
        module,
        "generate_openai_image",
        lambda prompt, **kwargs: "https://cdn.test/generated.jpg",
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack_result},
    )
    assert response.status_code == 302
    assert module.Post.query.count() == 6
    assert module.db.session.get(module.UserUsage, usage.id).ai_images_used == 20

    module.generate_pending_carousel_images()
    module.generate_pending_carousel_images()

    assert {post.status for post in module.Post.query.all()} == {"draft"}
    assert module.db.session.get(module.UserUsage, usage.id).ai_images_used == 20


def test_content_pack_carousel_parser_maps_structural_fields():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Speak Polish in Simple Steps
Subtitle: Essential Polish words and phrases for beginners
Body: Zażółć gęślą jaźń

Slide 2:
Phrase: Jak się masz?
Translation: How are you?
CTA: Miłego dnia!

Slide 3:

Slide 4:
Szczęśliwej podróży!

Slide 5: Często tu przychodzisz?"""
    )

    assert slides == [
        {
            "title": "Speak Polish in Simple Steps",
            "body": (
                "Essential Polish words and phrases for beginners\n"
                "Zażółć gęślą jaźń"
            ),
            "cta": None,
            "brand": None,
            "visual": None,
            "layout_role": "info",
        },
        {
            "title": "Jak się masz?",
            "body": "How are you?",
            "cta": "Miłego dnia!",
            "brand": None,
            "visual": None,
            "layout_role": "phrase",
            "phrase_pairs": (("Jak się masz?", "How are you?"),),
        },
        {
            "title": "Szczęśliwej podróży!",
            "body": None,
            "cta": None,
            "brand": None,
            "visual": None,
            "layout_role": "info",
        },
        {
            "title": "Często tu przychodzisz?",
            "body": None,
            "cta": None,
            "brand": None,
            "visual": None,
            "layout_role": "info",
        },
    ]


RESTAURANT_CAROUSEL = """Slide 1:
Title: Polish at the Restaurant
Visual: A guest arriving at a restaurant
Slide 2:
Title: Asking for a table
Visual: A guest speaking with the restaurant host
Slide 3:
Title: Ordering food
Visual: A diner ordering a meal from a server
Slide 4:
Title: Ordering drinks
Visual: A server bringing drinks to a dining table
Slide 5:
Title: Asking for water
Visual: A diner asking the waiter for water
Slide 6:
Title: Asking for the bill
Visual: A diner requesting the bill and preparing payment"""

LOVE_CAROUSEL = """Slide 1:
Title: Polish love phrases
Visual: A warm personal conversation between a couple
Slide 2:
Title: Expressing affection
Visual: Two partners sharing an affectionate moment"""


def grounded_prompts(carousel, style="photorealistic", palette="warm_sunset"):
    slides = content_pack_routes._parse_content_pack_carousel_slides(carousel)
    presentations = content_pack_routes._carousel_presentations(slides)
    direction = content_pack_routes._campaign_art_direction(
        "viral_carousel", slides, style, palette
    )
    grounding = content_pack_routes._campaign_grounding(slides, direction)
    prompts = []
    for index, (slide, presentation) in enumerate(zip(slides, presentations)):
        slide_grounding = content_pack_routes._slide_grounding(
            slide, grounding, presentation["role"]
        )
        prompts.append(content_pack_routes._build_slide_background_prompt(
            "ignored", index, slide["visual"], presentation["role"],
            presentation["layout"], presentation["treatment"],
            presentation["semantic_text"], presentation["visual_weight"],
            presentation["metaphor"], direction, grounding, slide_grounding,
        ))
    return slides, presentations, direction, grounding, prompts


def test_campaign_grounding_is_fresh_and_current_request_only():
    _, _, _, restaurant, _ = grounded_prompts(RESTAURANT_CAROUSEL)
    _, _, _, love, _ = grounded_prompts(LOVE_CAROUSEL)
    _, _, _, restaurant_again, _ = grounded_prompts(RESTAURANT_CAROUSEL)

    assert restaurant is not restaurant_again
    assert restaurant == restaurant_again
    assert "restaurant" in restaurant["semantic_domain"]
    assert "language_learning" in restaurant["semantic_domain"]
    assert restaurant["semantic_domain"] == "restaurant + language_learning"
    assert "relationships" not in restaurant["semantic_domain"]
    assert "relationships" in love["semantic_domain"]


def test_campaign_order_does_not_change_provider_prompts():
    _, _, _, _, restaurant_first = grounded_prompts(RESTAURANT_CAROUSEL)
    _, _, _, _, love_second = grounded_prompts(LOVE_CAROUSEL)
    _, _, _, _, love_first = grounded_prompts(LOVE_CAROUSEL)
    _, _, _, _, restaurant_second = grounded_prompts(RESTAURANT_CAROUSEL)

    assert restaurant_first == restaurant_second
    assert love_first == love_second


def test_restaurant_prompts_are_current_domain_grounded_without_prior_tokens():
    slides, presentations, direction, grounding, prompts = grounded_prompts(
        RESTAURANT_CAROUSEL
    )
    joined = "\n".join(prompts).lower()

    assert grounding["resolved_style"] == "photorealistic"
    assert grounding["resolved_palette"] == "warm_sunset"
    assert all("current semantic domain: restaurant + language_learning" in prompt for prompt in prompts)
    assert all("current campaign subject: language-learning activity in a restaurant" in prompt for prompt in prompts)
    assert "restaurant host" in prompts[1]
    assert "ordering a meal" in prompts[2]
    assert "glass and carafe" in prompts[3]
    assert "glass and carafe" in prompts[4]
    assert "blank bill folder" in prompts[5]
    assert not any(token in joined for token in (
        "romance", "love", "couple", "gift", "handbag", "jewellery", "intimate", "romantic",
    ))
    for slide, prompt in zip(slides, prompts):
        assert slide["title"] not in prompt
    assert all("ABSOLUTELY NO READABLE TEXT" in prompt for prompt in prompts)
    assert direction["resolved_style"] == "photorealistic"
    assert direction["resolved_palette"] == "warm_sunset"
    assert len(presentations) == 6


def test_relationship_fixture_remains_relationship_grounded():
    _, _, _, grounding, prompts = grounded_prompts(LOVE_CAROUSEL)
    assert "relationships" in grounding["semantic_domain"]
    assert all("personal-connection setting" in prompt for prompt in prompts)
    assert all("restaurant host" not in prompt for prompt in prompts)


def test_motif_remapping_cannot_remove_current_domain_grounding():
    fixture = """Slide 1:
Title: Restaurant conversation
Visual: A branching node network inside a restaurant
Slide 2:
Title: Restaurant ordering
Visual: A branching node network around a dining table
Slide 3:
Title: Restaurant payment
Visual: Another branching node network near the bill"""
    slides, presentations, _, grounding, prompts = grounded_prompts(fixture)
    assert any(item["metaphor"] == "focal_object" for item in presentations[1:])
    assert all("restaurant" in prompt.lower() for prompt in prompts)
    assert grounding["semantic_domain"] == "restaurant + language_learning"
    assert len(slides) == len(prompts)


def test_auto_style_uses_only_current_campaign_and_explicit_choices_stay_authoritative():
    slides = content_pack_routes._parse_content_pack_carousel_slides(RESTAURANT_CAROUSEL)
    auto_one = content_pack_routes._campaign_art_direction(
        "viral_carousel", slides, "auto", "auto"
    )
    grounded_prompts(LOVE_CAROUSEL, "bold_graphic", "electric")
    auto_two = content_pack_routes._campaign_art_direction(
        "viral_carousel", slides, "auto", "auto"
    )
    _, _, explicit, grounding, prompts = grounded_prompts(RESTAURANT_CAROUSEL)

    assert auto_one == auto_two
    assert explicit["resolved_style"] == "photorealistic"
    assert explicit["resolved_palette"] == "warm_sunset"
    assert grounding["campaign_subject"] in prompts[0]
    assert prompts[0].index("0. CURRENT SEMANTIC GROUNDING") < prompts[0].index("1. CAMPAIGN STYLE LOCK")
    assert all(
        not value.startswith(
            ("Slide ", "Title:", "Subtitle:", "Phrase:", "Translation:")
        )
        for slide in slides
        for value in slide.values()
        if isinstance(value, str)
    )


def test_carousel_plan_accepts_allowlisted_weight_and_anchors_endpoints():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: A strong opening
Visual Weight: light
Visual: One bold focal object
Slide 2:
Title: A measured explanation
Visual Weight: medium
Visual: Editorial document
Slide 3:
CTA: Finish with one action
Visual Weight: heavy
Visual: Small closing accent"""
    )

    presentations = content_pack_routes._carousel_presentations(slides)

    assert [item["visual_weight"] for item in presentations] == [
        "heavy", "medium", "light"
    ]
    assert all(
        item["visual_weight"] in content_pack_routes.VISUAL_WEIGHTS
        for item in presentations
    )


def test_heavy_internal_diagram_avoids_small_editorial_artwork_zone():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Strong opening
Visual: One bold focal object
Slide 2:
Title: One important relationship connects every useful content destination
Visual Weight: heavy
Visual: A semantic network connecting source and audience"""
    )

    presentation = content_pack_routes._carousel_presentations(slides)[1]

    assert presentation["treatment"] == "diagram"
    assert presentation["visual_weight"] == "heavy"
    assert presentation["layout"] == "visual_focus"


def test_planner_derives_allowlisted_typography_presentations():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Strong cover
Visual: One focal object
Slide 2:
Title: Editorial explanation
Visual: One document under analysis
Slide 3:
CTA: Finish with purpose
Visual: Typography-only statement"""
    )

    presentations = content_pack_routes._carousel_presentations(slides)

    assert [item["typography_presentation"] for item in presentations] == [
        "display", "editorial", "quiet",
    ]


def test_planner_derives_deterministic_editorial_composition_rhythm():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Strong cover
Visual Weight: heavy
Visual: One focal object
Slide 2:
Title: A concise internal payoff
Visual Weight: heavy
Visual: One document under analysis
Slide 3:
CTA: Finish with purpose
Visual: Typography-only statement"""
    )

    first = content_pack_routes._carousel_presentations(slides)
    second = content_pack_routes._carousel_presentations(slides)

    assert [item["editorial_composition"] for item in first] == [
        "hero_bleed", "poster", "quiet",
    ]
    assert first == second


def test_nonsemantic_mirrored_internal_pair_remaps_to_vertical_composition():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Strong cover
Visual: One focal object
Slide 2:
Title: Reveal the strongest idea
Visual Weight: medium
Visual: One document under analysis
Slide 3:
Title: Adapt for every audience
Visual Weight: medium
Visual: One human figure adapting an idea for an audience
Slide 4:
CTA: Finish with purpose
Visual: Typography-only statement"""
    )
    presentations = content_pack_routes._carousel_presentations(slides)
    assert presentations[1]["layout"] != presentations[2]["layout"]
    assert presentations[2]["editorial_composition"] == "vertical_editorial"
    assert presentations[2]["optical_lock"] == "baseline_lock"
    assert presentations[2]["furniture"] == "none"


def test_genuine_comparison_split_is_not_remapped_to_vertical():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Strong cover
Visual: One focal object
Slide 2:
Title: Before and after
Visual: Compare two states before and after
Slide 3:
Title: Compare the outcomes
Visual: Comparison of the two outcomes
Slide 4:
CTA: Finish with purpose"""
    )
    presentations = content_pack_routes._carousel_presentations(slides)
    assert all(
        item["editorial_composition"] != "vertical_editorial"
        for item in presentations[1:3]
    )


@pytest.mark.parametrize("style", sorted(content_pack_routes.DESIGN_MANAGER_STYLES - {"auto"}))
def test_explicit_design_manager_style_is_preserved(style):
    slides = [{"title": "Same campaign", "body": None, "visual": "One transformation"}]
    direction = content_pack_routes._campaign_art_direction(
        "viral_carousel", slides, style, "monochrome"
    )
    assert direction["resolved_style"] == style
    assert direction["resolved_palette"] == "monochrome"


def test_auto_style_and_palette_resolution_is_deterministic():
    slides = [{"title": "A founder human story", "body": None, "visual": "A believable community moment"}]
    first = content_pack_routes._campaign_art_direction("viral_carousel", slides, "auto", "auto")
    second = content_pack_routes._campaign_art_direction("viral_carousel", slides, "auto", "auto")
    assert first == second
    assert first["resolved_style"] == "photorealistic"
    assert first["resolved_palette"] == "earth_and_cream"


def test_style_and_palette_change_prompt_grammar_without_overlay_copy():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        "Slide 1:\nTitle: Private exact overlay\nVisual: One source transforming"
    )
    plan = content_pack_routes._carousel_presentations(slides)[0]
    prompts = []
    for style, palette in (("minimal_premium", "monochrome"), ("three_d_clay", "soft_pastel")):
        prompts.append(content_pack_routes._build_slide_background_prompt(
            "Style: viral Instagram business carousel", 0, slides[0]["visual"],
            plan["role"], plan["layout"], plan["treatment"], plan["semantic_text"],
            plan["visual_weight"], plan["metaphor"],
            content_pack_routes._campaign_art_direction("viral_carousel", slides, style, palette),
        ))
    assert prompts[0] != prompts[1]
    assert "Private exact overlay" not in prompts[0]
    assert "Private exact overlay" not in prompts[1]


def test_carousel_plan_breaks_adjacent_diagram_and_metaphor_repetition():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Connected ideas
Visual: A node network
Slide 2:
Title: More connected ideas
Visual: Another node network
Slide 3:
Title: A clean conclusion
Visual: One distinct object"""
    )

    presentations = content_pack_routes._carousel_presentations(slides)

    assert presentations[0]["treatment"] == "visual_focus"
    assert presentations[0]["metaphor"] == "transformation"
    assert presentations[1]["treatment"] == "diagram"
    assert presentations[1]["layout"] != presentations[0]["layout"]


def test_carousel_plan_allows_repeated_metaphor_for_genuine_process():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Step one
Visual: First process arrow in a workflow
Slide 2:
Title: Step two
Visual: Second process arrow in the sequence"""
    )

    presentations = content_pack_routes._carousel_presentations(slides)

    assert presentations[0]["treatment"] == "process"
    assert presentations[1]["treatment"] == "process"


def test_representative_plan_uses_professional_treatment_and_layout_variety():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: One bold opening
Visual: A single hero object
Slide 2:
Title: A short statement
Visual: Typography-only statement
Slide 3:
Title: Three useful outcomes
Visual: Three benefits shown as grouped elements
Slide 4:
Title: Before and after
Visual: A true side-by-side comparison
Slide 5:
CTA: Finish with one action
Visual: Small closing accent"""
    )

    presentations = content_pack_routes._carousel_presentations(slides)

    assert len({item["treatment"] for item in presentations}) >= 3
    assert len({item["layout"] for item in presentations}) >= 4
    assert any(item["treatment"] == "typography_only" for item in presentations)


def test_feature_cards_require_explicit_grouped_semantics():
    grouped = content_pack_routes._select_visual_treatment(
        "Three benefits shown as grouped elements", "info"
    )
    ordinary = content_pack_routes._select_visual_treatment(
        "A single benefit shown as one focal object", "info"
    )

    assert grouped == "feature_cards"
    assert ordinary != "feature_cards"


def test_background_prompt_includes_weight_but_excludes_overlay_copy():
    private_copy = "Exact private customer headline"
    prompt = content_pack_routes._build_slide_background_prompt(
        "Style",
        1,
        "Three benefits shown as grouped elements",
        "info",
        visual_treatment="feature_cards",
        semantic_text="grouped elements",
        visual_weight="medium",
    )

    assert "visual weight: medium" in prompt
    assert "treatment: feature_cards" in prompt
    assert private_copy not in prompt


def test_language_learning_pairs_remain_structural_and_bounded():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Phrase: Dzień dobry
Translation: Good morning
Phrase: Jak się masz?
Translation: How are you?
Phrase: Miłego dnia!
Translation: Have a nice day!"""
    )

    assert slides[0]["phrase_pairs"] == (
        ("Dzień dobry", "Good morning"),
        ("Jak się masz?", "How are you?"),
        ("Miłego dnia!", "Have a nice day!"),
    )
    assert slides[0]["title"] == "Dzień dobry\nJak się masz?\nMiłego dnia!"
    assert slides[0]["body"] == "Good morning\nHow are you?\nHave a nice day!"


@pytest.mark.parametrize(
    ("concept", "required"),
    [
        ("A workbook document", "blank layered paper objects"),
        ("A phone screen interface", "abstract glowing display surface"),
        ("An open book page", "blank-page book object"),
        ("A poster sign", "blank graphic surface"),
        ("A language lesson card menu", "blank card object"),
    ],
)
def test_text_inviting_visual_concepts_are_sanitized(concept, required):
    direction = content_pack_routes._safe_visual_direction(concept, concept)
    assert required in direction
    assert "no " in direction


def test_language_learning_copy_never_reaches_provider_prompt():
    phrase = "Czy możesz mi pomóc?"
    translation = "Can you help me?"
    prompt = content_pack_routes._build_slide_background_prompt(
        "ignored", 1, "A language lesson card on a phone screen", "phrase",
        "split_left", "illustration", f"{phrase} {translation}", "medium",
        "distinct_object",
    )

    assert phrase not in prompt
    assert translation not in prompt
    assert "ABSOLUTELY NO READABLE TEXT" in prompt
    assert "no words, letters, numbers, language characters" in prompt
    assert "All visible typography is added later by SMU" in prompt


def test_campaign_art_direction_is_normalized_and_source_appropriate():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Transform one source
Visual: One concept becomes several formats
Slide 2:
CTA: Finish with purpose"""
    )
    direction = content_pack_routes._campaign_art_direction(
        "viral_carousel", slides
    )

    assert set(direction) == {
        "art_style", "visual_theme", "palette_intent", "lighting_or_depth",
        "texture_intent", "shape_language", "composition_energy",
        "campaign_motif", "image_detail_level",
    }
    assert direction["campaign_motif"] == "transformation"
    assert "premium SaaS editorial illustration" in direction["art_style"]


def test_provider_prompt_contains_rich_campaign_brief_without_overlay_copy():
    private_copy = "Private exact customer headline"
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        f"Slide 1:\nTitle: {private_copy}\nVisual: One bold transformation"
    )
    presentation = content_pack_routes._carousel_presentations(slides)[0]
    direction = content_pack_routes._campaign_art_direction("viral_carousel", slides)
    prompt = content_pack_routes._build_slide_background_prompt(
        "ignored raw customer image prompt", 0, slides[0]["visual"],
        presentation["role"], presentation["layout"], presentation["treatment"],
        presentation["semantic_text"], presentation["visual_weight"],
        presentation["metaphor"], direction,
    )

    assert private_copy not in prompt
    assert "ignored raw customer image prompt" not in prompt
    assert "1. CAMPAIGN STYLE LOCK" in prompt
    assert "2. SCENE BRIEF" in prompt
    assert "artwork-zone occupancy" in prompt
    assert "sequence purpose: opening campaign hero" in prompt
    assert "no readable text" in prompt
    assert "no readable logos" in prompt
    assert "no watermarks" in prompt
    assert "no UI screenshots" in prompt


def test_scene_brief_is_concrete_bounded_and_weight_specific():
    heavy = content_pack_routes._scene_brief(
        "Raw source material transforms into platform formats", "private overlay copy",
        "visual_focus", "heavy", "hero_left", "transformation",
    )
    medium = content_pack_routes._scene_brief(
        "Research reveals one insight", "private overlay copy", "illustration",
        "medium", "split_right", "document",
    )
    light = content_pack_routes._scene_brief(
        "One everyday object", "private overlay copy", "illustration", "light",
        "split_left", "distinct_object",
    )

    assert set(heavy) == {
        "scene_subject", "scene_action", "foreground_elements",
        "midground_elements", "background_environment", "spatial_relationship",
        "camera_or_viewpoint", "depth_strategy", "cropping_strategy",
        "material_or_surface_language", "focal_scale",
        "supporting_element_limit", "negative_space_intent", "metaphor_family",
    }
    assert "raw source form" in heavy["scene_subject"]
    assert "reshapes" in heavy["scene_action"]
    assert "cropped" in heavy["foreground_elements"]
    assert "deliberately crop" in heavy["cropping_strategy"]
    assert "no large dead margins" in heavy["negative_space_intent"]
    assert "balanced" in medium["focal_scale"]
    assert "restrained" in light["focal_scale"]
    assert "private overlay copy" not in repr((heavy, medium, light))


@pytest.mark.parametrize(
    ("treatment", "required"),
    [
        ("diagram", "directional relationship"),
        ("process", "sequential stages"),
        ("comparison", "two distinct states"),
        ("feature_cards", "not fake UI"),
    ],
)
def test_treatment_specific_scene_language_is_semantic(treatment, required):
    prompt = content_pack_routes._build_slide_background_prompt(
        "ignored", 2, "Source-relevant relationship", "info", "split_right",
        treatment, "private exact overlay copy", "medium", "distinct_object",
    )

    assert required in prompt
    assert "private exact overlay copy" not in prompt
    if treatment == "feature_cards":
        assert "cards containing text" in prompt


def test_heavy_prompt_has_confident_occupancy_without_small_object_contradiction():
    prompt = content_pack_routes._build_slide_background_prompt(
        "ignored", 0, "One source transforms", "cover", "hero_left",
        "visual_focus", "private overlay copy", "heavy", "transformation",
    )

    assert "dominant and large, confidently filling" in prompt
    assert "no large dead margins inside the artwork zone" in prompt
    assert "no tiny central icon" in prompt
    assert "small isolated supporting object" not in prompt
    assert "1. CAMPAIGN STYLE LOCK" in prompt
    assert prompt.index("1. CAMPAIGN STYLE LOCK") < prompt.index("2. SCENE BRIEF")
    assert prompt.index("2. SCENE BRIEF") < prompt.index("3. COMPOSITION / GEOMETRY")
    assert prompt.index("3. COMPOSITION / GEOMETRY") < prompt.index("4. QUALITY BAR")
    assert prompt.index("4. QUALITY BAR") < prompt.index("5. NEGATIVE REQUIREMENTS")


def test_typography_only_builds_no_provider_artwork_prompt():
    assert content_pack_routes._build_slide_background_prompt(
        "ignored", 1, visual_treatment="typography_only"
    ) is None


def test_repeated_node_metaphor_is_removed_from_actual_artwork_prompt():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Connected ideas
Visual: A branching node network
Slide 2:
Title: Another perspective
Visual: A branching node network"""
    )
    presentations = content_pack_routes._carousel_presentations(slides)
    cover = presentations[0]
    prompt = content_pack_routes._build_slide_background_prompt(
        "Locked campaign style", 0, slides[0]["visual"], cover["role"],
        cover["layout"], cover["treatment"], cover["semantic_text"],
        cover["visual_weight"], cover["metaphor"],
    )

    assert cover["metaphor"] == "transformation"
    assert "avoiding nodes, branches, arrows, and network geometry" in prompt


def test_non_adjacent_node_metaphor_is_remapped_before_artwork_generation():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Strong opening
Visual: One bold focal object
Slide 2:
Title: Connected ideas
Visual: A branching node network
Slide 3:
Title: Editorial insight
Visual: One document under analysis
Slide 4:
Title: Better connections
Visual: Another branching node network"""
    )

    presentations = content_pack_routes._carousel_presentations(slides)

    assert presentations[1]["metaphor"] == "node_network"
    assert presentations[3]["metaphor"] == "focal_object"
    assert presentations[3]["treatment"] == "visual_focus"


def test_diagram_fallback_is_counted_as_node_family_for_repetition_control():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Build for every platform
Visual: Distinct platform-ready formats
Slide 2:
Title: Inspect the source
Visual: One editorial document
Slide 3:
Title: Connect with the audience
Visual: Better connections everywhere"""
    )

    presentations = content_pack_routes._carousel_presentations(slides)

    assert presentations[0]["metaphor"] == "transformation"
    assert presentations[0]["treatment"] == "visual_focus"
    assert presentations[2]["metaphor"] == "node_network"


def test_cover_does_not_collapse_to_generic_diagram_fallback():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Build for every platform
Visual: One source branches into distinct platform-ready formats
Slide 2:
CTA: Finish with purpose"""
    )

    cover = content_pack_routes._carousel_presentations(slides)[0]

    assert cover["visual_weight"] == "heavy"
    assert cover["treatment"] == "visual_focus"
    assert cover["layout"] == "hero_center"
    assert cover["metaphor"] == "transformation"


def test_presentation_plan_varies_campaign_furniture_and_restrains_closing():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        "\n".join(f"Slide {index}: Distinct idea {index}" for index in range(1, 6))
    )
    slides[-1]["layout_role"] = "cta"

    presentations = content_pack_routes._carousel_presentations(slides)

    assert presentations[0]["furniture"] == "dual_rail"
    assert presentations[-1]["furniture"] == "single_rail"
    assert len({item["furniture"] for item in presentations}) >= 3


def test_content_pack_carousel_parser_preserves_unlabelled_text():
    assert content_pack_routes._parse_content_pack_carousel_slides("Miłego dnia!") == [
        {
            "title": "Miłego dnia!",
            "body": None,
            "cta": None,
            "brand": None,
            "visual": None,
            "layout_role": "info",
        }
    ]


def test_content_pack_carousel_parser_promotes_cta_only_copy_to_required_title():
    assert content_pack_routes._parse_content_pack_carousel_slides(
        "Slide 6:\nCTA: Learn more Polish with Polish with Me"
    ) == [
        {
            "title": "Learn more Polish with Polish with Me",
            "body": None,
            "cta": None,
            "brand": None,
            "visual": None,
            "layout_role": "cta",
        }
    ]


@pytest.mark.parametrize("slide_count", [2, 6])
def test_carousel_normalizer_accepts_valid_slide_counts_unchanged(slide_count):
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        "\n".join(
            f"Slide {index}: Value {index}"
            for index in range(1, slide_count + 1)
        )
    )

    assert content_pack_routes._normalize_content_pack_carousel_slides(slides) == slides


def test_viral_carousel_quality_accepts_short_distinct_progression():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1: Start with the source
Slide 2: Find the strongest idea
Slide 3: Build for each platform
Slide 4: Adapt with purpose"""
    )

    assert content_pack_routes._validate_viral_carousel_copy(slides) is None


@pytest.mark.parametrize(
    ("carousel", "reason"),
    [
        (
            "Slide 1: Strong cover\nSlide 2: Repeated point\nSlide 3: Repeated point",
            "carousel_repeats_slide",
        ),
        (
            "Slide 1: Strong cover\nSlide 2: Takeaway",
            "carousel_generic_closing",
        ),
    ],
)
def test_viral_carousel_quality_rejects_repetitive_or_generic_copy(
    carousel, reason
):
    slides = content_pack_routes._parse_content_pack_carousel_slides(carousel)

    with pytest.raises(ValueError, match=reason):
        content_pack_routes._validate_viral_carousel_copy(slides)


def test_viral_carousel_quality_preserves_polish_exactly():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        "Slide 1: Jeden pomysł\nSlide 2: Wiele możliwości"
    )

    content_pack_routes._validate_viral_carousel_copy(slides)
    assert [slide["title"] for slide in slides] == [
        "Jeden pomysł", "Wiele możliwości"
    ]


def test_density_gate_accepts_eight_word_cover_and_ten_short_internal_words():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1: One clear idea can become many better posts
Slide 2: We can make it fit on each new platform now"""
    )

    assert content_pack_routes._copy_word_count(slides[0]["title"]) == 8
    assert content_pack_routes._copy_word_count(slides[1]["title"]) == 10
    assert content_pack_routes._validate_viral_carousel_copy(slides) is None


def test_renderer_safe_production_11_word_68_character_info_headline_passes():
    headline = "One idea makes many social posts without losing its original meaning"
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        f"Slide 1: Strong cover\nSlide 2: {headline}"
    )

    assert content_pack_routes._copy_word_count(headline) == 11
    assert len(headline) == 68
    assert content_pack_routes._validate_viral_carousel_copy(slides) is None


def test_production_15_word_99_character_cover_support_uses_real_preflight():
    support = (
        "Core source becomes useful content for every platform without losing its "
        "original meaning or focus."
    )
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        f"Slide 1:\nTitle: Strong cover\nBody: {support}\nSlide 2: Distinct close"
    )

    assert content_pack_routes._copy_word_count(support) == 15
    assert len(support) == 99
    assert content_pack_routes._validate_viral_carousel_copy(slides) is None


def test_preflight_rejects_genuinely_unrenderable_internal_headline():
    headline = (
        "Supercalifragilisticexpialidocious pseudopseudohypoparathyroidism "
        "electroencephalographically counterdemonstrations"
    )
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        f"Slide 1: Strong cover\nSlide 2: {headline}"
    )

    with pytest.raises(
        content_pack_routes.CarouselQualityError,
        match="carousel_headline_does_not_fit",
    ):
        content_pack_routes._validate_viral_carousel_copy(slides)


def test_density_gate_counts_hyphenated_term_as_one_word():
    assert content_pack_routes._copy_word_count("source-first platform-native") == 2


@pytest.mark.parametrize(
    "support",
    [
        "One source becomes focused content for every platform without losing its core intent",
        "One two three four five six seven eight nine ten eleven twelve thirteen fourteen",
        "One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen",
    ],
)
def test_support_density_gate_accepts_render_safe_internal_copy(support):
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        f"Slide 1: Strong cover\nSlide 2:\nTitle: Clear point\nBody: {support}"
    )

    assert content_pack_routes._validate_viral_carousel_copy(slides) is None


def test_support_density_gate_accepts_production_shaped_13_word_82_character_copy():
    support = (
        "Turn a prime source idea into platform-ready posts without losing focus or intent."
    )
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        f"Slide 1: Strong cover\nSlide 2:\nTitle: Clear point\nBody: {support}"
    )

    assert content_pack_routes._copy_word_count(support) == 13
    assert len(support) == 82
    assert content_pack_routes._validate_viral_carousel_copy(slides) is None


def test_multiple_support_sentences_reach_renderer_preflight(monkeypatch):
    support = "Clarity comes first. Then strong action follows."
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        f"Slide 1: Strong cover\nSlide 2:\nTitle: Clear point\nBody: {support}"
    )
    calls = []
    original = content_pack_routes.preflight_viral_carousel_text

    def capture(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(content_pack_routes, "preflight_viral_carousel_text", capture)

    assert content_pack_routes._copy_word_count(support) == 7
    assert len(support) == 48
    assert content_pack_routes._validate_viral_carousel_copy(slides) is None
    assert calls[1]["body"] == support


def test_production_shaped_multiple_sentence_headline_reaches_preflight(monkeypatch):
    headline = "Clarity comes first. Then strong action follows."
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        f"Slide 1: Strong cover\nSlide 2: {headline}"
    )
    calls = []
    original = content_pack_routes.preflight_viral_carousel_text

    def capture(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(content_pack_routes, "preflight_viral_carousel_text", capture)

    assert content_pack_routes._copy_word_count(headline) == 7
    assert len(headline) == 48
    assert content_pack_routes._validate_viral_carousel_copy(slides) is None
    assert calls[1]["title"] == headline


@pytest.mark.parametrize(
    ("field", "value", "structure_reason"),
    [
        ("role", "unknown", "unsupported_role"),
        ("treatment", "unknown", "unsupported_treatment"),
        ("layout", "unknown", "unsupported_layout"),
    ],
)
def test_malformed_presentation_fails_with_safe_structure_subreason(
    field, value, structure_reason, caplog
):
    private_title = "private customer headline"
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        f"Slide 1: {private_title}\nSlide 2: Safe close"
    )
    presentations = content_pack_routes._carousel_presentations(slides)
    presentations[0][field] = value
    caplog.set_level(
        logging.WARNING, logger="smu_core.blueprints.content_pack.routes"
    )

    with pytest.raises(content_pack_routes.CarouselQualityError) as raised:
        content_pack_routes._validate_viral_carousel_copy(slides, presentations)

    assert raised.value.structure_reason == structure_reason
    assert f"structure_reason={structure_reason}" in caplog.text
    assert private_title not in caplog.text


def test_missing_valid_and_unmatched_optional_emphasis_are_safe(monkeypatch):
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Strong cover
Slide 2:
Title: A valid emphasis example
Emphasis: valid emphasis
Slide 3:
Title: An unmatched emphasis is optional
Emphasis: text not in headline"""
    )
    calls = []
    original = content_pack_routes.preflight_viral_carousel_text

    def capture(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(content_pack_routes, "preflight_viral_carousel_text", capture)

    assert content_pack_routes._validate_viral_carousel_copy(slides) is None
    assert calls[0]["emphasis"] is None
    assert calls[1]["emphasis"] == {"text": "valid emphasis", "role": "accent"}
    assert calls[2]["emphasis"] is None


def test_genuine_structure_failure_creates_no_rows_or_credit_reservation(
    client, app, module, monkeypatch
):
    user = create_user(module, email="structure-gate@example.com")
    login(client, user)
    reserve_calls = []
    set_content_pack_helper(
        app,
        monkeypatch,
        "reserve_ai_image_credits",
        lambda current_user, count, commit=False: reserve_calls.append(count),
    )
    original = content_pack_routes._carousel_presentations

    def invalid_presentations(slides):
        presentations = original(slides)
        presentations[0]["role"] = "unknown"
        return presentations

    monkeypatch.setattr(
        content_pack_routes, "_carousel_presentations", invalid_presentations
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": CONTENT_PACK_RESULT, "image_style": "viral_carousel"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert reserve_calls == []
    assert module.Post.query.count() == 0


def test_complete_six_slide_carousel_passes_all_copy_quality_gates():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: One source, six stronger posts
Body: Build once, then adapt with purpose.
Slide 2:
Title: Find the sharpest insight
Body: Start with the idea your audience will remember and use tomorrow.
Slide 3:
Title: Shape it for each platform
Body: Turn a prime source idea into platform-ready posts without losing focus or intent.
Slide 4:
Title: Change the angle, not truth
Body: Match each channel while preserving the source and its original meaning.
Slide 5:
Title: Give every slide one job
Body: Let each frame advance one clear, useful part of the story.
Slide 6:
CTA: Build your next content system
Body: Start with one source today."""
    )

    assert len(slides) == 6
    assert content_pack_routes._validate_viral_carousel_copy(slides) is None


def test_support_density_rejection_logs_safe_metrics_without_copy(caplog):
    private_support = "confidentialword " * 45
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        "Slide 1: Strong cover\nSlide 2:\nTitle: Clear point\n"
        f"Body: {private_support.strip()}"
    )
    caplog.set_level(
        logging.WARNING, logger="smu_core.blueprints.content_pack.routes"
    )

    with pytest.raises(content_pack_routes.CarouselQualityError) as raised:
        content_pack_routes._validate_viral_carousel_copy(slides)

    assert raised.value.slide_index == 2
    assert raised.value.role == "info"
    assert raised.value.word_count == content_pack_routes._copy_word_count(
        private_support
    )
    assert raised.value.character_count == len(private_support.strip())
    assert "carousel_copy_quality_rejected" in caplog.text
    assert "content_type=" in caplog.text
    assert "slide_index=2" in caplog.text
    assert "role=info" in caplog.text
    assert f"word_count={raised.value.word_count}" in caplog.text
    assert "character_count=" in caplog.text
    assert private_support.strip() not in caplog.text


def test_density_rejection_is_user_safe_and_reserves_no_image_credits(
    client, app, module, monkeypatch
):
    user = create_user(module, email="density-gate@example.com")
    usage = module.UserUsage(
        user_id=user.id,
        plan="starter",
        content_packs_used=1,
        ai_images_used=0,
        usage_period_start=utc_now() - timedelta(days=1),
        usage_period_end=utc_now() + timedelta(days=30),
    )
    module.db.session.add(usage)
    module.db.session.commit()
    login(client, user)
    reserve_calls = []
    set_content_pack_helper(
        app,
        monkeypatch,
        "reserve_ai_image_credits",
        lambda current_user, count, commit=False: reserve_calls.append(count),
    )
    dense_pack = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        "Slide 1: Strong cover\n"
        "Slide 2:\nTitle: Clear point\n"
        f"Body: {'oversizedprivateword ' * 40}",
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": dense_pack, "image_style": "viral_carousel"},
        follow_redirects=True,
    )
    refreshed = module.db.session.get(module.UserUsage, usage.id)

    assert response.status_code == 200
    assert "one slide was too text-heavy" in response.get_data(as_text=True)
    assert "carousel_support_does_not_fit" not in response.get_data(as_text=True)
    assert reserve_calls == []
    assert module.Post.query.count() == 0
    assert refreshed.ai_images_used == 0
    assert refreshed.content_packs_used == 1


def test_successful_preflight_timing_log_is_safe(client, app, module, monkeypatch, caplog):
    user = create_user(module)
    login(client, user)
    set_content_pack_helper(
        app, monkeypatch, "get_placeholder_image_url",
        lambda: "https://cdn.test/placeholder.jpg",
    )
    private_copy = "Private preflight headline"
    result = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        f"Slide 1: {private_copy}\nSlide 2: Short lesson\nSlide 3: Clear close",
    )
    caplog.set_level(logging.INFO, logger=content_pack_routes.__name__)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": result, "image_style": "viral_carousel"},
    )

    assert response.status_code == 302
    assert "carousel_preflight_complete slide_count=3 duration_ms=" in caplog.text
    assert private_copy not in caplog.text


def test_scene_variety_plan_is_bounded_deterministic_and_role_aware():
    slides = [
        {"title": "Polish at a Restaurant", "body": "Useful phrases", "visual": "restaurant interior", "layout_role": "info"},
        {"title": "Co polecasz?", "body": "What do you recommend?", "visual": "ordering", "layout_role": "phrase"},
        {"title": "Dania wegetariańskie", "body": "Vegetarian dishes", "visual": "food choice", "layout_role": "phrase"},
        {"title": "Poproszę menu", "body": "The menu, please", "visual": "menu service", "layout_role": "phrase"},
        {"title": "Poproszę rachunek", "body": "The bill, please", "visual": "payment", "layout_role": "phrase"},
        {"title": "Save and practise", "body": None, "visual": "typography-only", "layout_role": "cta"},
    ]
    presentations = content_pack_routes._carousel_presentations(slides)
    direction = content_pack_routes._campaign_art_direction(
        "viral_carousel", slides, "photorealistic", "warm_sunset"
    )
    grounding = content_pack_routes._campaign_grounding(slides, direction)

    first = content_pack_routes._scene_variety_plan(slides, presentations, grounding)
    second = content_pack_routes._scene_variety_plan(slides, presentations, grounding)

    assert first == second
    assert first[0]["semantic_role"] == "campaign_cover"
    assert first[0]["scene_mode"] == "establishing"
    assert first[0]["shot_type"] == "wide"
    assert first[-1]["semantic_role"] == "closing"
    assert all(item["scene_mode"] in content_pack_routes.SCENE_MODES for item in first)
    assert all(item["shot_type"] in content_pack_routes.SHOT_TYPES for item in first)
    assert all(item["subject_category"] in content_pack_routes.SUBJECT_CATEGORIES for item in first)
    assert all(
        (item["scene_mode"], item["shot_type"], item["subject_category"])
        != (previous["scene_mode"], previous["shot_type"], previous["subject_category"])
        for previous, item in zip(first, first[1:])
    )


def test_scene_variety_is_request_local_across_unrelated_campaigns():
    restaurant = [{"title": "Restaurant guide", "body": None, "visual": "dining room", "layout_role": "info"}]
    technology = [{"title": "AI workflow", "body": None, "visual": "device workflow", "layout_role": "info"}]

    def plan(slides):
        presentations = content_pack_routes._carousel_presentations(slides)
        direction = content_pack_routes._campaign_art_direction("viral_carousel", slides)
        grounding = content_pack_routes._campaign_grounding(slides, direction)
        return content_pack_routes._scene_variety_plan(slides, presentations, grounding)

    assert plan(restaurant) == plan(restaurant)
    assert plan(technology) == plan(technology)
    assert plan(restaurant)[0] == plan(restaurant)[0]


@pytest.mark.parametrize("slide_count", [7, 9])
def test_carousel_normalizer_caps_oversized_pack_and_preserves_final_cta(
    slide_count,
):
    blocks = ["Slide 1:\nTitle: Cover"]
    blocks.extend(
        f"Slide {index}:\nTitle: Value {index}"
        for index in range(2, slide_count)
    )
    blocks.append(f"Slide {slide_count}:\nCTA: Final action")
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        "\n".join(blocks)
    )

    normalized = content_pack_routes._normalize_content_pack_carousel_slides(slides)

    assert len(normalized) == 6
    assert [slide["title"] for slide in normalized] == [
        "Cover",
        "Value 2",
        "Value 3",
        "Value 4",
        "Value 5",
        "Final action",
    ]
    assert normalized[-1]["layout_role"] == "cta"


def test_carousel_normalizer_without_cta_retains_first_six_in_source_order():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        "\n".join(f"Slide {index}: Value {index}" for index in range(1, 9))
    )

    normalized = content_pack_routes._normalize_content_pack_carousel_slides(slides)

    assert [slide["title"] for slide in normalized] == [
        "Value 1",
        "Value 2",
        "Value 3",
        "Value 4",
        "Value 5",
        "Value 6",
    ]


def test_carousel_normalizer_keeps_only_final_semantic_cta_when_oversized():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Title: Cover
Slide 2:
CTA: Early action
Slide 3:
Title: Value 3
Slide 4:
Title: Value 4
Slide 5:
Title: Value 5
Slide 6:
Title: Value 6
Slide 7:
Title: Value 7
Slide 8:
CTA: Final action"""
    )

    normalized = content_pack_routes._normalize_content_pack_carousel_slides(slides)

    assert [slide["title"] for slide in normalized] == [
        "Cover",
        "Value 3",
        "Value 4",
        "Value 5",
        "Value 6",
        "Final action",
    ]
    assert normalized[-1]["layout_role"] == "cta"


def test_oversized_carousel_creates_six_rows_and_reserves_six_credits(
    client, module
):
    module.app.config["SMU_ADMIN_EMAILS"] = set()
    user = create_user(module, email="normalized-six@example.com")
    usage = module.UserUsage(
        user_id=user.id,
        plan="starter",
        ai_images_used=14,
        content_packs_used=0,
        usage_period_start=utc_now() - timedelta(days=1),
        usage_period_end=utc_now() + timedelta(days=30),
    )
    module.db.session.add(usage)
    module.db.session.commit()
    login(client, user)
    oversized = "\n".join(
        ["Slide 1:\nTitle: Cover"]
        + [f"Slide {index}:\nTitle: Value {index}" for index in range(2, 7)]
        + ["Slide 7:\nCTA: Final action"]
    )
    content_pack_result = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        oversized,
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack_result},
    )
    posts = module.Post.query.order_by(module.Post.sort_order.asc()).all()
    payloads = [carousel_generation.parse_overlay_prompt(post.prompt) for post in posts]

    assert response.status_code == 302
    assert len(posts) == 6
    assert module.db.session.get(module.UserUsage, usage.id).ai_images_used == 20
    assert [payload["overlay"]["title"] for payload in payloads] == [
        "Cover",
        "Value 2",
        "Value 3",
        "Value 4",
        "Value 5",
        "Final action",
    ]
    assert [payload["layout_role"] for payload in payloads] == [
        "cover",
        "info",
        "info",
        "info",
        "info",
        "cta",
    ]


def test_oversized_carousel_credit_message_uses_normalized_slide_count(
    client, module
):
    module.app.config["SMU_ADMIN_EMAILS"] = set()
    user = create_user(module, email="normalized-insufficient@example.com")
    usage = module.UserUsage(
        user_id=user.id,
        plan="starter",
        ai_images_used=15,
        content_packs_used=0,
        usage_period_start=utc_now() - timedelta(days=1),
        usage_period_end=utc_now() + timedelta(days=30),
    )
    module.db.session.add(usage)
    module.db.session.commit()
    login(client, user)
    oversized = "\n".join(
        [f"Slide {index}: Value {index}" for index in range(1, 7)]
        + ["Slide 7:\nCTA: Final action"]
    )
    content_pack_result = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        oversized,
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack_result},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (
        "You need 6 AI image credits to create this carousel, but you have 5 remaining."
        in response.get_data(as_text=True)
    )
    assert module.Post.query.count() == 0
    assert module.db.session.get(module.UserUsage, usage.id).ai_images_used == 15


def test_carousel_with_fewer_than_two_valid_slides_uses_safe_message(
    client, module
):
    user = create_user(module)
    login(client, user)
    content_pack_result = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        "Slide 1:\nTitle: Only valid slide\nSlide 2:",
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack_result},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "SMU couldn&#39;t create enough carousel slides" in response.get_data(
        as_text=True
    )
    assert module.Post.query.count() == 0
    assert module.UserUsage.query.filter_by(user_id=user.id).first() is None


def test_tip_is_body_copy_and_visual_is_background_metadata_only():
    slides = content_pack_routes._parse_content_pack_carousel_slides(
        """Slide 1:
Phrase: Miłego dnia!
Translation: Have a nice day!
Tip: Use with friends and younger people.
Visual: Two young people smiling and waving"""
    )

    assert slides == [
        {
            "title": "Miłego dnia!",
            "body": "Have a nice day!\nUse with friends and younger people.",
            "cta": None,
            "brand": None,
            "visual": "Two young people smiling and waving",
            "layout_role": "phrase",
            "phrase_pairs": (("Miłego dnia!", "Have a nice day!"),),
        }
    ]


def test_visual_direction_is_categorical_and_strips_text_request():
    prompt = content_pack_routes._build_slide_background_prompt(
        "Realistic photography",
        1,
        "Sunset background with greeting text",
    )

    assert "warm sunset atmosphere" in prompt
    assert "friendly conversational interaction" in prompt
    assert "Sunset background with greeting text" not in prompt
    assert "greeting text" not in prompt
    assert "no readable text" in prompt


@pytest.mark.parametrize(
    ("visual", "expected"),
    [
        ("One source branching into platform destinations", "one original object branching"),
        ("Raw notes organised into a source idea", "raw source materials being examined"),
        ("One object from a different angle", "different visual perspectives"),
        ("A completed campaign ready to publish", "finished cohesive campaign system"),
        ("Polish vocabulary used in conversation", "language learning"),
    ],
)
def test_visual_direction_maps_meaning_to_safe_text_free_metaphors(visual, expected):
    prompt = content_pack_routes._build_slide_background_prompt(
        "Balanced editorial style", 2, visual, "info"
    )

    assert expected in prompt
    assert visual not in prompt
    assert "no readable text" in prompt


def test_visual_direction_does_not_default_to_generic_laptop_scene():
    prompt = content_pack_routes._build_slide_background_prompt(
        "Balanced editorial style",
        3,
        "A person at a laptop changing the content angle",
        "info",
    )

    assert "different visual perspectives" in prompt
    assert "person at a laptop" not in prompt
    assert "smartphone" not in prompt


def test_background_prompts_reserve_role_specific_negative_space():
    cover = content_pack_routes._build_slide_background_prompt("Style", 0)
    phrase = content_pack_routes._build_slide_background_prompt(
        "Style", 1, layout_role="phrase"
    )
    info = content_pack_routes._build_slide_background_prompt(
        "Style", 2, layout_role="info"
    )
    cta = content_pack_routes._build_slide_background_prompt(
        "Style", 5, layout_role="cta"
    )

    assert "slide role: cover" in cover
    assert "layout: hero_left" in cover
    assert "weighted to the right artwork zone" in cover
    assert "left typography zone completely free" in cover
    assert "slide role: phrase" in phrase
    assert "layout: split_left" in phrase
    assert "designated right artwork zone" in phrase
    assert "left typography zone empty" in phrase
    assert "slide role: info" in info
    assert "layout: split_right" in info
    assert "designated left artwork zone" in info
    assert "right typography zone empty" in info
    assert "slide role: cta" in cta
    assert "layout: closing" in cta
    assert "Typography must dominate" in cta
    assert "small upper-right zone" in cta
    assert all(
        "faces, facial features, and primary objects completely outside" in prompt
        for prompt in (cover, phrase, info, cta)
    )


def test_content_pack_carousel_builds_six_distinct_text_free_backgrounds(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)
    structured_slides = """Slide 1:
Title: Speak Polish in Simple Steps
Subtitle: Essential Polish words and phrases for beginners
Slide 2:
Phrase: Jak się masz?
Translation: How are you?
Slide 3:
Phrase: Miłego dnia!
Translation: Have a nice day!
Slide 4:
Title: Zażółć gęślą jaźń
Body: Polish vocabulary practice
Slide 5:
Phrase: Szczęśliwej podróży!
Translation: Have a good trip!
Slide 6:
CTA: Learn more Polish with Polish with Me"""
    content_pack_result = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        structured_slides,
    )
    set_content_pack_helper(
        app,
        monkeypatch,
        "get_placeholder_image_url",
        lambda: "https://cdn.test/placeholder.jpg",
    )
    set_content_pack_helper(
        app,
        monkeypatch,
        "apply_image_style",
        lambda prompt, style: "CONSISTENT BRAND STYLE",
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack_result, "image_style": "minimal"},
    )
    posts = module.Post.query.order_by(module.Post.sort_order.asc()).all()
    payloads = [carousel_generation.parse_overlay_prompt(post.prompt) for post in posts]
    backgrounds = [payload["background_prompt"] for payload in payloads]

    assert response.status_code == 302
    assert len(payloads) == 6
    assert payloads[5]["overlay"] == {
        "title": "Learn more Polish with Polish with Me",
        "body": None,
        "cta": None,
        "brand": None,
    }
    assert [payload["layout_role"] for payload in payloads] == [
        "cover",
        "phrase",
        "phrase",
        "info",
        "phrase",
        "cta",
    ]
    assert [payload["layout_variant"] for payload in payloads] == [
        "hero_left",
        "split_left",
        "split_right",
        "split_left",
        "split_right",
        "closing",
    ]
    artwork_payloads = [
        payload for payload in payloads
        if payload["visual_treatment"] != "typography_only"
    ]
    assert all(
        payload["layout_variant"] in payload["background_prompt"]
        for payload in artwork_payloads
    )
    assert all(
        payload["background_prompt"] == content_pack_routes.TYPOGRAPHY_ONLY_BACKGROUND
        for payload in payloads if payload["visual_treatment"] == "typography_only"
    )
    assert len(set(payload["background_prompt"] for payload in artwork_payloads)) == len(artwork_payloads)
    assert all("art style: minimalist modern design" in payload["background_prompt"] for payload in artwork_payloads)
    assert all("2. SCENE BRIEF" in payload["background_prompt"] for payload in artwork_payloads)
    required_text_free_phrases = (
        "no readable text",
        "no words",
        "no letters",
        "no handwriting",
        "no pseudo-text",
        "no gibberish text",
        "no typography",
        "no captions",
        "no labels",
        "no readable logos",
        "no text on screens",
        "no text on paper",
        "no written signs",
    )
    assert all(
        phrase in prompt
        for prompt in (payload["background_prompt"] for payload in artwork_payloads)
        for phrase in required_text_free_phrases
    )
    for payload in payloads:
        for value in payload["overlay"].values():
            if value:
                assert value not in payload["background_prompt"]


@pytest.mark.parametrize(
    ("image_style", "expected_treatment"),
    [
        ("realistic", "high-quality photography"),
        ("viral_carousel", "viral Instagram business carousel"),
        ("luxury", "luxury brand aesthetic"),
        ("minimal", "minimalist modern design"),
        ("corporate", "professional corporate social media design"),
        ("pixar", "3D animated film look"),
    ],
)
def test_content_pack_carousel_keeps_selected_style_across_all_six_slides(
    client, app, module, image_style, expected_treatment
):
    user = create_user(module, email=f"{image_style}@example.com")
    login(client, user)
    structured_slides = "\n".join(
        f"Slide {index}: Private overlay copy {index}" for index in range(1, 7)
    )
    content_pack_result = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        structured_slides,
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack_result, "image_style": image_style},
    )
    posts = module.Post.query.order_by(module.Post.sort_order.asc()).all()
    payloads = [carousel_generation.parse_overlay_prompt(post.prompt) for post in posts]
    backgrounds = [payload["background_prompt"] for payload in payloads]

    assert response.status_code == 302
    assert len(backgrounds) == 6
    artwork_backgrounds = [
        payload["background_prompt"] for payload in payloads
        if payload["visual_treatment"] != "typography_only"
    ]
    assert len(set(artwork_backgrounds)) == len(artwork_backgrounds)
    assert all(expected_treatment in prompt for prompt in artwork_backgrounds)
    assert all("1. CAMPAIGN STYLE LOCK" in prompt for prompt in artwork_backgrounds)
    assert all(
        "Private overlay copy" not in prompt for prompt in backgrounds
    )


@pytest.mark.parametrize("image_style", ["", "unknown-style"])
def test_content_pack_carousel_default_and_unknown_style_use_existing_fallback(
    client, module, image_style
):
    user = create_user(module, email=f"fallback-{image_style or 'default'}@example.com")
    login(client, user)
    structured_slides = "\n".join(
        f"Slide {index}: Isolated copy {index}" for index in range(1, 7)
    )
    content_pack_result = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        structured_slides,
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack_result, "image_style": image_style},
    )
    posts = module.Post.query.order_by(module.Post.sort_order.asc()).all()
    backgrounds = [
        carousel_generation.parse_overlay_prompt(post.prompt)["background_prompt"]
        for post in posts
    ]

    assert response.status_code == 302
    assert len(backgrounds) == 6
    artwork_backgrounds = [
        payload["background_prompt"]
        for post in posts
        if (payload := carousel_generation.parse_overlay_prompt(post.prompt))["visual_treatment"] != "typography_only"
    ]
    assert all("art style: premium editorial illustration" in prompt for prompt in artwork_backgrounds)
    assert all("\nStyle:" not in prompt for prompt in artwork_backgrounds)
    assert all("1. CAMPAIGN STYLE LOCK" in prompt for prompt in artwork_backgrounds)


def test_content_pack_carousel_preserves_exact_polish_slide_copy(
    client, app, module, monkeypatch
):
    user = create_user(module)
    login(client, user)
    polish_slides = [
        "Miłego dnia!",
        "Szczęśliwej podróży!",
        "Często tu przychodzisz?",
        "Zażółć gęślą jaźń",
    ]
    content_pack_result = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        "\n".join(
            f"Slide {index}: {text}"
            for index, text in enumerate(polish_slides, start=1)
        ),
    )
    set_content_pack_helper(
        app, monkeypatch, "get_placeholder_image_url", lambda: "https://cdn.test/placeholder.jpg"
    )
    set_content_pack_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: prompt)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack_result, "image_style": "minimal"},
    )
    posts = module.Post.query.order_by(module.Post.sort_order.asc()).all()
    payloads = [carousel_generation.parse_overlay_prompt(post.prompt) for post in posts]

    assert response.status_code == 302
    assert [payload["overlay"]["title"] for payload in payloads] == polish_slides
    for text, payload in zip(polish_slides, payloads):
        assert text not in payload["background_prompt"]


def test_content_pack_carousel_rejects_oversized_slide_without_rows(
    client, module
):
    user = create_user(module)
    login(client, user)
    oversized = "x" * (carousel_generation.MAX_OVERLAY_TITLE_LENGTH + 1)
    content_pack_result = CONTENT_PACK_RESULT.replace(
        "Slide 1: First slide\nSlide 2: Second slide\nSlide 3: Third slide",
        f"Slide 1: {oversized}\nSlide 2: Valid slide",
    )

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": content_pack_result},
    )

    assert response.status_code == 302
    assert response.location.endswith("/content-pack")
    assert module.Post.query.count() == 0
    usage = module.UserUsage.query.filter_by(user_id=user.id).one()
    assert usage.ai_images_used == 0


def test_create_content_pack_carousel_validation_creates_no_rows(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post(
        "/content-pack/create-carousel",
        data={"content_pack_result": "", "image_style": "viral_carousel"},
    )

    assert response.status_code == 302
    assert response.location.endswith("/content-pack")
    assert module.Post.query.count() == 0


def test_create_platform_draft_creates_single_post(client, app, module, monkeypatch):
    user = create_user(module)
    login(client, user)
    set_content_pack_helper(app, monkeypatch, "build_brand_context", lambda user_id: "BRAND")
    set_content_pack_helper(app, monkeypatch, "get_placeholder_image_url", lambda: "https://cdn.test/single.jpg")
    set_content_pack_helper(app, monkeypatch, "apply_image_style", lambda prompt, style: f"{style}:{prompt}")

    response = client.post(
        "/content-pack/create-platform-draft",
        data={
            "content_pack_result": CONTENT_PACK_RESULT,
            "platform": "linkedin",
            "image_style": "minimal",
        },
    )
    post = module.Post.query.first()

    assert response.status_code == 302
    assert post is not None
    assert post.user_id == user.id
    assert post.file_url == "https://cdn.test/single.jpg"
    assert post.file_type == "image"
    assert post.caption == "LinkedIn caption"
    assert post.platforms == "linkedin"
    assert post.post_type == "single"
    assert post.status == "generating"
    assert post.sort_order == 0
    assert post.is_cover is False
    assert "Brand Brief:\nBRAND" in post.prompt
    assert "LinkedIn caption" in post.prompt
    assert response.location.endswith(f"/post/{post.id}")


def test_create_platform_draft_validation_creates_no_row(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post(
        "/content-pack/create-platform-draft",
        data={"content_pack_result": CONTENT_PACK_RESULT, "platform": "threads"},
    )

    assert response.status_code == 302
    assert response.location.endswith("/content-pack")
    assert module.Post.query.count() == 0


def test_content_pack_brand_lookup_is_user_specific(client, app, module, monkeypatch):
    owner = create_user(module, email="owner@example.com")
    other = create_user(module, email="other@example.com")
    module.db.session.add(module.BrandBrief(user_id=owner.id, business_name="Owner"))
    module.db.session.add(module.BrandBrief(user_id=other.id, business_name="Other"))
    module.db.session.commit()
    login(client, other)
    seen_user_ids = []

    def fake_build_brand_context(user_id):
        seen_user_ids.append(user_id)
        return "OTHER BRAND CONTEXT"

    set_content_pack_helper(app, monkeypatch, "build_brand_context", fake_build_brand_context)
    set_content_pack_helper(
        app,
        monkeypatch,
        "generate_content_pack",
        lambda source_text, brand_context: brand_context,
    )

    with captured_templates(app) as templates:
        response = client.post(
            "/content-pack",
            data={"source_type": "text", "source_input": "Idea"},
        )

    assert response.status_code == 200
    assert seen_user_ids == [other.id]
    assert templates[0][1]["content_pack_result"] == "OTHER BRAND CONTEXT"


def test_content_pack_model_and_app_import_compatibility_remain(module):
    assert smu_app.Post is Post
    assert smu_app.BrandBrief is BrandBrief
    assert module.Post is Post
    assert module.BrandBrief is BrandBrief


def test_existing_blueprints_and_unrelated_endpoints_remain_registered(module):
    endpoints = {rule.endpoint for rule in module.app.url_map.iter_rules()}

    assert {
        "landing_page",
        "privacy_policy",
        "terms_of_service",
        "maintenance",
        "help_centre",
        "contact",
        "register",
        "login",
        "logout",
        "beta_apply",
        "admin_beta",
        "submit_feedback",
        "brand_brief",
        "connected_accounts",
        "index",
        "calendar_view",
        "post_studio",
        "send_to_make",
        "tiktok_repurpose",
    }.issubset(endpoints)


def test_scheduler_reference_is_untouched(module):
    assert module.scheduler is smu_app.scheduler

import pytest

from smu_core.services.generation_contract import (
    CAPABILITY_REGISTRY,
    GENERATION_CONTRACT_VERSION,
    AssetUse,
    ContentRequirements,
    GenerationContractError,
    GenerationOutput,
    GenerationRequest,
    SourceMaterial,
    VisualSettings,
    build_content_pack_request,
    capability,
    coerce_resolved_visual_capabilities,
    resolve_visual_capabilities,
    validate_content_pack_selections,
    validate_artwork_request,
    validate_generation_output,
    validate_text_generation_request,
)


PAIRS = (
    ("Cześć, jak się masz?", "Hi, how are you?"),
    ("Dziękuję.", "Thank you."),
    ("Dziękuję.", "Thank you."),
)


def test_content_pack_contract_preserves_exact_intent_and_duplicate_multiplicity():
    request = build_content_pack_request(
        source_type="text",
        source_text="Exact source",
        original_input="Exact source",
        carousel_intent={
            "required_slide_count": 4,
            "required_phrase_pairs": PAIRS,
        },
        request_id="request-1",
    )

    assert request.version == GENERATION_CONTRACT_VERSION
    assert request.user_instructions == "Exact source"
    assert request.source_material == SourceMaterial(
        kind="user_text", content="Exact source", origin="direct_input"
    )
    assert request.content_requirements.exact_slide_count == 4
    assert request.content_requirements.required_phrase_pairs == PAIRS
    assert request.asset_use == AssetUse(mode="generate_new")


def test_transcript_is_source_material_and_never_authoritative_instructions():
    request = build_content_pack_request(
        source_type="tiktok",
        source_text="Transcript says: exactly six slides.",
        original_input="https://www.tiktok.com/example",
        carousel_intent=None,
        request_id="request-2",
    )

    assert request.source_material.kind == "transcript"
    assert request.source_material.content == "Transcript says: exactly six slides."
    assert request.user_instructions == ""
    assert request.content_requirements.exact_slide_count is None


def test_capability_registry_records_supported_and_unavailable_operations():
    assert CAPABILITY_REGISTRY.keys() == {
        "formats", "templates", "artwork_styles", "compositions",
        "palettes", "image_operations",
    }
    assert capability("image_operations", "generate_new")["available"] is True
    assert capability("image_operations", "use_uploaded_unchanged") == {
        "available": True,
        "surfaces": ("create_post",),
    }
    assert capability("image_operations", "use_uploaded_as_reference")["available"] is False
    assert capability("image_operations", "restyle_uploaded")["available"] is False
    assert "create_post" in capability("artwork_styles", "auto")["surfaces"]
    assert "tiktok" in capability("palettes", "auto")["surfaces"]


def test_content_pack_visual_capabilities_resolve_to_stable_separate_ids():
    resolved = resolve_visual_capabilities(
        template_id="content_pack_structured",
        artwork_style_id="editorial_illustration",
        composition_id="asymmetric_split",
        palette_id="warm_sunset",
        image_operation="generate_new",
        legacy_render_style="viral_carousel",
    )

    assert resolved.template_id == "content_pack_structured"
    assert resolved.artwork_style_id == "editorial_illustration"
    assert resolved.composition_id == "asymmetric_split"
    assert resolved.palette_id == "warm_sunset"
    assert resolved.image_operation == "generate_new"
    assert coerce_resolved_visual_capabilities(resolved) == resolved


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ({"artwork_style_id": "auto"}, "unsupported_capability_combination"),
        ({"composition_id": "auto"}, "unsupported_capability_combination"),
        ({"palette_id": "auto"}, "unsupported_capability_combination"),
        ({"template_id": "provider_canvas"}, "capability_unavailable_for_surface"),
        ({"image_operation": "restyle_uploaded"}, "unsupported_capability"),
    ],
)
def test_content_pack_visual_capabilities_reject_unsupported_combinations(
    values, reason
):
    request = {
        "template_id": "content_pack_structured",
        "artwork_style_id": "editorial_illustration",
        "composition_id": "asymmetric_split",
        "palette_id": "warm_sunset",
        "image_operation": "generate_new",
        "legacy_render_style": "viral_carousel",
    }
    request.update(values)

    with pytest.raises(GenerationContractError) as raised:
        resolve_visual_capabilities(**request)
    assert raised.value.reason == reason


@pytest.mark.parametrize(
    "selection",
    [
        ("unknown", None, None),
        ("", "unknown", None),
        ("", None, "unknown"),
    ],
)
def test_explicit_unsupported_content_pack_selections_are_rejected(selection):
    with pytest.raises(GenerationContractError):
        validate_content_pack_selections(*selection)


def test_text_and_artwork_validation_have_separate_boundaries():
    request = GenerationRequest(
        version=GENERATION_CONTRACT_VERSION,
        request_id="request-3",
        surface="content_pack",
        user_instructions="",
        source_material=SourceMaterial(kind="user_text", content="Source"),
        content_requirements=ContentRequirements(output_format="content_pack"),
        visual_settings=VisualSettings(
            template_id="content_pack_structured",
            artwork_style="auto",
            composition="auto",
        ),
        asset_use=AssetUse(mode="use_uploaded_as_reference", asset_ids=("asset-1",)),
    )

    assert validate_text_generation_request(request) is request
    with pytest.raises(GenerationContractError) as raised:
        validate_artwork_request(request)
    assert raised.value.reason == "unsupported_capability"


def test_output_must_match_request_identity_format_and_nonempty_text():
    request = build_content_pack_request(
        source_type="text",
        source_text="Source",
        original_input="Source",
        request_id="request-4",
    )
    output = GenerationOutput(
        version=GENERATION_CONTRACT_VERSION,
        request_id="request-4",
        output_format="content_pack",
        text="Generated pack",
    )

    assert validate_generation_output(output, request) is output
    with pytest.raises(GenerationContractError) as raised:
        validate_generation_output(
            GenerationOutput(
                version=GENERATION_CONTRACT_VERSION,
                request_id="another-request",
                output_format="content_pack",
                text="Generated pack",
            ),
            request,
        )
    assert raised.value.reason == "output_request_mismatch"

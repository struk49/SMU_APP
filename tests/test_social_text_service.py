from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from smu_core.services import media, social_text


LICENSE_PATH = social_text.FONT_PATH.with_name("OFL.txt")
POLISH_TEXT_SAMPLES = (
    "Zażółć gęślą jaźń",
    "ą ć ę ł ń ó ś ź ż",
    "Ą Ć Ę Ł Ń Ó Ś Ź Ż",
    "Miłego dnia!",
    "Szczęśliwej podróży!",
    "Często tu przychodzisz?",
    "Wracaj do zdrowia!",
    "Na zdrowie!",
)


def source_bytes(*, mode="RGB", size=(1000, 1000)):
    buffer = BytesIO()
    color = (25, 35, 45, 255) if mode == "RGBA" else (25, 35, 45)
    Image.new(mode, size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def source_bytes_with_color(color, *, size=(1000, 1000)):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_bundled_production_font_and_license_are_present():
    assert social_text.FONT_PATH.is_file()
    assert social_text.FONT_PATH.name == "SMUSocialText-Regular.ttf"
    assert LICENSE_PATH.is_file()
    license_text = LICENSE_PATH.read_text(encoding="utf-8")
    assert license_text
    assert "SIL Open Font License" in license_text
    assert "Version 1.1" in license_text


def test_bundled_font_measures_and_renders_required_polish_text():
    font = ImageFont.truetype(str(social_text.FONT_PATH), size=48)
    image = Image.new("RGB", (1400, 800), "black")
    draw = ImageDraw.Draw(image)

    for index, text in enumerate(POLISH_TEXT_SAMPLES):
        bounds = draw.textbbox((0, 0), text, font=font)
        assert bounds[2] > bounds[0]
        assert bounds[3] > bounds[1]
        draw.text((10, 10 + index * 80), text, font=font, fill="white")


@pytest.mark.parametrize("text", POLISH_TEXT_SAMPLES)
def test_bundled_font_preserves_exact_polish_unicode(monkeypatch, text):
    seen = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, rendered_text, *args, **kwargs):
        seen.append(rendered_text)
        return original(self, position, rendered_text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    social_text.render_social_text(source_bytes(), title=text)
    assert " ".join(seen[0].splitlines()) == text


@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_rendered_bytes_are_openable_png_with_dimensions_preserved(mode):
    output = social_text.render_social_text(source_bytes(mode=mode), title="Exact title")
    with Image.open(BytesIO(output)) as rendered:
        assert rendered.format == "PNG"
        assert rendered.mode == "RGBA"
        assert rendered.size == (1000, 1000)


@pytest.mark.parametrize(
    "title",
    [
        "Exact English spelling preserved",
        "ą ć ę ł ń ó ś ź ż",
        "Ą Ć Ę Ł Ń Ó Ś Ź Ż",
    ],
)
def test_title_text_reaches_pillow_exactly(monkeypatch, title):
    seen = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        seen.append(text)
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    social_text.render_social_text(source_bytes(), title=title)

    assert " ".join(seen[0].splitlines()) == title


def test_title_body_cta_and_brand_render_in_order(monkeypatch):
    seen = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        seen.append((position, text))
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    social_text.render_social_text(
        source_bytes(),
        title="Title",
        body="Body",
        cta="Call now",
        brand="SMU",
    )

    assert [text for _, text in seen] == ["Title", "Body", "Call now", "SMU"]
    assert [position[1] for position, _ in seen] == sorted(
        position[1] for position, _ in seen
    )


def test_empty_optional_fields_are_not_drawn(monkeypatch):
    seen = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        seen.append(text)
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    social_text.render_social_text(
        source_bytes(), title="Title", body="", cta=None, brand="   "
    )
    assert seen == ["Title"]


def test_multiline_wrapping_and_font_fitting_stay_within_bounds(monkeypatch):
    calls = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        calls.append((position, text, kwargs["font"], kwargs["spacing"]))
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    title = "A deliberately long exact headline that must wrap across multiple lines"
    social_text.render_social_text(
        source_bytes(size=(700, 875)),
        title=title,
        body="A measured body line that also wraps safely inside its assigned region.",
    )

    draw = ImageDraw.Draw(Image.new("RGB", (700, 875)))
    for (x, y), text, font, spacing in calls:
        box = draw.multiline_textbbox((x, y), text, font=font, spacing=spacing)
        assert box[0] >= 0
        assert box[1] >= 0
        assert box[2] <= 700
        assert box[3] <= 875
    assert "\n" in calls[0][1]
    assert " ".join(calls[0][1].splitlines()) == title


def test_all_text_blocks_stay_inside_eight_percent_safe_area(monkeypatch):
    calls = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        calls.append((position, text, kwargs))
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    social_text.render_social_text(
        source_bytes(size=(1024, 1024)),
        title="Professional title hierarchy",
        body="Supporting body copy with comfortable deterministic wrapping.",
        cta="Learn more",
        brand="SMU",
    )

    measurement = ImageDraw.Draw(Image.new("RGB", (1024, 1024)))
    safe_margin = round(1024 * 0.08)
    for position, text, kwargs in calls:
        bounds = measurement.multiline_textbbox(
            position,
            text,
            font=kwargs["font"],
            spacing=kwargs["spacing"],
            stroke_width=kwargs["stroke_width"],
        )
        assert bounds[0] >= safe_margin
        assert bounds[1] >= safe_margin
        assert bounds[2] <= 1024 - safe_margin
        assert bounds[3] <= 1024 - safe_margin

    assert calls[0][2]["font"].size > calls[1][2]["font"].size
    assert calls[1][2]["font"].size < calls[2][2]["font"].size
    assert calls[3][2]["font"].size < calls[1][2]["font"].size


@pytest.mark.parametrize(
    "title",
    [
        "Dziękuję / Dziękuję bardzo — Thank you / Thank you very much",
        "Powodzenia — Good luck & Wszystkiego najlepszego — All the best",
    ],
)
def test_long_polish_english_title_wraps_without_clipping(monkeypatch, title):
    seen = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        seen.append((position, text, kwargs))
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    social_text.render_social_text(source_bytes(size=(1024, 1024)), title=title)

    position, rendered, kwargs = seen[0]
    bounds = ImageDraw.Draw(Image.new("RGB", (1024, 1024))).multiline_textbbox(
        position,
        rendered,
        font=kwargs["font"],
        spacing=kwargs["spacing"],
        stroke_width=kwargs["stroke_width"],
    )
    assert "\n" in rendered
    assert " ".join(rendered.splitlines()) == title
    assert bounds[2] <= round(1024 * 0.92)
    assert bounds[3] <= round(1024 * 0.32)
    assert kwargs["font"].size >= social_text.MIN_FONT_SIZE


def test_renderer_uses_consistent_contrast_panels_and_controlled_stroke(monkeypatch):
    panels = []
    text_calls = []
    original_panel = ImageDraw.ImageDraw.rounded_rectangle
    original_text = ImageDraw.ImageDraw.multiline_text

    def capture_panel(self, bounds, *args, **kwargs):
        panels.append((bounds, kwargs))
        return original_panel(self, bounds, *args, **kwargs)

    def capture_text(self, position, text, *args, **kwargs):
        text_calls.append(kwargs)
        return original_text(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "rounded_rectangle", capture_panel)
    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture_text)
    social_text.render_social_text(
        source_bytes(), title="Title", body="Body", cta="CTA"
    )

    assert len(panels) == 3
    assert all(call["stroke_width"] == 1 for call in text_calls)
    assert len({panel[1]["fill"] for panel in panels}) == 1


@pytest.mark.parametrize("layout_role", ["cover", "phrase", "info", "cta"])
def test_role_uses_direct_unified_typography_on_calm_background(monkeypatch, layout_role):
    panels = []
    original = ImageDraw.ImageDraw.rounded_rectangle

    def capture(self, bounds, *args, **kwargs):
        panels.append((bounds, kwargs))
        return original(self, bounds, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "rounded_rectangle", capture)
    social_text.render_social_text(
        source_bytes(),
        title="Designed headline",
        body="Comfortably readable supporting copy.",
        cta="Take one action",
        brand="SMU",
        layout_role=layout_role,
    )

    assert panels == []


@pytest.mark.parametrize(
    "background",
    [
        (248, 248, 248),
        (8, 12, 20),
        None,
    ],
)
def test_unified_contrast_surface_is_readable_on_light_dark_and_busy_backgrounds(
    monkeypatch, background
):
    text_calls = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        text_calls.append(kwargs)
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    if background is None:
        image = Image.new("RGB", (1000, 1000))
        pixels = image.load()
        for y in range(1000):
            for x in range(1000):
                value = 245 if (x // 40 + y // 40) % 2 else 25
                pixels[x, y] = (value, 80, 255 - value)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()
    else:
        image_bytes = source_bytes_with_color(background)

    output = social_text.render_social_text(
        image_bytes,
        title="Readable title",
        body="Readable supporting copy",
        layout_role="info",
    )

    assert output.startswith(b"\x89PNG")
    assert text_calls
    expected_fill = (
        (22, 26, 34, 255)
        if background == (248, 248, 248)
        else (255, 255, 255, 255)
    )
    if background is not None:
        assert all(call["fill"] == expected_fill for call in text_calls)
    assert all(call["stroke_width"] >= 1 for call in text_calls)


def test_role_aware_long_bilingual_copy_never_clips(monkeypatch):
    calls = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        calls.append((position, text, kwargs))
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    social_text.render_social_text(
        source_bytes(size=(1024, 1024)),
        title="Dziękuję bardzo",
        body="Thank you very much — a useful phrase for polite everyday conversations.",
        layout_role="phrase",
    )
    measurement = ImageDraw.Draw(Image.new("RGB", (1024, 1024)))
    margin = round(1024 * 0.08)
    for position, text, kwargs in calls:
        bounds = measurement.multiline_textbbox(
            position,
            text,
            font=kwargs["font"],
            spacing=kwargs["spacing"],
            stroke_width=kwargs["stroke_width"],
        )
        assert bounds[0] >= margin
        assert bounds[1] >= margin
        assert bounds[2] <= 1024 - margin
        assert bounds[3] <= 1024 - margin


def test_cover_role_does_not_render_internal_cover_label(monkeypatch):
    rendered_text = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        rendered_text.append(text)
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    social_text.render_social_text(
        source_bytes(), title="Customer headline", layout_role="cover"
    )

    assert [" ".join(text.splitlines()) for text in rendered_text] == [
        "Customer headline"
    ]


@pytest.mark.parametrize(
    ("layout_role", "expected"),
    [
        ("cover", "hero_left"),
        ("phrase", "split_left"),
        ("info", "editorial_statement"),
        ("cta", "closing"),
    ],
)
def test_role_selects_deterministic_internal_layout(layout_role, expected):
    assert social_text.select_design_layout(layout_role) == expected
    assert social_text.select_design_layout(layout_role) == expected


def test_same_input_renders_identical_bytes_without_random_selection():
    kwargs = {
        "title": "Deterministic title",
        "body": "Supporting copy",
        "layout_role": "info",
        "design_style": "corporate",
    }
    first = social_text.render_social_text(source_bytes(), **kwargs)
    second = social_text.render_social_text(source_bytes(), **kwargs)
    assert first == second


def test_style_tokens_cover_actual_styles_and_unknown_uses_default():
    assert set(social_text.STYLE_TOKENS) == {
        "default",
        "realistic",
        "viral_carousel",
        "luxury",
        "minimal",
        "corporate",
        "pixar",
    }
    assert social_text._style_tokens("unknown") == social_text.STYLE_TOKENS["default"]


def test_existing_styles_change_bounded_design_tokens_deterministically():
    outputs = {
        style: social_text.render_social_text(
            source_bytes(),
            title="One representative headline",
            body="One concise supporting point.",
            layout_role="info",
            design_style=style,
        )
        for style in social_text.STYLE_TOKENS
    }
    assert len(set(outputs.values())) == len(social_text.STYLE_TOKENS)


def test_light_and_dark_regions_choose_opposite_readable_foregrounds():
    box = (80, 80, 520, 700)
    light = Image.open(BytesIO(source_bytes_with_color((245, 245, 245)))).convert("RGBA")
    dark = Image.open(BytesIO(source_bytes_with_color((12, 18, 28)))).convert("RGBA")

    assert social_text._analyze_text_region(light, box)["foreground"] == (22, 26, 34, 255)
    assert social_text._analyze_text_region(dark, box)["foreground"] == (255, 255, 255, 255)


def test_busy_background_uses_one_localized_surface_bounded_to_typography(monkeypatch):
    image = Image.new("RGB", (1000, 1000))
    draw = ImageDraw.Draw(image)
    for y in range(0, 1000, 20):
        for x in range(0, 1000, 20):
            fill = "white" if (x // 20 + y // 20) % 2 else "black"
            draw.rectangle((x, y, x + 19, y + 19), fill=fill)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    surfaces = []
    original = ImageDraw.ImageDraw.rounded_rectangle

    def capture(self, bounds, *args, **kwargs):
        if kwargs.get("fill") in {(8, 12, 20, 112), (248, 248, 244, 130)}:
            surfaces.append(bounds)
        return original(self, bounds, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "rounded_rectangle", capture)
    social_text.render_social_text(
        buffer.getvalue(),
        title="Busy but readable",
        body="Localized protection follows this typography.",
        layout_role="info",
    )

    assert len(surfaces) == 1
    left, top, right, bottom = surfaces[0]
    assert (right - left) * (bottom - top) / 1_000_000 < 0.35
    assert bottom < round(1000 * 0.78)


def test_role_aware_layouts_draw_no_decorative_dash_or_accent(monkeypatch):
    accents = []
    monkeypatch.setattr(
        ImageDraw.ImageDraw,
        "line",
        lambda self, *args, **kwargs: accents.append((args, kwargs)),
    )

    for role in social_text.LAYOUT_ROLES:
        social_text.render_social_text(
            source_bytes(), title="Bold exact headline", layout_role=role
        )

    assert accents == []


def test_named_layout_variants_create_deterministic_position_variety(monkeypatch):
    calls = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        calls.append((position, kwargs["align"]))
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    positions = {}
    for variant in ("split_left", "split_right", "visual_focus"):
        calls.clear()
        social_text.render_social_text(
            source_bytes(),
            title="Exact headline",
            layout_role="info",
            layout_variant=variant,
        )
        positions[variant] = calls[0]

    assert len(set(positions.values())) == 3
    assert positions["split_left"][1] == "left"
    assert positions["split_right"][1] == "right"


def test_brand_is_never_injected_and_supplied_brand_still_renders(monkeypatch):
    rendered = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        rendered.append(" ".join(text.splitlines()))
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    social_text.render_social_text(
        source_bytes(), title="No default brand", layout_role="cover"
    )
    assert rendered == ["No default brand"]

    rendered.clear()
    social_text.render_social_text(
        source_bytes(),
        title="Supplied brand",
        brand="Customer Brand",
        layout_role="cover",
    )
    assert rendered == ["Supplied brand", "Customer Brand"]


def test_balanced_cover_wrapping_preserves_exact_words(monkeypatch):
    rendered = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        rendered.append(text)
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    title = "Different Platforms. Different Content."
    social_text.render_social_text(source_bytes(), title=title, layout_role="cover")

    assert " ".join(rendered[0].splitlines()) == title
    assert 2 <= len(rendered[0].splitlines()) <= 4


@pytest.mark.parametrize(
    "colour",
    [(250, 250, 250), (180, 180, 180), (28, 28, 28), (4, 4, 4)],
)
def test_uniform_regions_use_direct_typography_without_fallback(colour):
    image = Image.open(BytesIO(source_bytes_with_color(colour))).convert("RGBA")
    analysis = social_text._analyze_text_region(image, (80, 80, 540, 790))

    assert analysis["luminance_deviation"] == 0
    assert analysis["local_variation"] == 0
    assert analysis["busy"] is False


def test_local_transition_density_distinguishes_calm_subject_from_busy_texture():
    calm = Image.new("RGB", (1000, 1000), (238, 232, 218))
    calm_draw = ImageDraw.Draw(calm)
    calm_draw.ellipse((310, 220, 780, 690), fill=(130, 62, 48))
    busy = Image.new("RGB", (1000, 1000))
    busy_draw = ImageDraw.Draw(busy)
    for y in range(0, 1000, 20):
        for x in range(0, 1000, 20):
            fill = (245, 245, 245) if (x // 20 + y // 20) % 2 else (10, 10, 10)
            busy_draw.rectangle((x, y, x + 19, y + 19), fill=fill)

    calm_analysis = social_text._analyze_text_region(calm, (80, 80, 820, 820))
    busy_analysis = social_text._analyze_text_region(busy, (80, 80, 820, 820))

    assert calm_analysis["busy"] is False
    assert busy_analysis["busy"] is True
    assert busy_analysis["local_variation"] > calm_analysis["local_variation"]


def test_clean_cta_uses_direct_typography_without_card(monkeypatch):
    surfaces = []
    original = ImageDraw.ImageDraw.rounded_rectangle

    def capture(self, bounds, *args, **kwargs):
        surfaces.append((bounds, kwargs.get("fill")))
        return original(self, bounds, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "rounded_rectangle", capture)
    social_text.render_social_text(
        source_bytes_with_color((22, 28, 38)),
        title="Build the next post",
        body="Start with one clear idea.",
        layout_role="cta",
    )

    assert surfaces == []


def test_hero_headline_is_materially_larger_than_editorial(monkeypatch):
    calls = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        calls.append(kwargs["font"].size)
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    social_text.render_social_text(
        source_bytes(size=(1024, 1024)), title="Make content distinct", layout_role="cover"
    )
    hero_size = calls[0]
    calls.clear()
    social_text.render_social_text(
        source_bytes(size=(1024, 1024)), title="Make content distinct", layout_role="info"
    )

    assert hero_size >= 85
    assert hero_size > calls[0]


def test_style_tokens_create_substantive_hierarchy_and_spacing_differences():
    tokens = social_text.STYLE_TOKENS

    assert tokens["viral_carousel"]["headline"] > tokens["default"]["headline"]
    assert tokens["viral_carousel"]["region_width"] > tokens["default"]["region_width"]
    assert tokens["luxury"]["gap"] > tokens["viral_carousel"]["gap"]
    assert tokens["luxury"]["region_width"] < tokens["viral_carousel"]["region_width"]
    assert tokens["minimal"]["surface_alpha"] < tokens["default"]["surface_alpha"]
    assert tokens["realistic"]["surface_alpha"] < tokens["default"]["surface_alpha"]
    assert tokens["corporate"]["gap"] > tokens["viral_carousel"]["gap"]
    assert tokens["pixar"]["headline"] > tokens["default"]["headline"]


def test_viral_headline_is_larger_than_default_when_safe(monkeypatch):
    calls = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        calls.append(kwargs["font"].size)
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    sizes = {}
    for style in ("default", "viral_carousel"):
        calls.clear()
        social_text.render_social_text(
            source_bytes(size=(1024, 1024)),
            title="Create boldly",
            layout_role="cover",
            design_style=style,
        )
        sizes[style] = calls[0]

    assert sizes["viral_carousel"] > sizes["default"]


def test_viral_carousel_uses_deterministic_dark_designed_canvas():
    source = source_bytes_with_color((220, 70, 60), size=(1024, 1024))
    kwargs = {
        "title": "START WITH THE SOURCE",
        "body": "Find the strongest idea.",
        "layout_role": "cover",
        "layout_variant": "hero_left",
        "design_style": "viral_carousel",
    }

    first = social_text.render_social_text(source, **kwargs)
    second = social_text.render_social_text(source, **kwargs)
    with Image.open(BytesIO(first)) as rendered:
        assert rendered.getpixel((0, 0)) == (9, 18, 34, 255)
        assert rendered.getpixel((900, 300)) != (9, 18, 34, 255)

    assert first == second


def test_dense_role_copy_uses_safe_wide_fallback_without_truncation(monkeypatch):
    rendered_text = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        rendered_text.append(text)
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    title = "UNDERSTAND THE SOURCE BEFORE BUILDING DISTINCT PLATFORM CONTENT"
    body = "Find the strongest grounded idea, then adapt its structure for every channel."

    output = social_text.render_social_text(
        source_bytes(size=(1024, 1024)),
        title=title,
        body=body,
        layout_role="phrase",
        layout_variant="split_left",
        design_style="viral_carousel",
    )

    assert output.startswith(b"\x89PNG")
    assert " ".join(rendered_text[0].splitlines()) == title
    assert " ".join(rendered_text[1].splitlines()) == body


def test_output_remains_compatible_with_existing_jpeg_normalization():
    rendered = social_text.render_social_text(source_bytes(), title="Title")
    normalized = media.normalize_image_to_jpeg(rendered)
    with Image.open(BytesIO(normalized["bytes"])) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (1000, 1000)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"title": ""}, "title_required"),
        ({"title": "x" * (social_text.TEXT_LIMITS["title"] + 1)}, "text_limit_exceeded"),
        ({"title": "valid", "body": "x" * (social_text.TEXT_LIMITS["body"] + 1)}, "text_limit_exceeded"),
        ({"title": "valid", "cta": "x" * (social_text.TEXT_LIMITS["cta"] + 1)}, "text_limit_exceeded"),
        ({"title": "valid", "brand": "x" * (social_text.TEXT_LIMITS["brand"] + 1)}, "text_limit_exceeded"),
        ({"title": "bad\ud800text"}, "invalid_text"),
        ({"title": "valid", "layout": "unknown"}, "unsupported_layout"),
    ],
)
def test_invalid_inputs_raise_safe_categories(kwargs, reason):
    supplied_copy = next(
        (value for value in kwargs.values() if isinstance(value, str) and len(value) > 20),
        None,
    )
    with pytest.raises(social_text.SocialTextRenderError) as raised:
        social_text.render_social_text(source_bytes(), **kwargs)
    assert raised.value.reason == reason
    if supplied_copy:
        assert supplied_copy not in str(raised.value)


def test_layout_variant_requires_role_and_known_deterministic_variant():
    with pytest.raises(social_text.SocialTextRenderError) as missing_role:
        social_text.render_social_text(
            source_bytes(), title="Title", layout_variant="visual_focus"
        )
    assert missing_role.value.reason == "unsupported_layout_variant"

    with pytest.raises(social_text.SocialTextRenderError) as unknown_variant:
        social_text.render_social_text(
            source_bytes(),
            title="Title",
            layout_role="info",
            layout_variant="random",
        )
    assert unknown_variant.value.reason == "unsupported_layout_variant"


def test_unbreakable_text_fails_with_safe_dedicated_exception():
    supplied_copy = "W" * social_text.TEXT_LIMITS["title"]
    with pytest.raises(social_text.SocialTextRenderError) as raised:
        social_text.render_social_text(source_bytes(size=(320, 400)), title=supplied_copy)
    assert raised.value.reason == "text_does_not_fit"
    assert supplied_copy not in str(raised.value)


@pytest.mark.parametrize("size", [(social_text.MAX_DIMENSION + 1, 10), (5000, 4000)])
def test_dimension_and_pixel_safeguards(size):
    with pytest.raises(social_text.SocialTextRenderError) as raised:
        social_text.render_social_text(source_bytes(size=size), title="Title")
    assert raised.value.reason == "image_dimensions_unsupported"


def test_input_byte_limit_is_checked_before_pillow_open():
    oversized = b"x" * (social_text.MAX_INPUT_BYTES + 1)
    with pytest.raises(social_text.SocialTextRenderError) as raised:
        social_text.render_social_text(oversized, title="Title")
    assert raised.value.reason == "image_too_large"


def test_font_path_is_internal_and_not_caller_controlled():
    with pytest.raises(TypeError):
        social_text.render_social_text(
            source_bytes(), title="Title", font_path="https://example.test/font.ttf"
        )


def test_missing_production_font_fails_without_exposing_copy(monkeypatch, caplog):
    supplied_copy = "Private customer headline"
    monkeypatch.setattr(
        social_text,
        "FONT_PATH",
        social_text.FONT_PATH.with_name("does-not-exist.ttf"),
    )
    with pytest.raises(social_text.SocialTextRenderError) as raised:
        social_text.render_social_text(source_bytes(), title=supplied_copy)
    assert raised.value.reason == "font_unavailable"
    assert supplied_copy not in str(raised.value)
    assert supplied_copy not in caplog.text


def test_renderer_performs_no_file_writes_or_remote_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("renderer attempted an external operation")

    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    output = social_text.render_social_text(source_bytes(), title="Offline title")
    assert output.startswith(b"\x89PNG")


@pytest.mark.parametrize("unsupported", ["\ufffd", "\U0001f600"])
def test_unsupported_font_character_fails_without_copy_leak(unsupported, caplog):
    supplied_copy = f"Private prompt {unsupported} and caption"
    with pytest.raises(social_text.SocialTextRenderError) as raised:
        social_text.render_social_text(source_bytes(), title=supplied_copy)

    assert raised.value.reason == "unsupported_text_character"
    assert supplied_copy not in str(raised.value)
    assert supplied_copy not in caplog.text


def test_role_title_hierarchy_and_cta_position_are_deterministic(monkeypatch):
    calls = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        calls.append((position, kwargs["font"].size))
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    sizes = {}
    positions = {}
    for role in ("cover", "phrase", "info", "cta"):
        calls.clear()
        social_text.render_social_text(
            source_bytes(), title="Short title", layout_role=role
        )
        positions[role], sizes[role] = calls[0]

    assert sizes["cover"] > sizes["phrase"] > sizes["info"]
    assert positions["cta"][1] > positions["info"][1]


def test_phrase_title_is_stronger_than_translation_body(monkeypatch):
    sizes = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        sizes.append(kwargs["font"].size)
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    social_text.render_social_text(
        source_bytes(),
        title="Chleb",
        body="Bread. A staple on every table.",
        layout_role="phrase",
    )

    assert sizes[0] > sizes[1]


@pytest.mark.parametrize("layout_role", ["cover", "phrase", "info", "cta"])
def test_every_role_keeps_text_inside_safe_area(monkeypatch, layout_role):
    calls = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, position, text, *args, **kwargs):
        calls.append((position, text, kwargs))
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    social_text.render_social_text(
        source_bytes(size=(1024, 1024)),
        title="Role title",
        body="Supporting information",
        cta="Learn more",
        brand="SMU",
        layout_role=layout_role,
    )

    draw = ImageDraw.Draw(Image.new("RGB", (1024, 1024)))
    safe_margin = round(1024 * 0.08)
    for position, text, kwargs in calls:
        bounds = draw.multiline_textbbox(
            position,
            text,
            font=kwargs["font"],
            spacing=kwargs["spacing"],
            stroke_width=kwargs["stroke_width"],
        )
        assert bounds[0] >= safe_margin
        assert bounds[1] >= safe_margin
        assert bounds[2] <= 1024 - safe_margin
        assert bounds[3] <= 1024 - safe_margin


def test_bundled_variable_font_exposes_real_weight_instances():
    sample = "Premium typography"
    widths = {
        weight: social_text._load_font(64, weight).getlength(sample)
        for weight in social_text.FONT_WEIGHTS
    }

    assert widths["regular"] != widths["black"]
    assert widths["medium"] != widths["extrabold"]


def test_exact_polish_punctuation_emphasis_is_rendered_unchanged(monkeypatch):
    captured = []
    original = ImageDraw.ImageDraw.text

    def capture(self, position, text, *args, **kwargs):
        captured.append(text)
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    title = "DZIĘKUJĘ — TO WAŻNE!"
    emphasis = "TO WAŻNE!"
    output = social_text.render_social_text(
        source_bytes(),
        title=title,
        emphasis={"text": emphasis, "role": "accent"},
        layout_role="cover",
        design_style="viral_carousel",
    )

    assert output.startswith(b"\x89PNG")
    assert emphasis in captured


@pytest.mark.parametrize(
    "emphasis",
    [
        {"text": "Title", "role": "accent", "color": "#ffffff"},
        {"text": "Title", "role": "accent", "font": "Comic Sans"},
        {"text": "Title", "role": "accent", "size": 200},
    ],
)
def test_emphasis_rejects_arbitrary_visual_styling(emphasis):
    with pytest.raises(social_text.SocialTextRenderError) as raised:
        social_text.render_social_text(
            source_bytes(), title="Title", emphasis=emphasis
        )

    assert raised.value.reason == "invalid_typography_metadata"


def test_typography_only_viral_slide_does_not_prepare_artwork(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("typography-only treatment prepared artwork")

    monkeypatch.setattr(social_text.ImageOps, "fit", forbidden)
    output = social_text.render_social_text(
        source_bytes(),
        title="Typography leads",
        layout_role="info",
        design_style="viral_carousel",
        visual_treatment="typography_only",
    )

    assert output.startswith(b"\x89PNG")


def test_typography_only_has_no_automatic_corner_circle(monkeypatch):
    ellipse_calls = []
    original = social_text.ImageDraw.ImageDraw.ellipse

    def capture(self, *args, **kwargs):
        ellipse_calls.append((args, kwargs))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(social_text.ImageDraw.ImageDraw, "ellipse", capture)
    social_text.render_social_text(
        source_bytes(),
        title="Typography leads",
        layout_role="info",
        design_style="viral_carousel",
        visual_treatment="typography_only",
    )

    assert ellipse_calls == []


def test_illustration_viral_slide_uses_supplied_artwork(monkeypatch):
    calls = []
    original = social_text.ImageOps.contain

    def capture(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(social_text.ImageOps, "contain", capture)
    social_text.render_social_text(
        source_bytes(),
        title="Artwork supports the idea",
        layout_role="cover",
        design_style="viral_carousel",
        visual_treatment="illustration",
    )

    assert calls


def test_semantic_visual_treatments_are_distinct_and_deterministic():
    outputs = []
    for treatment in (
        "typography_only", "illustration", "diagram", "process", "comparison",
        "visual_focus",
    ):
        first = social_text.render_social_text(
            source_bytes(),
            title="One exact headline",
            layout_role="info",
            design_style="viral_carousel",
            visual_treatment=treatment,
        )
        second = social_text.render_social_text(
            source_bytes(),
            title="One exact headline",
            layout_role="info",
            design_style="viral_carousel",
            visual_treatment=treatment,
        )
        assert first == second
        outputs.append(first)

    assert len(set(outputs)) == len(outputs)


def test_short_typography_only_headline_uses_stronger_scale(monkeypatch):
    title_sizes = []
    original = social_text._prepare_composition_block

    def capture(draw, text, box, **kwargs):
        if text == "Short statement":
            title_sizes.append(kwargs["start_size"])
        return original(draw, text, box, **kwargs)

    monkeypatch.setattr(social_text, "_prepare_composition_block", capture)
    common = {
        "title": "Short statement",
        "layout_role": "info",
        "layout_variant": "editorial_statement",
        "design_style": "viral_carousel",
    }
    social_text.render_social_text(
        source_bytes(), **common, visual_treatment="typography_only"
    )
    social_text.render_social_text(
        source_bytes(), **common, visual_treatment="illustration"
    )

    assert title_sizes[0] > title_sizes[1]


@pytest.mark.parametrize(
    "layout_variant",
    [
        "hero_left", "split_left", "split_right", "visual_focus",
        "editorial_statement", "closing",
    ],
)
def test_viral_composition_zones_protect_text_from_artwork(layout_variant):
    zones = social_text.viral_composition_zones(
        1024, 1024, layout_variant, "illustration"
    )
    text_left, text_top, text_right, text_bottom = zones["text_rect"]
    art_left, art_top, art_right, art_bottom = zones["art_rect"]

    assert zones["overlap_allowed"] is False
    assert (
        text_right <= art_left
        or art_right <= text_left
        or text_bottom <= art_top
        or art_bottom <= text_top
    )
    support = zones["support_rect"]
    assert text_left <= support[0] < support[2] <= text_right
    assert text_top <= support[1] < support[3] <= text_bottom


def test_artwork_is_clipped_outside_protected_text_rect():
    source = Image.new("RGBA", (1600, 700), (255, 0, 180, 255))
    for layout_variant in (
        "hero_left", "split_left", "split_right", "visual_focus",
        "editorial_statement", "closing",
    ):
        canvas = social_text._build_designed_carousel_canvas(
            source, layout_variant, "illustration"
        )
        text_rect = social_text.viral_composition_zones(
            *canvas.size, layout_variant, "illustration"
        )["text_rect"]
        colours = canvas.crop(text_rect).getcolors(maxcolors=10)
        assert colours == [
            ((text_rect[2] - text_rect[0]) * (text_rect[3] - text_rect[1]), (9, 18, 34, 255))
        ]


def test_crop_modes_and_focal_anchors_are_deterministic():
    illustration = social_text.viral_composition_zones(
        1024, 1024, "split_left", "illustration"
    )
    visual_focus = social_text.viral_composition_zones(
        1024, 1024, "visual_focus", "visual_focus"
    )
    repeated = social_text.viral_composition_zones(
        1024, 1024, "visual_focus", "visual_focus"
    )

    assert illustration["crop_mode"] == "contain"
    assert visual_focus["crop_mode"] == "cover"
    assert illustration["anchor"] == (0.72, 0.50)
    assert visual_focus["anchor"] == (0.50, 0.46)
    assert visual_focus == repeated


def test_closing_artwork_zone_remains_secondary():
    zones = social_text.viral_composition_zones(
        1024, 1024, "closing", "illustration"
    )
    left, top, right, bottom = zones["art_rect"]

    assert (right - left) * (bottom - top) < 0.30 * 1024 * 1024


def test_invalid_viral_artwork_falls_back_to_safe_local_canvas():
    output = social_text.render_social_text(
        b"not an image",
        title="Safe fallback",
        layout_role="info",
        layout_variant="split_left",
        design_style="viral_carousel",
        visual_treatment="illustration",
    )

    assert output.startswith(b"\x89PNG")


def test_mixed_headline_runs_flow_sequentially_without_overlap():
    draw = ImageDraw.Draw(Image.new("RGBA", (1000, 1000)))
    title = "Jeden pomysł, wiele możliwości"
    emphasis = {"text": "wiele możliwości", "role": "accent"}
    block = social_text._fit_mixed_headline(
        draw,
        title,
        emphasis,
        (80, 80, 650, 650),
        max_lines=4,
        start_size=120,
        min_size=52,
        align="left",
        stroke_width=2,
    )

    flattened = " ".join(
        "".join(value for value, _, _ in line) for line in block["mixed_lines"]
    )
    assert flattened == title
    emphasized_text = " ".join(
        "".join(value for value, _, emphasized in line if emphasized)
        for line in block["mixed_lines"]
        if any(emphasized for _, _, emphasized in line)
    )
    assert emphasized_text == emphasis["text"]
    for line, expected_width in zip(block["mixed_lines"], block["line_widths"]):
        widths = [draw.textlength(value, font=font) for value, font, _ in line]
        assert sum(widths) == pytest.approx(expected_width)
        assert all(width > 0 for width in widths)


def test_mixed_headline_preserves_spaces_punctuation_and_polish_exactly():
    draw = ImageDraw.Draw(Image.new("RGBA", (1400, 800)))
    title = "Zażółć gęślą jaźń — jeden pomysł, wiele możliwości!"
    block = social_text._fit_mixed_headline(
        draw,
        title,
        {"text": "jeden pomysł,", "role": "accent"},
        (40, 40, 1360, 760),
        max_lines=3,
        start_size=90,
        min_size=52,
        align="left",
        stroke_width=2,
    )

    reconstructed = " ".join(
        "".join(value for value, _, _ in line) for line in block["mixed_lines"]
    )
    assert reconstructed == title


def test_long_polish_mixed_headline_uses_safe_measured_fallback():
    output = social_text.render_social_text(
        source_bytes(),
        title="Jeden pomysł, wiele możliwości dla każdego ważnego kanału",
        emphasis={"text": "wiele możliwości", "role": "accent"},
        layout_role="info",
        layout_variant="editorial_statement",
        design_style="viral_carousel",
        visual_treatment="typography_only",
    )

    assert output.startswith(b"\x89PNG")


def test_viral_diagram_headline_keeps_mobile_readable_minimum(monkeypatch):
    sizes = []
    original = social_text._draw_mixed_headline

    def capture(draw, block, emphasis, **kwargs):
        sizes.append(block["font"].size)
        return original(draw, block, emphasis, **kwargs)

    monkeypatch.setattr(social_text, "_draw_mixed_headline", capture)
    social_text.render_social_text(
        source_bytes(size=(1024, 1024)),
        title="ONE SOURCE. MANY DESTINATIONS.",
        body="A clear supporting thought.",
        emphasis={"text": "MANY DESTINATIONS.", "role": "accent"},
        layout_role="info",
        layout_variant="split_right",
        design_style="viral_carousel",
        visual_treatment="diagram",
    )

    assert sizes and sizes[0] >= round(1024 * 0.052)

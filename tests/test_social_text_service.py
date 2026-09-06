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

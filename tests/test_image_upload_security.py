"""Harmless documents and local rasters; no converters or external services."""

from io import BytesIO
from unittest.mock import Mock

import pytest
from PIL import EpsImagePlugin, Image
from werkzeug.datastructures import FileStorage

from conftest import create_user, login
from smu_core.models import Post, UserUsage
from smu_core.services import image_validation, media, social_text


def raster(fmt="PNG", size=(32, 32)):
    output = BytesIO()
    Image.new("RGB", size, "green").save(output, format=fmt)
    return output.getvalue()


# Headers only, no executable PostScript or exploit payloads.
DOCUMENTS = [
    b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n%%EOF\n",
    b"%!PS-Adobe-3.0\n%%EOF\n",
    b"%PDF-1.7\n%%EOF\n",
    b"\xc5\xd0\xd3\xc6" + b"\x00" * 28,
]


@pytest.fixture(autouse=True)
def forbidden_ghostscript(monkeypatch):
    dispatch = Mock(side_effect=AssertionError("Ghostscript must never dispatch"))
    monkeypatch.setattr(EpsImagePlugin, "Ghostscript", dispatch)
    yield dispatch
    dispatch.assert_not_called()


@pytest.mark.parametrize("data", DOCUMENTS)
@pytest.mark.parametrize("name", ["photo.jpg", "photo.png", "photo.gif", "photo.webp"])
def test_document_rejected_before_any_decoder_or_cloudinary(data, name, monkeypatch):
    upload = Mock()
    decoder = Mock(side_effect=AssertionError("Document must fail before decoding"))
    monkeypatch.setattr(image_validation.Image, "open", decoder)
    for force_jpeg in (False, True):
        file = FileStorage(stream=BytesIO(data), filename=name, content_type="image/png")
        with pytest.raises(image_validation.ImageValidationError, match="not supported"):
            media.upload_to_cloudinary(file, force_jpeg=force_jpeg, upload_func=upload)
    upload.assert_not_called()
    decoder.assert_not_called()


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "GIF", "WEBP"])
def test_valid_rasters_are_unchanged_for_raw_upload_and_safe_for_instagram(fmt):
    data = raster(fmt)
    source = BytesIO(data)
    source.seek(3)
    assert image_validation.validate_raster_image(source) == data
    assert source.tell() == 0
    upload = Mock(return_value={"secure_url": "https://cdn.test/image"})
    media.upload_to_cloudinary(source, upload_func=upload)
    assert upload.call_args.args[0].read() == data
    normalized = media.normalize_image_to_jpeg(data)
    with Image.open(BytesIO(normalized["bytes"]), formats=("JPEG",)) as image:
        assert image.mode == "RGB" and image.size == (32, 32)
    assert social_text.render_social_text(raster(fmt, (320, 400)), title="Herbs").startswith(b"\x89PNG")


@pytest.mark.parametrize("data", DOCUMENTS + [b"", b"broken image"])
def test_social_text_rejects_invalid_artwork_without_canvas_substitution(data):
    with pytest.raises(social_text.SocialTextRenderError) as raised:
        social_text.render_social_text(data, title="Title", layout_role="info",
            layout_variant="split_left", design_style="viral_carousel")
    assert raised.value.reason == "invalid_image"


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "GIF", "WEBP"])
def test_truncated_rasters_rejected(fmt):
    data = raster(fmt)
    with pytest.raises(image_validation.ImageValidationError):
        image_validation.validate_raster_image(data[:len(data) // 2])


def test_bytes_limit_precedes_decoder(monkeypatch):
    monkeypatch.setattr(image_validation, "MAX_IMAGE_BYTES", 64)
    decoder = Mock(side_effect=AssertionError("No decoding oversized bytes"))
    monkeypatch.setattr(image_validation.Image, "open", decoder)
    with pytest.raises(image_validation.ImageValidationError, match="20 MiB"):
        image_validation.validate_raster_image(b"x" * 65)
    decoder.assert_not_called()


def test_pixel_budget_precedes_pixel_loading(monkeypatch):
    data = raster(size=(40, 40))
    monkeypatch.setattr(image_validation, "MAX_IMAGE_PIXELS", 1000)
    load = Mock(side_effect=AssertionError("No pixel allocation"))
    monkeypatch.setattr(Image.Image, "load", load)
    with pytest.raises(image_validation.ImageValidationError, match="decoded pixels"):
        image_validation.validate_raster_image(data)
    load.assert_not_called()


def test_animated_frame_budget_is_cumulative(monkeypatch):
    output = BytesIO()
    frames = [Image.new("RGB", (10, 10), color) for color in ("red", "green", "blue")]
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:])
    monkeypatch.setattr(image_validation, "MAX_IMAGE_PIXELS", 250)
    with pytest.raises(image_validation.ImageValidationError, match="across all frames"):
        image_validation.validate_raster_image(output.getvalue())


@pytest.mark.parametrize("platform", ["instagram", "facebook"])
@pytest.mark.parametrize("carousel", [False, True])
@pytest.mark.parametrize("bad", DOCUMENTS + [b"broken image", "truncated", "oversized", "pixels"])
def test_rejected_batch_has_no_upload_rows_or_credit_activity(
    client, app, module, monkeypatch, platform, carousel, bad
):
    login(client, create_user(module))
    helpers = app.extensions["smu_post_create_helpers"]
    upload = Mock(side_effect=AssertionError("Rejected batch must not upload"))
    reserve = Mock(side_effect=AssertionError("Uploads never reserve credits"))
    release = Mock(side_effect=AssertionError("No credits to release"))
    monkeypatch.setitem(helpers, "upload_to_cloudinary", upload)
    monkeypatch.setitem(helpers, "reserve_ai_image_credits", reserve)
    monkeypatch.setitem(helpers, "release_ai_image_credits", release)
    valid = raster()
    if bad == "truncated":
        bad = valid[:len(valid) // 2]
    elif bad == "oversized":
        monkeypatch.setattr(image_validation, "MAX_IMAGE_BYTES", len(valid))
        bad = b"x" * (len(valid) + 1)
    elif bad == "pixels":
        monkeypatch.setattr(image_validation, "MAX_IMAGE_PIXELS", 1024)
        bad = raster(size=(33, 33))
    files = [(BytesIO(bad), "disguised.png", "image/png")]
    if carousel:
        files.insert(0, (BytesIO(valid), "valid.png", "image/png"))
    data = {"media": files, "caption": "Preserve caption", "prompt": "Preserve brief",
            "platforms": [platform]}
    if carousel:
        data["make_carousel"] = "on"
    usage_before = UserUsage.query.count()
    add = Mock(side_effect=AssertionError("Rejected uploads must not add rows"))
    monkeypatch.setattr(module.db.session, "add", add)
    response = client.post("/create", data=data, content_type="multipart/form-data")
    assert response.status_code == 422
    html = response.get_data(as_text=True)
    assert "Preserve caption" in html and "Preserve brief" in html
    assert Post.query.count() == 0 and UserUsage.query.count() == usage_before
    upload.assert_not_called()
    reserve.assert_not_called()
    release.assert_not_called()
    add.assert_not_called()


@pytest.mark.parametrize("platform", ["instagram", "facebook"])
@pytest.mark.parametrize("carousel", [False, True])
@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "GIF", "WEBP"])
def test_valid_upload_routes(client, app, module, monkeypatch, platform, carousel, fmt):
    login(client, create_user(module))
    uploads = []
    def upload(file, force_jpeg=False):
        uploads.append((file.read(), force_jpeg))
        return {"secure_url": "https://cdn.test/image.jpg"}
    monkeypatch.setitem(app.extensions["smu_post_create_helpers"], "upload_to_cloudinary", upload)
    original = raster(fmt)
    count = 2 if carousel else 1
    data = {"media": [(BytesIO(original), f"image{n}.{fmt.lower()}") for n in range(count)],
            "platforms": [platform], "caption": "Separate caption"}
    if carousel:
        data["make_carousel"] = "on"
    response = client.post("/create", data=data, content_type="multipart/form-data")
    assert response.status_code == 302
    assert uploads == [(original, platform == "instagram")] * count
    assert Post.query.count() == count
    assert UserUsage.query.count() == 0


def test_raster_allowlist_is_passed_to_every_pillow_open(monkeypatch):
    data = raster(size=(320, 400))
    original = Image.open
    calls = []
    def restricted(*args, **kwargs):
        calls.append(kwargs.get("formats"))
        assert kwargs.get("formats") == image_validation.RASTER_FORMATS
        return original(*args, **kwargs)
    monkeypatch.setattr(Image, "open", restricted)
    media.normalize_image_to_jpeg(data)
    social_text.render_social_text(data, title="Herbs")
    assert len(calls) >= 6


def test_supported_filename_does_not_allow_other_pillow_formats():
    with pytest.raises(image_validation.ImageValidationError, match="unsupported or damaged"):
        media.upload_to_cloudinary(FileStorage(stream=BytesIO(raster("BMP")), filename="photo.png"))


@pytest.mark.parametrize("data", DOCUMENTS)
def test_document_cannot_bypass_validation_as_a_video(data):
    upload = Mock()
    with pytest.raises(image_validation.ImageValidationError, match="not supported"):
        media.upload_to_cloudinary(FileStorage(stream=BytesIO(data), filename="video.mp4"), upload_func=upload)
    upload.assert_not_called()


def test_valid_video_bypasses_raster_decoder(monkeypatch):
    source = FileStorage(stream=BytesIO(b"video fixture"), filename="video.mp4")
    decoder = Mock(side_effect=AssertionError("Do not raster-decode videos"))
    monkeypatch.setattr(image_validation.Image, "open", decoder)
    upload = Mock(return_value={"secure_url": "https://cdn.test/video.mp4"})
    media.upload_to_cloudinary(source, upload_func=upload)
    assert upload.call_args.args[0].read() == b"video fixture"
    decoder.assert_not_called()


def test_truncated_gif_without_trailer_is_rejected():
    data = raster("GIF")
    assert data.endswith(b";")
    with pytest.raises(image_validation.ImageValidationError, match="incomplete"):
        image_validation.validate_raster_image(data[:-1])


def test_nonbinary_and_unseekable_sources_have_safe_errors():
    from io import StringIO
    for source in (StringIO("not binary"), object()):
        with pytest.raises(image_validation.ImageValidationError, match="Could not read"):
            image_validation.validate_raster_image(source)


def test_oversized_gif_frame_rejected_before_disposal_allocation(monkeypatch):
    output = BytesIO()
    frames = [Image.new("RGB", (10, 10), color) for color in ("red", "green")]
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], disposal=2)
    data = output.getvalue()
    descriptor = b",\x00\x00\x00\x00\x0a\x00\x0a\x00"
    index = data.rindex(descriptor)
    # Harmless oversized frame metadata, not an exploit or compressed pixel bomb.
    data = data[:index + 5] + (5000).to_bytes(2, "little") + (4000).to_bytes(2, "little") + data[index + 9:]
    original_fill = Image.core.fill
    calls = []
    def fill(mode, size, color):
        calls.append(size)
        assert size[0] * size[1] <= image_validation.MAX_IMAGE_PIXELS
        return original_fill(mode, size, color)
    monkeypatch.setattr(Image.core, "fill", fill)
    with pytest.raises(image_validation.ImageValidationError) as raised:
        image_validation.validate_raster_image(data)
    assert raised.value.reason == "image_dimensions_unsupported"
    assert (5000, 4000) not in calls

from io import BytesIO

import pytest
from PIL import Image

import app as smu_app
from smu_core.services import media


def image_bytes(mode="RGB", color=(20, 40, 60), size=(8, 6), image_format="PNG"):
    buffer = BytesIO()
    image = Image.new(mode, size, color)
    image.save(buffer, format=image_format)
    return buffer.getvalue()


def read_image(raw_bytes):
    return Image.open(BytesIO(raw_bytes))


def test_media_service_exports_and_app_wrappers_remain_callable(module):
    moved_helpers = [
        "get_file_type",
        "normalize_image_to_jpeg",
        "log_image_normalization_diagnostics",
        "upload_jpeg_to_cloudinary",
        "upload_to_cloudinary",
        "make_instagram_safe_url",
        "get_url_path_extension",
    ]

    for name in moved_helpers:
        assert callable(getattr(media, name))
        assert callable(getattr(module, name))


def test_app_wrappers_delegate_to_media_service(monkeypatch):
    calls = {}

    def fake_upload_jpeg_to_cloudinary(source, **kwargs):
        calls["jpeg_upload"] = kwargs
        return {"secure_url": "https://cdn.test/jpeg.jpg"}

    def fake_upload_to_cloudinary(source, **kwargs):
        calls["upload"] = kwargs
        return {"secure_url": "https://cdn.test/file.jpg"}

    monkeypatch.setattr(
        smu_app.media_service,
        "get_file_type",
        lambda filename: calls.setdefault("get_file_type", filename) or "image",
    )
    monkeypatch.setattr(
        smu_app.media_service,
        "normalize_image_to_jpeg",
        lambda source: calls.setdefault("normalize", source) or {"bytes": b""},
    )
    monkeypatch.setattr(
        smu_app.media_service,
        "log_image_normalization_diagnostics",
        lambda result, **kwargs: calls.setdefault("log", (result, kwargs)),
    )
    monkeypatch.setattr(
        smu_app.media_service,
        "upload_jpeg_to_cloudinary",
        fake_upload_jpeg_to_cloudinary,
    )
    monkeypatch.setattr(
        smu_app.media_service,
        "upload_to_cloudinary",
        fake_upload_to_cloudinary,
    )
    monkeypatch.setattr(
        smu_app.media_service,
        "make_instagram_safe_url",
        lambda url: calls.setdefault("safe_url", url) or "safe",
    )
    monkeypatch.setattr(
        smu_app.media_service,
        "get_url_path_extension",
        lambda url: calls.setdefault("extension", url) or "jpg",
    )

    assert smu_app.get_file_type("photo.JPG") == "photo.JPG"
    assert smu_app.normalize_image_to_jpeg(b"image") == b"image"
    smu_app.log_image_normalization_diagnostics({"final_format": "JPEG"})
    assert smu_app.upload_jpeg_to_cloudinary(b"image")["secure_url"].endswith(".jpg")
    assert smu_app.upload_to_cloudinary("file")["secure_url"].endswith(".jpg")
    assert smu_app.make_instagram_safe_url("url") == "url"
    assert smu_app.get_url_path_extension("file.jpg") == "file.jpg"
    assert calls["jpeg_upload"]["upload_func"] is smu_app.cloudinary.uploader.upload
    assert calls["jpeg_upload"]["normalize_image_func"] is smu_app.normalize_image_to_jpeg
    assert calls["upload"]["upload_jpeg_func"] is smu_app.upload_jpeg_to_cloudinary
    assert calls["upload"]["upload_func"] is smu_app.cloudinary.uploader.upload
    assert calls["log"][1]["get_url_path_extension_func"] is smu_app.get_url_path_extension


def test_post_create_bridge_still_exposes_media_helpers(app):
    helpers = app.extensions["smu_post_create_helpers"]

    assert callable(helpers["get_file_type"])
    assert callable(helpers["upload_to_cloudinary"])


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("photo.jpg", "image"),
        ("photo.JPEG", "image"),
        ("graphic.PNG", "image"),
        ("clip.mp4", "video"),
        ("clip.MOV", "video"),
        ("clip.avi", "video"),
        ("clip.webm", "video"),
    ],
)
def test_get_file_type_preserves_supported_extensions(filename, expected):
    assert media.get_file_type(filename) == expected


def test_get_file_type_unsupported_extension_behaviour():
    with pytest.raises(Exception, match="Unsupported file type: txt"):
        media.get_file_type("notes.txt")

    with pytest.raises(Exception, match="Unsupported file type: "):
        media.get_file_type("no-extension")


def test_rgb_jpeg_remains_valid_rgb_jpeg_with_dimensions_preserved():
    source = BytesIO(image_bytes(image_format="JPEG", size=(11, 7)))

    result = media.normalize_image_to_jpeg(source)
    converted = read_image(result["bytes"])

    assert result["source_format"] == "JPEG"
    assert result["final_format"] == "JPEG"
    assert result["final_mode"] == "RGB"
    assert converted.format == "JPEG"
    assert converted.mode == "RGB"
    assert converted.size == (11, 7)


def test_rgb_png_becomes_jpeg():
    result = media.normalize_image_to_jpeg(
        image_bytes(mode="RGB", color=(1, 120, 200), image_format="PNG")
    )
    converted = read_image(result["bytes"])

    assert result["source_format"] == "PNG"
    assert converted.format == "JPEG"
    assert converted.mode == "RGB"


def test_rgba_png_transparency_flattens_to_white_background():
    source = BytesIO()
    image = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    image.putpixel((0, 0), (255, 0, 0, 255))
    image.save(source, format="PNG")

    result = media.normalize_image_to_jpeg(source.getvalue())
    converted = read_image(result["bytes"])
    transparent_pixel = converted.getpixel((3, 3))

    assert result["source_format"] == "PNG"
    assert result["final_mode"] == "RGB"
    assert converted.format == "JPEG"
    assert converted.size == (4, 4)
    assert all(channel > 240 for channel in transparent_pixel)


def test_upload_jpeg_to_cloudinary_preserves_upload_arguments():
    calls = {}
    normalized_bytes = media.normalize_image_to_jpeg(image_bytes())["bytes"]

    def fake_normalize(source):
        calls["source"] = source
        return {
            "bytes": normalized_bytes,
            "source_format": "PNG",
            "final_format": "JPEG",
            "final_mode": "RGB",
        }

    def fake_upload(file_obj, **kwargs):
        calls["upload_name"] = file_obj.name
        calls["upload_image"] = Image.open(file_obj)
        calls["upload_kwargs"] = kwargs
        return {"secure_url": "https://cdn.test/uploaded.jpg"}

    def fake_log(result, upload_url=None):
        calls["log"] = {"result": result, "upload_url": upload_url}

    result = media.upload_jpeg_to_cloudinary(
        b"raw",
        normalize_image_func=fake_normalize,
        upload_func=fake_upload,
        log_diagnostics_func=fake_log,
    )

    assert result == {"secure_url": "https://cdn.test/uploaded.jpg"}
    assert calls["source"] == b"raw"
    assert calls["upload_name"] == "instagram-safe.jpg"
    assert calls["upload_image"].format == "JPEG"
    assert calls["upload_kwargs"] == {
        "folder": "social_posts",
        "resource_type": "image",
        "format": "jpg",
    }
    assert calls["log"]["upload_url"] == "https://cdn.test/uploaded.jpg"


def test_upload_to_cloudinary_preserves_auto_upload_and_force_jpeg_behaviour():
    calls = []

    def fake_upload(source, **kwargs):
        calls.append(("auto", source, kwargs))
        return {"secure_url": "https://cdn.test/auto.png"}

    def fake_jpeg_upload(source):
        calls.append(("jpeg", source, {}))
        return {"secure_url": "https://cdn.test/jpeg.jpg"}

    assert media.upload_to_cloudinary(
        "file.png",
        upload_func=fake_upload,
        upload_jpeg_func=fake_jpeg_upload,
    ) == {"secure_url": "https://cdn.test/auto.png"}
    assert media.upload_to_cloudinary(
        "file.png",
        force_jpeg=True,
        upload_func=fake_upload,
        upload_jpeg_func=fake_jpeg_upload,
    ) == {"secure_url": "https://cdn.test/jpeg.jpg"}
    assert calls == [
        ("auto", "file.png", {"folder": "social_posts", "resource_type": "auto"}),
        ("jpeg", "file.png", {}),
    ]


def test_upload_failure_propagates():
    def fail_upload(source, **kwargs):
        raise RuntimeError("cloudinary failed")

    with pytest.raises(RuntimeError, match="cloudinary failed"):
        media.upload_to_cloudinary("file.png", upload_func=fail_upload)


def test_instagram_safe_url_and_extension_behaviour_are_unchanged():
    url = "https://res.cloudinary.com/demo/image/upload/v1/photo.png"

    assert media.make_instagram_safe_url(url) == (
        "https://res.cloudinary.com/demo/image/upload/"
        "c_fill,w_1080,h_1080,q_auto,f_jpg/v1/photo.png"
    )
    assert media.make_instagram_safe_url("https://example.test/upload/file.jpg") == (
        "https://example.test/upload/c_fill,w_1080,h_1080,q_auto,f_jpg/file.jpg"
    )
    assert media.make_instagram_safe_url("") == ""
    assert media.get_url_path_extension(url) == "png"
    assert media.get_url_path_extension("https://cdn.test/path/photo.JPG?x=1") == "jpg"
    assert media.get_url_path_extension("") == ""


def test_normalization_diagnostics_do_not_log_full_url_or_image_content(capsys):
    media.log_image_normalization_diagnostics(
        {
            "source_format": "PNG",
            "final_format": "JPEG",
            "final_mode": "RGB",
        },
        upload_url="https://cdn.test/private/path/photo.jpg?token=secret",
    )

    output = capsys.readouterr().out

    assert "source_format" in output
    assert "final_url_extension" in output
    assert "jpg" in output
    assert "https://cdn.test" not in output
    assert "token=secret" not in output

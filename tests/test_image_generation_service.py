import base64

import pytest

import app as smu_app
from smu_core.services import images


class FakeImageData:
    def __init__(self, b64_json):
        self.b64_json = b64_json


class FakeImageResult:
    def __init__(self, b64_json):
        self.data = [FakeImageData(b64_json)]


class FakeImagesClient:
    def __init__(self, b64_json=None, error=None):
        self.b64_json = b64_json or base64.b64encode(b"generated image").decode("ascii")
        self.error = error
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)

        if self.error:
            raise self.error

        return FakeImageResult(self.b64_json)


class FakeOpenAIClient:
    def __init__(self, b64_json=None, error=None):
        self.images = FakeImagesClient(b64_json=b64_json, error=error)


def test_images_service_exports_and_app_wrapper_remains_callable(module):
    assert callable(images.generate_openai_image)
    assert callable(images.generate_multiple_openai_images)
    assert callable(module.generate_openai_image)
    assert callable(module.generate_multiple_openai_images)


def test_app_wrapper_delegates_to_images_service(monkeypatch):
    calls = {}

    def fake_generate_openai_image(prompt, **kwargs):
        calls["single"] = {"prompt": prompt, **kwargs}
        return "https://cdn.test/generated.jpg"

    def fake_generate_multiple_openai_images(prompt, count=1, **kwargs):
        calls["multiple"] = {"prompt": prompt, "count": count, **kwargs}
        return ["https://cdn.test/one.jpg", "https://cdn.test/two.jpg"]

    monkeypatch.setattr(
        smu_app.images_service,
        "generate_openai_image",
        fake_generate_openai_image,
    )
    monkeypatch.setattr(
        smu_app.images_service,
        "generate_multiple_openai_images",
        fake_generate_multiple_openai_images,
    )

    assert smu_app.generate_openai_image("Prompt") == "https://cdn.test/generated.jpg"
    assert smu_app.generate_multiple_openai_images("Prompt", count=2) == [
        "https://cdn.test/one.jpg",
        "https://cdn.test/two.jpg",
    ]
    assert calls["single"]["prompt"] == "Prompt"
    assert calls["single"]["openai_api_key"] == smu_app.OPENAI_API_KEY
    assert calls["single"]["openai_client"] is smu_app.openai_client
    assert (
        calls["single"]["upload_jpeg_to_cloudinary_func"]
        is smu_app.upload_jpeg_to_cloudinary
    )
    assert calls["multiple"]["prompt"] == "Prompt"
    assert calls["multiple"]["count"] == 2
    assert calls["multiple"]["generate_openai_image_func"] is smu_app.generate_openai_image


def test_helper_bridges_still_expose_image_generation_helpers(app):
    assert callable(app.extensions["smu_post_create_helpers"]["generate_openai_image"])
    assert callable(app.extensions["smu_post_create_helpers"]["generate_multiple_openai_images"])
    assert callable(app.extensions["smu_post_edit_helpers"]["generate_openai_image"])
    assert callable(app.extensions["smu_tiktok_helpers"]["generate_openai_image"])


def test_generate_openai_image_preserves_request_parameters_and_base64_upload():
    raw_image = b"jpeg bytes"
    client = FakeOpenAIClient(
        b64_json=base64.b64encode(raw_image).decode("ascii")
    )
    calls = {}

    def fake_upload_jpeg_to_cloudinary(image_bytes):
        calls["image_bytes"] = image_bytes
        return {"secure_url": "https://cdn.test/generated.jpg"}

    result = images.generate_openai_image(
        "A branded image prompt",
        openai_api_key="key",
        openai_client=client,
        upload_jpeg_to_cloudinary_func=fake_upload_jpeg_to_cloudinary,
    )

    assert result == "https://cdn.test/generated.jpg"
    assert client.images.calls == [
        {
            "model": "gpt-image-1",
            "prompt": "A branded image prompt",
            "size": "1024x1024",
            "quality": "medium",
            "output_format": "jpeg",
            "timeout": images.OPENAI_IMAGE_TIMEOUT_SECONDS,
        }
    ]
    assert calls["image_bytes"] == raw_image


def test_generate_multiple_openai_images_preserves_loop_contract():
    calls = []

    def fake_generate(prompt):
        calls.append(prompt)
        return f"https://cdn.test/{len(calls)}.jpg"

    assert images.generate_multiple_openai_images(
        "Prompt",
        count=3,
        generate_openai_image_func=fake_generate,
    ) == [
        "https://cdn.test/1.jpg",
        "https://cdn.test/2.jpg",
        "https://cdn.test/3.jpg",
    ]
    assert calls == ["Prompt", "Prompt", "Prompt"]


def test_missing_api_key_preserves_exception_message():
    with pytest.raises(Exception, match="OPENAI_API_KEY is missing from your .env file"):
        images.generate_openai_image(
            "Prompt",
            openai_api_key="",
            openai_client=FakeOpenAIClient(),
            upload_jpeg_to_cloudinary_func=lambda image_bytes: {},
        )


def test_openai_exception_is_not_swallowed():
    with pytest.raises(RuntimeError, match="openai failed"):
        images.generate_openai_image(
            "Prompt",
            openai_api_key="key",
            openai_client=FakeOpenAIClient(error=RuntimeError("openai failed")),
            upload_jpeg_to_cloudinary_func=lambda image_bytes: {},
        )


def test_malformed_response_preserves_current_error_behaviour():
    class MalformedImages:
        def generate(self, **kwargs):
            class Result:
                data = []

            return Result()

    class MalformedClient:
        images = MalformedImages()

    with pytest.raises(IndexError):
        images.generate_openai_image(
            "Prompt",
            openai_api_key="key",
            openai_client=MalformedClient(),
            upload_jpeg_to_cloudinary_func=lambda image_bytes: {},
        )


def test_upload_exception_is_not_swallowed():
    def fail_upload(image_bytes):
        raise RuntimeError("upload failed")

    with pytest.raises(RuntimeError, match="upload failed"):
        images.generate_openai_image(
            "Prompt",
            openai_api_key="key",
            openai_client=FakeOpenAIClient(),
            upload_jpeg_to_cloudinary_func=fail_upload,
        )


def test_no_http_download_dependency_is_required(monkeypatch):
    def fail_import(name, *args, **kwargs):
        if name == "requests":
            raise AssertionError("image generation should not import requests")
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.setattr("builtins.__import__", fail_import)

    assert images.generate_openai_image(
        "Prompt",
        openai_api_key="key",
        openai_client=FakeOpenAIClient(),
        upload_jpeg_to_cloudinary_func=lambda image_bytes: {
            "secure_url": "https://cdn.test/generated.jpg"
        },
    ) == "https://cdn.test/generated.jpg"

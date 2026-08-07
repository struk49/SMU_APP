import os
from io import BytesIO
from urllib.parse import urlparse

import cloudinary.uploader
from PIL import Image


IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
VIDEO_EXTENSIONS = {"mp4", "mov", "avi", "webm"}
INSTAGRAM_SAFE_TRANSFORMATION = "/upload/c_fill,w_1080,h_1080,q_auto,f_jpg/"


def get_file_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""

    if ext in IMAGE_EXTENSIONS:
        return "image"

    if ext in VIDEO_EXTENSIONS:
        return "video"

    raise Exception(f"Unsupported file type: {ext}")


def normalize_image_to_jpeg(file_or_bytes):
    if isinstance(file_or_bytes, bytes):
        source = BytesIO(file_or_bytes)
    else:
        source = file_or_bytes

    if hasattr(source, "seek"):
        source.seek(0)

    with Image.open(source) as image:
        source_format = image.format or "unknown"

        if image.mode in {"RGBA", "LA"} or (
            image.mode == "P" and "transparency" in image.info
        ):
            rgba_image = image.convert("RGBA")
            background = Image.new("RGBA", rgba_image.size, (255, 255, 255, 255))
            background.alpha_composite(rgba_image)
            final_image = background.convert("RGB")
        else:
            final_image = image.convert("RGB")

        output = BytesIO()
        final_image.save(
            output,
            format="JPEG",
            quality=92,
            progressive=False,
            optimize=True,
        )

    return {
        "bytes": output.getvalue(),
        "source_format": source_format,
        "final_format": "JPEG",
        "final_mode": "RGB",
    }


def get_url_path_extension(url):
    path = urlparse(url or "").path
    _, extension = os.path.splitext(path)
    return extension.lower().lstrip(".")


def log_image_normalization_diagnostics(
    result,
    upload_url=None,
    *,
    get_url_path_extension_func=None,
):
    if get_url_path_extension_func is None:
        get_url_path_extension_func = get_url_path_extension

    print(
        "Image normalization diagnostics:",
        {
            "source_format": result.get("source_format"),
            "final_format": result.get("final_format"),
            "final_color_mode": result.get("final_mode"),
            "final_url_extension": get_url_path_extension_func(upload_url),
        },
    )


def upload_jpeg_to_cloudinary(
    file_or_bytes,
    *,
    normalize_image_func=None,
    upload_func=None,
    log_diagnostics_func=None,
):
    if normalize_image_func is None:
        normalize_image_func = normalize_image_to_jpeg
    if upload_func is None:
        upload_func = cloudinary.uploader.upload
    if log_diagnostics_func is None:
        log_diagnostics_func = log_image_normalization_diagnostics

    normalized = normalize_image_func(file_or_bytes)
    upload_buffer = BytesIO(normalized["bytes"])
    upload_buffer.name = "instagram-safe.jpg"

    upload_result = upload_func(
        upload_buffer,
        folder="social_posts",
        resource_type="image",
        format="jpg",
    )

    log_diagnostics_func(
        normalized,
        upload_url=upload_result.get("secure_url"),
    )

    return upload_result


def upload_to_cloudinary(
    file_or_url,
    force_jpeg=False,
    *,
    upload_jpeg_func=None,
    upload_func=None,
):
    if upload_jpeg_func is None:
        upload_jpeg_func = upload_jpeg_to_cloudinary
    if upload_func is None:
        upload_func = cloudinary.uploader.upload

    if force_jpeg:
        return upload_jpeg_func(file_or_url)

    return upload_func(
        file_or_url,
        folder="social_posts",
        resource_type="auto",
    )


def make_instagram_safe_url(url):
    return url.replace("/upload/", INSTAGRAM_SAFE_TRANSFORMATION)

import base64


def generate_openai_image(
    prompt,
    *,
    openai_api_key=None,
    openai_client=None,
    upload_jpeg_to_cloudinary_func=None,
):
    if not openai_api_key:
        raise Exception("OPENAI_API_KEY is missing from your .env file")

    result = openai_client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        size="1024x1024",
        quality="medium",
        output_format="jpeg",
    )

    image_base64 = result.data[0].b64_json
    image_bytes = base64.b64decode(image_base64)

    upload_result = upload_jpeg_to_cloudinary_func(image_bytes)

    return upload_result["secure_url"]


def generate_multiple_openai_images(
    prompt,
    count=1,
    *,
    generate_openai_image_func=None,
):
    image_urls = []

    for _ in range(count):
        image_url = generate_openai_image_func(prompt)
        image_urls.append(image_url)

    return image_urls

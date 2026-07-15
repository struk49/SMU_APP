# Publishing Contract

## Single-post payload
```json
{
  "post_type": "single",
  "post_id": 123,
  "caption": "Caption text",
  "prompt": "Optional prompt",
  "file_url": "https://public-media-url.example/image.jpg",
  "file_type": "image",
  "platforms": ["facebook", "instagram"]
}
```

## Carousel payload
```json
{
  "post_type": "carousel",
  "group_id": "uuid",
  "caption": "Caption text",
  "prompt": "Optional prompt",
  "platforms": ["instagram"],
  "media": [
    {
      "post_id": 1,
      "file_url": "https://public-media-url.example/1.jpg",
      "file_type": "image",
      "sort_order": 0,
      "is_cover": true
    }
  ]
}
```

## Required behaviour
- Filter requested platforms against the post owner's Connected Accounts.
- Select the owner's custom webhook first.
- Fall back to the configured global webhook only where intended.
- Send exactly once.
- Treat non-2xx responses as failures.
- Update status only after successful delivery.
- Never log webhook secrets.

# SMU Project Overview

SMU is an AI-powered social-media content workspace for creators and businesses.
The application includes a public landing page for product credibility, while
the authenticated workspace remains the core app experience. In the default
subscription mode, users can register and log in, then need an active Stripe
subscription before using product features.
Public About, Privacy Policy, Terms of Service and Contact pages are available
for paid-launch readiness and public trust.

## Current capabilities
- User authentication and user-owned posts
- Brand Brief
- Connected Accounts
- AI Content Studio
- Caption grading and Brand Coach
- Revision history
- Content Packs
- TikTok repurposing
- AI image generation through OpenAI
- Cloudinary media hosting
- Single-image and carousel payloads
- Make.com webhook publishing
- Post scheduling with APScheduler
- Stripe Checkout subscription foundation
- Subscription-gated product access
- Stripe Customer Portal billing management

## Stack
- Python / Flask
- Flask-Login
- Flask-SQLAlchemy
- SQLite locally and PostgreSQL on Render
- Bootstrap 5 and Jinja
- APScheduler
- OpenAI API
- Cloudinary
- Make.com
- Render
- Stripe

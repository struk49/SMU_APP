import json
import re

from smu_core.extensions import db
from smu_core.models import BrandBrief, PostRevision


def build_brand_context(user_id):
    brief = BrandBrief.query.filter_by(user_id=user_id).first()

    if not brief:
        return ""

    return f"""
BRAND BRIEF

Business Name:
{brief.business_name}

Niche:
{brief.niche}

Target Audience:
{brief.target_audience}

Offer:
{brief.offer}

Tone Of Voice:
{brief.tone_of_voice}

Content Goals:
{brief.content_goals}

Platforms:
{brief.main_platforms}

CTA Style:
{brief.cta_style}

Words To Avoid:
{brief.words_to_avoid}

IMPORTANT:
All content must match this brand brief.
Do not create generic content.
Use the tone, audience and offer above.
"""


def save_post_revision(post, source="manual"):
    latest_revision = PostRevision.query.filter_by(
        post_id=post.id,
        user_id=post.user_id
    ).order_by(PostRevision.version_number.desc()).first()

    next_version = 1

    if latest_revision:
        next_version = latest_revision.version_number + 1

    revision = PostRevision(
        post_id=post.id,
        user_id=post.user_id,
        version_number=next_version,
        caption=post.caption or "",
        score=post.grade_score,
        source=source
    )

    db.session.add(revision)
    return revision


def update_brand_coach(
    post,
    brand_context="",
    *,
    evaluate_brand_match_func,
    parse_brand_feedback_func,
):
    try:
        feedback_json = evaluate_brand_match_func(post, brand_context)

        parsed = parse_brand_feedback_func(feedback_json)

        post.brand_score = parsed["overall_score"]
        post.brand_feedback = json.dumps(parsed)

        return parsed

    except Exception as e:
        print("Brand Coach update error:", e)
        return None


def rewrite_caption_with_action(
    post,
    brand_context="",
    action="improve",
    *,
    openai_client,
):
    action_instructions = {
        "hook": "Improve the opening hook. Make the first line more attention-grabbing.",
        "cta": "Improve the call to action. Make the next step clearer and more persuasive.",
        "shorten": "Shorten the caption while keeping the main message.",
        "professional": "Rewrite the caption in a more professional tone.",
        "friendly": "Rewrite the caption in a warmer, friendlier tone.",
        "alternatives": "Create 3 alternative versions of this caption."
    }

    instruction = action_instructions.get(action, action_instructions["hook"])

    prompt = f"""
You are an expert social media copywriter.

Brand Brief:
{brand_context}

Current Caption:
{post.caption or ""}

Platform:
{post.platforms or ""}

Task:
{instruction}

Rules:
- Keep the message aligned with the Brand Brief.
- Keep the content suitable for the selected platform.
- Do not explain your changes.
- Return only the rewritten caption.
"""

    response = openai_client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

    return response.output_text.strip()


def grade_post_with_ai(
    post,
    brand_context="",
    *,
    openai_client,
):
    prompt = f"""
You are an expert social media strategist and content reviewer.

Your job is to grade this social media post for effectiveness.

Brand Brief:
{brand_context}

Post Caption:
{post.caption or ""}

Platform:
{post.platforms or ""}

Post Type:
{post.post_type or "single"}

Please score the post out of 10 for each category:

1. Hook
2. Clarity
3. Engagement
4. Call To Action
5. Platform Fit
6. Brand Fit

Then provide:
- OVERALL_SCORE: a single score out of 10
- STRENGTHS: short bullet points
- IMPROVEMENTS: short bullet points with specific advice

Return the result in this exact format:

HOOK_SCORE: X/10
CLARITY_SCORE: X/10
ENGAGEMENT_SCORE: X/10
CTA_SCORE: X/10
PLATFORM_FIT_SCORE: X/10
BRAND_FIT_SCORE: X/10
OVERALL_SCORE: X/10

STRENGTHS:
- ...
- ...

IMPROVEMENTS:
- ...
- ...
"""

    response = openai_client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

    return response.output_text.strip()


def extract_overall_score(grade_result):
    """
    Extracts the OVERALL_SCORE from the AI grading response.
    """

    if not grade_result:
        return None

    match = re.search(
        r"OVERALL_SCORE:\s*([0-9]+(?:\.[0-9]+)?)\/10",
        grade_result,
    )

    if not match:
        return None

    try:
        return float(match.group(1))
    except ValueError:
        return None

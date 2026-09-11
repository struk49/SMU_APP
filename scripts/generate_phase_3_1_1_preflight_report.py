"""Generate the local, factual Phase 3.1.1 renderer-preflight report."""

from pathlib import Path
import re

from smu_core.services.social_text import preflight_viral_carousel_text


REPORT = (
    Path(__file__).resolve().parents[1]
    / "artifacts"
    / "phase_3_1_1_renderer_preflight_report.txt"
)
WORD_RE = re.compile(r"\b[\w']+(?:[-‐‑–][\w']+)*\b", re.UNICODE)

CASES = (
    ("short cover", "cover", "hero_left", "illustration", "One strong idea", None),
    (
        "long but renderable cover", "cover", "hero_left", "illustration",
        "One source can become many useful posts without losing its central meaning", None,
    ),
    (
        "too-long cover", "cover", "hero_left", "illustration",
        "Supercalifragilisticexpialidocious " * 20, None,
    ),
    ("short info", "info", "split_left", "illustration", "Adapt with purpose", None),
    (
        "11-word / 68-character info", "info", "split_left", "illustration",
        "One idea makes many social posts without losing its original meaning", None,
    ),
    (
        "long but renderable info", "info", "split_right", "illustration",
        "Thoughtful source material can become useful channel-specific content while preserving meaning", None,
    ),
    (
        "actual unrenderable info", "info", "visual_focus", "visual_focus",
        "Pseudopseudohypoparathyroidism electroencephalographically " * 12, None,
    ),
    (
        "short support", "info", "split_left", "illustration",
        "Clear point", "One concise supporting idea.",
    ),
    (
        "15-word / 99-character cover support", "cover", "hero_left", "illustration",
        "Strong cover",
        "Core source becomes useful content for every platform without losing its original meaning or focus.",
    ),
    (
        "actual unrenderable support", "info", "split_left", "illustration",
        "Clear point", "Extraordinarilylongword " * 30,
    ),
    (
        "CTA", "cta", "closing", "typography_only", "Finish with one useful action", None,
    ),
)


def main():
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    sections = []
    for name, role, layout, treatment, title, body in CASES:
        result = preflight_viral_carousel_text(
            title=title.strip(),
            body=body.strip() if body else None,
            layout_role=role,
            layout_variant=layout,
            visual_treatment=treatment,
        )
        measured_copy = body.strip() if body else title.strip()
        sections.extend(
            (
                f"Case: {name}",
                f"Role: {role}",
                f"Layout: {layout}",
                f"Words: {len(WORD_RE.findall(measured_copy))}",
                f"Characters: {len(measured_copy)}",
                f"Fit result: {'pass' if result['fits'] else 'fail'}",
                f"Measured layout: {result.get('layout', layout)}",
                f"Headline font size: {result.get('headline_font_size')}",
                f"Headline lines: {result.get('headline_lines')}",
                f"Support font size: {result.get('support_font_size')}",
                f"Support lines: {result.get('support_lines')}",
                f"Reason: {result.get('failure_reason')}",
                "",
            )
        )
    REPORT.write_text("\n".join(sections), encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()

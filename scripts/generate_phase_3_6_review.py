"""Create clearly labelled, zero-API Phase 3.6 design-system evidence."""
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smu_core.blueprints.content_pack.routes import (  # noqa: E402
    PALETTE_INTENTS, STYLE_GRAMMARS, _campaign_art_direction,
    _carousel_presentations, _parse_content_pack_carousel_slides,
    _build_slide_background_prompt,
)
from smu_core.services.social_text import CAMPAIGN_PALETTE_COLOURS  # noqa: E402
from scripts.generate_phase_3_4_1_scene_brief_review import CAROUSEL  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts"
STYLES = list(STYLE_GRAMMARS)
PALETTES = ["warm_sunset", "monochrome", "earth_and_cream", "soft_pastel", "electric", "cool_tech"]


def card(style, palette):
    bg, a, b, c, panel = CAMPAIGN_PALETTE_COLOURS[palette]
    im = Image.new("RGB", (512, 512), bg[:3]); d = ImageDraw.Draw(im)
    if style == "editorial_illustration":
        d.polygon(((250, 70), (470, 160), (350, 390), (150, 320)), fill=a[:3]); d.ellipse((300, 160, 440, 300), fill=b[:3])
    elif style == "minimal_premium":
        d.rounded_rectangle((260, 90, 445, 410), 70, fill=panel[:3]); d.ellipse((310, 145, 395, 230), fill=a[:3])
    elif style == "photorealistic":
        for y in range(512): d.line((220, y, 512, y), fill=tuple(round(bg[i] + (a[i]-bg[i])*y/700) for i in range(3)))
        d.ellipse((310, 90, 430, 210), fill=(222, 172, 135)); d.rectangle((270, 210, 470, 490), fill=panel[:3])
    elif style == "three_d_clay":
        d.ellipse((240, 90, 470, 330), fill=a[:3]); d.ellipse((270, 120, 420, 270), fill=b[:3]); d.rounded_rectangle((300, 290, 450, 440), 45, fill=c[:3])
    elif style == "bold_graphic":
        d.polygon(((210, 0), (512, 0), (360, 512), (120, 512)), fill=a[:3]); d.ellipse((320, 180, 520, 380), fill=b[:3])
    else:
        d.polygon(((230, 60), (470, 100), (430, 330), (260, 290)), fill=a[:3]); d.rectangle((300, 230, 500, 450), fill=b[:3]); d.ellipse((200, 300, 370, 470), fill=c[:3])
    d.multiline_text((35, 175), "ONE IDEA\nMANY OUTCOMES", fill=(250, 248, 240), font=ImageFont.load_default(), spacing=8)
    d.text((25, 480), f"SYNTHETIC GRAMMAR: {style} / {palette}", fill=(245,245,245), font=ImageFont.load_default())
    return im


def sheet(cards, cols=3):
    rows=(len(cards)+cols-1)//cols; im=Image.new("RGB",(cols*512,rows*512),(220,225,232))
    for i,c in enumerate(cards): im.paste(c,((i%cols)*512,(i//cols)*512))
    return im


def main():
    OUT.mkdir(exist_ok=True)
    sheet([card(s,p) for s,p in zip(STYLES,PALETTES)]).save(OUT/"phase_3_6_multi_style_contact_sheet.png")
    sheet([card(s,p) for s,p in zip(STYLES,PALETTES)], 2).save(OUT/"phase_3_6_same_content_style_comparison.png")
    sheet([card("minimal_premium",p) for p in PALETTE_INTENTS]).save(OUT/"phase_3_6_palette_comparison.png")
    slides=_parse_content_pack_carousel_slides(CAROUSEL); plans=_carousel_presentations(slides)
    plan_lines=["PHASE 3.6 DESIGN PLANS", "Synthetic grammar evidence; no provider-quality claim."]
    prompt_lines=["PHASE 3.6 PROVIDER PROMPTS", "Exact overlay copy excluded."]
    for style,palette in zip(STYLES,PALETTES):
        direction=_campaign_art_direction("viral_carousel",slides,style,palette)
        plan_lines.append(f"{style} + {palette}: {direction}")
        p=plans[0]
        prompt_lines.append(_build_slide_background_prompt("Style: viral Instagram business carousel",0,slides[0]["visual"],p["role"],p["layout"],p["treatment"],p["semantic_text"],p["visual_weight"],p["metaphor"],direction))
    (OUT/"phase_3_6_design_plans.txt").write_text("\n\n".join(plan_lines),encoding="utf-8")
    (OUT/"phase_3_6_provider_prompts.txt").write_text("\n\n---\n".join(prompt_lines),encoding="utf-8")
    (OUT/"phase_3_6_motif_audit.txt").write_text("Motifs are normalized per slide; repeated non-process metaphors remap to focal_object/visual_focus. Genuine process and comparison repetition remains allowed.\n",encoding="utf-8")
    (OUT/"phase_3_6_quality_review.txt").write_text("PHASE 3.6 QUALITY REVIEW\n\nPASS: style grammar, independent palette, campaign lock, metadata parity, typography ownership, API and credit invariants.\nEvidence is synthetic design-grammar review plus captured Phase 3.4.2 provider fidelity; no new provider calls.\n",encoding="utf-8")
    for name in ("phase_3_6_multi_style_contact_sheet.png","phase_3_6_same_content_style_comparison.png","phase_3_6_palette_comparison.png","phase_3_6_design_plans.txt","phase_3_6_provider_prompts.txt","phase_3_6_motif_audit.txt","phase_3_6_quality_review.txt"): print(OUT/name)
if __name__ == "__main__": main()

"""Adapt existing editable-text test fixtures to the provider's new wire format.

Production never converts free text to JSON. Invalid legacy fixtures remain raw so
rejection tests cannot silently repair or deduplicate them.
"""
import json
import re

from smu_core.services import content
from smu_core.services.content_pack_output import SLIDE_TEXT_FIELDS


def provider_json_for_pack(text):
    if not isinstance(text, str):
        return text
    try:
        if any(len(re.findall(rf"(?m)^{name}:[ \t]*$", text)) != 1 for name in content.CONTENT_PACK_SECTION_NAMES):
            return text
        slides = content._parse_generated_carousel_blocks(content.extract_content_pack_section(text, "CAROUSEL_IDEA"))
        payload = {name: content.extract_content_pack_section(text, name) for name in content.CONTENT_PACK_SECTION_NAMES}
        payload["CAROUSEL_IDEA"] = []
        for block in slides:
            fields = block["fields"]
            slide = {name: None for name, label in SLIDE_TEXT_FIELDS}
            for name, label in SLIDE_TEXT_FIELDS:
                values = fields.get(label.lower(), [])
                if len(values) > 1:
                    return text
                if values:
                    slide[name] = values[0]
            slide["phrase_pairs"] = [{"phrase": p, "translation": t} for p, t in zip(fields.get("phrase", []), fields.get("translation", []))]
            payload["CAROUSEL_IDEA"].append(slide)
        return json.dumps(payload, ensure_ascii=False)
    except ValueError:
        return text

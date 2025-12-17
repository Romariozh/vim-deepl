# python/vim_deepl/integrations/deepl.py
from __future__ import annotations

import os
import json
import urllib.request
import urllib.parse
from typing import Optional, Tuple



def deepl_call(text: str, target_lang: str, context: str = "") -> Tuple[Optional[str], str, Optional[str]]:
    """Perform a DeepL API call."""
    api_key = os.environ.get("DEEPL_API_KEY", "")
    if not api_key:
        return None, "", "DEEPL_API_KEY is not set."

    url = "https://api-free.deepl.com/v2/translate"
    params = {
        "auth_key": api_key,
        "text": text,
        "target_lang": target_lang,
    }

    if context:
        params["context"] = context

    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")

    try:
        with urllib.request.urlopen(req) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return None, "", f"DeepL request error: {e}"

    translations = response.get("translations") or []
    if not translations:
        return None, "", "DeepL empty response."

    tr_obj = translations[0]
    translated_text = tr_obj.get("text", "")
    detected_lang = tr_obj.get("detected_source_language", "")

    return translated_text, detected_lang, None


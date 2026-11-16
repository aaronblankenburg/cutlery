"""UI translations: one JSON file per language in locales/ (flat keys, {name} placeholders).

Adding a language means adding locales/<code>.json with every key of en.json; no code changes. The language
comes from the cookie set by the switch in the header, then from Accept-Language, then English.
"""
from __future__ import annotations

import functools
import importlib.resources
import json
import re

DEFAULT = "en"
COOKIE = "cutlery_lang"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


@functools.cache
def catalogs() -> dict[str, dict[str, str]]:
    out = {}
    for f in (importlib.resources.files("cutlery") / "locales").iterdir():
        if f.name.endswith(".json"):
            out[f.name[:-5]] = json.loads(f.read_text(encoding="utf-8"))
    return out


def languages() -> list[tuple[str, str]]:
    """(code, own name) of every shipped language, English first."""
    cats = catalogs()
    return sorted(((code, cat.get("language.name", code)) for code, cat in cats.items()),
                  key=lambda x: (x[0] != DEFAULT, x[0]))


def negotiate(cookie: str | None, accept_language: str | None) -> str:
    cats = catalogs()
    if cookie in cats:
        return cookie
    ranked = []
    for i, part in enumerate((accept_language or "").split(",")):
        lang, _, params = part.strip().partition(";")
        q = 1.0
        for p in params.split(";"):
            k, _, v = p.strip().partition("=")
            if k == "q":
                try:
                    q = float(v)
                except ValueError:
                    q = 0.0
        if lang:
            ranked.append((-q, i, lang.lower()))
    for _, _, lang in sorted(ranked):
        for code in (lang, lang.split("-")[0]):
            if code in cats:
                return code
    return DEFAULT


def translate(lang: str, key: str, **values) -> str:
    cats = catalogs()
    text = cats.get(lang, {}).get(key) or cats[DEFAULT].get(key) or key
    return PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))), text)

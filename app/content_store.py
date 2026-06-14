"""Loads lesson content and art-style presets from JSON, with light validation."""
from __future__ import annotations

import functools
import json
from typing import Any

from .config import CONTENT_DIR


@functools.lru_cache(maxsize=1)
def _load_lessons() -> list[dict[str, Any]]:
    with open(CONTENT_DIR / "lessons.json", encoding="utf-8") as f:
        data = json.load(f)
    return data["lessons"]


@functools.lru_cache(maxsize=1)
def _load_art_styles() -> dict[str, Any]:
    with open(CONTENT_DIR / "art_styles.json", encoding="utf-8") as f:
        return json.load(f)


def list_lessons() -> list[dict[str, Any]]:
    """Lightweight cards for the catalog screen (no heavy body)."""
    out = []
    for ls in _load_lessons():
        illus = dict(ls["illustration"])
        illus.setdefault("image", f"/illustrations/{ls['id']}.svg")
        out.append({
            "id": ls["id"],
            "theme": ls["theme"],
            "theme_ja": ls["theme_ja"],
            "level": ls["level"],
            "title_en": ls["title_en"],
            "title_ja": ls["title_ja"],
            "est_minutes": ls["est_minutes"],
            "summary_ja": ls["summary_ja"],
            "illustration": illus,
            "image": f"/illustrations/{ls['id']}.svg",
        })
    return out


def get_lesson(lesson_id: str) -> dict[str, Any] | None:
    for ls in _load_lessons():
        if ls["id"] == lesson_id:
            return ls
    return None


def lesson_glossary(lesson_id: str) -> dict[str, str]:
    """word -> Japanese, built from the lesson's vocab list for offline fallback."""
    ls = get_lesson(lesson_id)
    if not ls:
        return {}
    g = {}
    for v in ls.get("vocab", []):
        g[v["en"].lower().strip()] = v["ja"]
    return g


def themes() -> list[dict[str, str]]:
    seen: dict[str, str] = {}
    for ls in _load_lessons():
        seen.setdefault(ls["theme"], ls["theme_ja"])
    return [{"theme": k, "theme_ja": v} for k, v in seen.items()]


def art_styles() -> dict[str, Any]:
    return _load_art_styles()


def build_illustration_prompt(lesson_id: str, style_key: str) -> dict[str, Any]:
    """Combine a lesson's scene description with the chosen global art style."""
    ls = get_lesson(lesson_id)
    styles = _load_art_styles()
    presets = styles["presets"]
    style = presets.get(style_key) or presets[styles["default"]]
    if not ls:
        return {}
    illus = ls["illustration"]
    scene = illus["scene"]
    prompt = f"{scene}. {style['prompt_suffix']}"
    return {
        "lesson_id": lesson_id,
        "style_key": style_key if style_key in presets else styles["default"],
        "style_name_ja": style["name_ja"],
        "caption_ja": illus["caption_ja"],
        "scene": scene,
        "prompt": prompt,
        "negative_prompt": style.get("negative_prompt", ""),
        "aspect": illus.get("aspect", "3:2"),
        # Placeholder artwork shipped with the app. Users can replace this file
        # (same path/name) with their own illustration anytime.
        "image": illus.get("image", f"/illustrations/{lesson_id}.svg"),
    }

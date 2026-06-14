#!/usr/bin/env python3
"""Generate a placeholder illustration SVG for a lesson.

Usage:
    python tools/gen_lesson_svg.py <lesson_id> "<Theme>" "<English title>" "<日本語タイトル>"

Themes recognised for colour/motif: "Grammar Basics", "Study Skills", "Business",
"School", "Travel", "Food", "Lifestyle", "Japanese Culture" (others fall back to
a neutral study motif). Output: web/illustrations/<lesson_id>.svg
"""
from __future__ import annotations
import html
import os
import sys

NAVY = "#1d3b5b"; LINE = "#2f3e50"; WHITE = "#f7fbff"

THEME = {
    "Grammar Basics":   ("#e9e3f6", "#cdbfe9", "#7a5ec4"),
    "Study Skills":     ("#e2f0ed", "#bfe0d8", "#3d8b7d"),
    "Business":         ("#e7ecf6", "#c4d2ec", "#5a76c4"),
    "School":           ("#fde7d6", "#f7cfa6", "#e98c4a"),
    "Travel":           ("#d9f0f7", "#a9dcee", "#3fa3c4"),
    "Food":             ("#fde3e0", "#f7c0bb", "#e0726a"),
    "Lifestyle":        ("#e4f3e6", "#bfe3c6", "#4c9a6a"),
    "Japanese Culture": ("#f6e2e6", "#edc1cb", "#c1556a"),
}


def motif(theme: str, acc: str) -> str:
    if theme == "Grammar Basics":
        # S V O C building blocks
        labels = [("S", -150), ("V", -78), ("O", -6), ("C", 66)]
        blocks = "".join(
            f'<g transform="translate({x},0)"><rect x="0" y="-34" width="62" height="62" rx="10" '
            f'fill="{WHITE}" stroke="{NAVY}" stroke-width="4"/>'
            f'<text x="31" y="10" text-anchor="middle" font-family="Segoe UI,sans-serif" '
            f'font-size="34" font-weight="800" fill="{acc}">{lab}</text></g>'
            for lab, x in labels)
        return (f'<g transform="translate(256,170)">'
                f'<ellipse cx="44" cy="70" rx="170" ry="20" fill="#000000" opacity="0.07"/>'
                f'{blocks}'
                f'<path d="M-120 44 H150" stroke="{acc}" stroke-width="4" stroke-dasharray="2 8" '
                f'stroke-linecap="round" opacity="0.6"/></g>')
    if theme == "Study Skills":
        # open book + headphones + pencil
        return (f'<g transform="translate(300,170)">'
                f'<ellipse cx="0" cy="92" rx="150" ry="20" fill="#000000" opacity="0.07"/>'
                f'<path d="M-120 -20 Q-60 -42 0 -24 Q60 -42 120 -20 L120 60 Q60 40 0 58 Q-60 40 -120 60 Z" '
                f'fill="{WHITE}" stroke="{NAVY}" stroke-width="4" stroke-linejoin="round"/>'
                f'<path d="M0 -24 L0 58" stroke="{NAVY}" stroke-width="4"/>'
                f'<path d="M-66 -78 a66 66 0 0 1 132 0 v8 a12 12 0 0 1 -24 0 v-8 a42 42 0 0 0 -84 0 v8 '
                f'a12 12 0 0 1 -24 0 Z" fill="{acc}" stroke="{NAVY}" stroke-width="4" stroke-linejoin="round"/>'
                f'</g>')
    if theme == "Business":
        return (f'<g transform="translate(300,170)">'
                f'<ellipse cx="0" cy="96" rx="150" ry="20" fill="#000000" opacity="0.07"/>'
                f'<rect x="-95" y="-30" width="190" height="118" rx="16" fill="{WHITE}" stroke="{NAVY}" stroke-width="4"/>'
                f'<path d="M-40 -30 V-46 Q-40 -58 -28 -58 H28 Q40 -58 40 -46 V-30" fill="none" stroke="{NAVY}" stroke-width="4"/>'
                f'<rect x="-95" y="20" width="190" height="16" fill="{acc}" opacity="0.5"/></g>')
    # neutral: a lightbulb of ideas
    return (f'<g transform="translate(300,160)">'
            f'<circle r="60" fill="{acc}" opacity="0.5"/>'
            f'<circle r="60" fill="none" stroke="{NAVY}" stroke-width="4"/>'
            f'<rect x="-18" y="56" width="36" height="26" rx="6" fill="{WHITE}" stroke="{NAVY}" stroke-width="4"/></g>')


def build(lesson_id: str, theme: str, en: str, ja: str) -> str:
    c0, c1, acc = THEME.get(theme, ("#e8e8ee", "#cfcfda", "#6a6a86"))
    en_e, ja_e, th = html.escape(en), html.escape(ja), html.escape(theme)
    tag_w = max(72, int(len(theme) * 8.2) + 24)
    tag_x, tag_cx = 572 - tag_w, tag_w / 2
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400" width="600" height="400" role="img" aria-label="{en_e}">
  <defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{c0}"/><stop offset="1" stop-color="{c1}"/></linearGradient></defs>
  <rect x="0" y="0" width="600" height="400" rx="24" fill="url(#bg)"/>
  <circle cx="300" cy="170" r="138" fill="#ffffff" opacity="0.35"/>
  {motif(theme, acc)}
  <rect x="0" y="318" width="600" height="82" fill="{NAVY}" opacity="0.92"/>
  <text x="32" y="352" font-family="'Segoe UI','Hiragino Kaku Gothic ProN','Yu Gothic',sans-serif" font-size="26" font-weight="700" fill="#ffffff">{en_e}</text>
  <text x="32" y="380" font-family="'Hiragino Kaku Gothic ProN','Yu Gothic',sans-serif" font-size="17" fill="#cfe0f2">{ja_e}</text>
  <g transform="translate({tag_x},30)"><rect x="0" y="-20" width="{tag_w}" height="30" rx="15" fill="#ffffff" opacity="0.85"/>
    <text x="{tag_cx}" y="0" text-anchor="middle" font-family="'Segoe UI',sans-serif" font-size="13" font-weight="700" fill="{acc}">{th}</text></g>
  <text x="300" y="300" text-anchor="middle" font-family="'Hiragino Kaku Gothic ProN',sans-serif" font-size="12" fill="{NAVY}" opacity="0.5">差し替え用プレースホルダー / replace freely</text>
</svg>
'''


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print(__doc__); sys.exit(1)
    lesson_id, theme, en, ja = sys.argv[1:5]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(here, "web", "illustrations")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{lesson_id}.svg")
    with open(path, "w", encoding="utf-8") as f:
        f.write(build(lesson_id, theme, en, ja))
    print("wrote", path)

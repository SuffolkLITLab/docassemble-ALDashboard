import colorsys
import random
import re
from typing import Dict, List, Tuple

from colorspace import HCL, qualitative_hcl

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MIN_WCAG_RATIO = 4.5

FONT_STACKS = {
    "system": '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    "source_sans": '"Source Sans Pro", "Helvetica Neue", Arial, sans-serif',
    "georgia": 'Georgia, "Times New Roman", Times, serif',
    "atkinson": '"Atkinson Hyperlegible", "Segoe UI", Arial, sans-serif',
}

STYLE_SCSS = {
    "square": {
        "border_radius": "0",
        "border_radius_sm": "0",
        "border_radius_lg": "0",
        "border_radius_pill": "0",
        "btn_box_shadow": "none",
    },
    "rounded": {
        "border_radius": ".5rem",
        "border_radius_sm": ".35rem",
        "border_radius_lg": ".75rem",
        "border_radius_pill": "50rem",
        "btn_box_shadow": "none",
    },
    "beveled": {
        "border_radius": ".25rem",
        "border_radius_sm": ".2rem",
        "border_radius_lg": ".4rem",
        "border_radius_pill": "50rem",
        "btn_box_shadow": "inset 0 1px 0 rgba(255,255,255,.22), 0 2px 0 rgba(0,0,0,.15)",
    },
}


def _normalize_hex(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("#") and len(value) == 4:
        value = "#" + "".join(ch * 2 for ch in value[1:])
    if not value.startswith("#"):
        value = f"#{value}"
    if not HEX_COLOR_RE.match(value):
        raise ValueError(f"Invalid hex color: {value}")
    return value.upper()


def _hex_to_rgb(value: str) -> Tuple[int, int, int]:
    value = _normalize_hex(value)
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


def _rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _relative_luminance(rgb: Tuple[int, int, int]) -> float:
    def transform(channel: int) -> float:
        c = channel / 255
        if c <= 0.03928:
            return c / 12.92
        return ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    rr, gg, bb = transform(r), transform(g), transform(b)
    return 0.2126 * rr + 0.7152 * gg + 0.0722 * bb


def contrast_ratio(color1: str, color2: str) -> float:
    l1 = _relative_luminance(_hex_to_rgb(color1))
    l2 = _relative_luminance(_hex_to_rgb(color2))
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _ensure_text_contrast(bg_color: str, min_ratio: float = MIN_WCAG_RATIO) -> str:
    white_ratio = contrast_ratio(bg_color, "#FFFFFF")
    black_ratio = contrast_ratio(bg_color, "#000000")
    if white_ratio >= min_ratio and white_ratio >= black_ratio:
        return "#FFFFFF"
    if black_ratio >= min_ratio:
        return "#000000"
    return "#FFFFFF" if white_ratio >= black_ratio else "#000000"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _adjust_lightness(hex_color: str, amount: float) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    l = _clamp(l + amount, 0.0, 1.0)
    rr, gg, bb = colorsys.hls_to_rgb(h, l, s)
    return _rgb_to_hex((int(rr * 255), int(gg * 255), int(bb * 255)))


def _ensure_contrast_against_bg(
    color: str, bg_color: str = "#FFFFFF", min_ratio: float = MIN_WCAG_RATIO
) -> str:
    normalized = _normalize_hex(color)
    if contrast_ratio(normalized, bg_color) >= min_ratio:
        return normalized

    best = normalized
    best_ratio = contrast_ratio(normalized, bg_color)
    for step in range(1, 41):
        delta = step * 0.02
        for candidate in (
            _adjust_lightness(normalized, -delta),
            _adjust_lightness(normalized, delta),
        ):
            ratio = contrast_ratio(candidate, bg_color)
            if ratio > best_ratio:
                best, best_ratio = candidate, ratio
            if ratio >= min_ratio:
                return candidate
    return best


def _hcl_to_hex(hue: float, chroma: float, luminance: float) -> str:
    return _normalize_hex(HCL(hue % 360, chroma, luminance).colors()[0])


def _accessible_theme_colors(primary_seed: str) -> Dict[str, str]:
    seed = _normalize_hex(primary_seed)
    r, g, b = _hex_to_rgb(seed)
    hue, lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    hue_deg = hue * 360
    sat_boost = _clamp(55 + saturation * 25, 50, 85)
    lum_base = _clamp(38 + (1 - lightness) * 20, 32, 52)

    raw = {
        "primary": _hcl_to_hex(hue_deg, sat_boost, lum_base),
        "secondary": _hcl_to_hex(hue_deg + 25, 28, 46),
        "success": _hcl_to_hex(hue_deg + 125, 55, 42),
        "info": _hcl_to_hex(hue_deg + 200, 52, 45),
        "warning": _hcl_to_hex(hue_deg + 80, 62, 54),
        "danger": _hcl_to_hex(hue_deg - 35, 56, 44),
        "light": "#F8F9FA",
        "dark": "#212529",
    }

    for key in ("primary", "secondary", "success", "info", "warning", "danger"):
        raw[key] = _ensure_contrast_against_bg(raw[key], bg_color="#FFFFFF")
        if max(
            contrast_ratio(raw[key], "#FFFFFF"),
            contrast_ratio(raw[key], "#000000"),
        ) < MIN_WCAG_RATIO:
            raw[key] = _ensure_contrast_against_bg(raw[key], bg_color="#000000")
    return raw


def easy_theme_scss(
    seed_color: str, font_choice: str = "system", style_choice: str = "rounded"
) -> Dict[str, object]:
    if font_choice not in FONT_STACKS:
        font_choice = "system"
    if style_choice not in STYLE_SCSS:
        style_choice = "rounded"

    theme_colors = _accessible_theme_colors(seed_color)
    shape = STYLE_SCSS[style_choice]
    body_bg = "#FFFFFF"
    body_color = _ensure_contrast_against_bg(theme_colors["dark"], body_bg)
    link_color = _ensure_contrast_against_bg(theme_colors["primary"], body_bg)
    link_hover = _ensure_contrast_against_bg(_adjust_lightness(link_color, -0.1), body_bg)

    swatches: List[Dict[str, str]] = []
    for key in ("primary", "secondary", "success", "info", "warning", "danger", "light", "dark"):
        value = theme_colors[key]
        swatches.append(
            {
                "name": key,
                "hex": value,
                "text": _ensure_text_contrast(value),
                "contrast_on_white": f"{contrast_ratio(value, '#FFFFFF'):.2f}",
            }
        )

    scss = f"""$white: #FFFFFF;
$black: #000000;
$body-bg: {body_bg};
$body-color: {body_color};
$link-color: {link_color};
$link-hover-color: {link_hover};
$font-family-sans-serif: {FONT_STACKS[font_choice]};
$border-radius: {shape['border_radius']};
$border-radius-sm: {shape['border_radius_sm']};
$border-radius-lg: {shape['border_radius_lg']};
$border-radius-pill: {shape['border_radius_pill']};
$btn-box-shadow: {shape['btn_box_shadow']};

$theme-colors: (
  "primary": {theme_colors["primary"]},
  "secondary": {theme_colors["secondary"]},
  "success": {theme_colors["success"]},
  "info": {theme_colors["info"]},
  "warning": {theme_colors["warning"]},
  "danger": {theme_colors["danger"]},
  "light": {theme_colors["light"]},
  "dark": {theme_colors["dark"]}
);

@import "bootstrap";
"""
    return {
        "seed_color": _normalize_hex(seed_color),
        "font_choice": font_choice,
        "style_choice": style_choice,
        "theme_colors": theme_colors,
        "swatches": swatches,
        "scss": scss,
        "wcag_min_ratio": MIN_WCAG_RATIO,
    }


def random_seed_colors(count: int = 3) -> List[str]:
    count = max(1, min(count, 8))
    palette = [c.upper() for c in qualitative_hcl(c=60, l=60).colors(12)]
    random.shuffle(palette)
    return palette[:count]


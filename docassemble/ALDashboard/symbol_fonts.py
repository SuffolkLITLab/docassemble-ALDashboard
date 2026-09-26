"""Curated Unicode proposals for symbolic fonts that carry no usable cmap.

Fonts like Webdings, Wingdings, and Symbol encode their glyphs in the private
use area (U+F020-U+F0FF). Nothing inside a PDF -- and nothing inside the font
program itself -- records what those glyphs *mean*, so a ToUnicode map can
never be derived from encoding rules alone. The tables below are the only
source of real Unicode for these fonts, so every entry is a deliberate human
judgement rather than a computed result.

Each entry records how it was established. ``evidence`` of ``"rendered"`` means
the glyph outline was rasterized from the shipping font and visually matched to
the proposed character; ``"standard"`` means the mapping comes from a published
encoding. Anything not listed here gets no proposal at all: the reviewer sees
the glyph and types the character themselves. A wrong default that looks
authoritative is worse than no default, so this table stays deliberately small
and biased toward the symbols that actually appear on court forms.
"""

from __future__ import annotations

import hashlib
import re
from typing import Dict, NamedTuple, Optional


class SymbolProposal(NamedTuple):
    """One curated character proposal for a symbolic font code point."""

    character: str
    unicode_name: str
    evidence: str

    @property
    def codepoint_label(self) -> str:
        return "U+" + "".join(f"{ord(ch):04X}" for ch in self.character)


# Checkbox and check-mark glyphs carry nearly all of the meaning on court
# forms, so those are the codes worth proposing. Each was confirmed by
# rasterizing the outline from the shipping Windows font and comparing it to
# the candidate character.
_WINGDINGS: Dict[int, SymbolProposal] = {
    0xA8: SymbolProposal("☐", "BALLOT BOX", "rendered"),
    0xFB: SymbolProposal("✗", "BALLOT X", "rendered"),
    0xFC: SymbolProposal("✓", "CHECK MARK", "rendered"),
    0xFD: SymbolProposal("☒", "BALLOT BOX WITH X", "rendered"),
    0xFE: SymbolProposal("☑", "BALLOT BOX WITH CHECK", "rendered"),
}

_WEBDINGS: Dict[int, SymbolProposal] = {
    0x63: SymbolProposal("☐", "BALLOT BOX", "rendered"),
}

# The Adobe Symbol encoding is published and stable, so the Greek range can be
# proposed from the standard rather than from a rendering.
_SYMBOL_GREEK_LOWER = {
    "a": "α",
    "b": "β",
    "g": "γ",
    "d": "δ",
    "e": "ε",
    "z": "ζ",
    "h": "η",
    "q": "θ",
    "i": "ι",
    "k": "κ",
    "l": "λ",
    "m": "μ",
    "n": "ν",
    "x": "ξ",
    "o": "ο",
    "p": "π",
    "r": "ρ",
    "s": "σ",
    "t": "τ",
    "u": "υ",
    "f": "φ",
    "c": "χ",
    "y": "ψ",
    "w": "ω",
}

_GREEK_NAMES = {
    "α": "ALPHA",
    "β": "BETA",
    "γ": "GAMMA",
    "δ": "DELTA",
    "ε": "EPSILON",
    "ζ": "ZETA",
    "η": "ETA",
    "θ": "THETA",
    "ι": "IOTA",
    "κ": "KAPPA",
    "λ": "LAMDA",
    "μ": "MU",
    "ν": "NU",
    "ξ": "XI",
    "ο": "OMICRON",
    "π": "PI",
    "ρ": "RHO",
    "σ": "SIGMA",
    "τ": "TAU",
    "υ": "UPSILON",
    "φ": "PHI",
    "χ": "CHI",
    "ψ": "PSI",
    "ω": "OMEGA",
}


def _build_symbol_table() -> Dict[int, SymbolProposal]:
    table: Dict[int, SymbolProposal] = {}
    for letter, lower in _SYMBOL_GREEK_LOWER.items():
        name = _GREEK_NAMES[lower]
        table[ord(letter)] = SymbolProposal(
            lower, f"GREEK SMALL LETTER {name}", "standard"
        )
        upper = lower.upper()
        # Final sigma has no distinct capital in the Symbol layout.
        if upper and upper != lower:
            table[ord(letter.upper())] = SymbolProposal(
                upper, f"GREEK CAPITAL LETTER {name}", "standard"
            )
    return table


_SYMBOL: Dict[int, SymbolProposal] = _build_symbol_table()

_TABLES: Dict[str, Dict[int, SymbolProposal]] = {
    "wingdings": _WINGDINGS,
    "webdings": _WEBDINGS,
    "symbol": _SYMBOL,
}

# Subset Type0 fonts often discard the original character code and retain only
# a glyph id. In that case the rendered outline is the stable evidence. These
# fingerprints are added only after comparing the embedded outline to the
# source document visually.
_OUTLINE_PROPOSALS: Dict[tuple[str, str], SymbolProposal] = {
    (
        "wingdings",
        "6b4beb546207468fdcc16968a19efefb9d6f9b15124ee69aad2e5b692bb29390",
    ): SymbolProposal("☐", "BALLOT BOX", "rendered-outline"),
    (
        "webdings",
        "f21c561349f7a9f3f7ea1b5d08ec02ccff60acd1c71823c77cb9035ed8a863d1",
    ): SymbolProposal("☐", "BALLOT BOX", "rendered-outline"),
}

# Some symbol fonts ship a ToUnicode map that records the *key you press* to
# get a glyph rather than what the glyph means. CombiNumerals draws circled
# numbers but declares the QWERTY row, so a screen reader reads a form's
# section markers aloud as "q w e r t y u". The declared text is stable across
# subsets in a way a glyph id is not, so it makes a good key.
_DECLARED_TEXT_PROPOSALS: Dict[tuple[str, str], SymbolProposal] = {
    # Verified by rasterizing the embedded outlines: the seven glyphs used on
    # the Alabama custody petition render as 1 through 7 in circles.
    ("combinumerals", "q"): SymbolProposal("\u2460", "CIRCLED DIGIT ONE", "rendered"),
    ("combinumerals", "w"): SymbolProposal("\u2461", "CIRCLED DIGIT TWO", "rendered"),
    ("combinumerals", "e"): SymbolProposal("\u2462", "CIRCLED DIGIT THREE", "rendered"),
    ("combinumerals", "r"): SymbolProposal("\u2463", "CIRCLED DIGIT FOUR", "rendered"),
    ("combinumerals", "t"): SymbolProposal("\u2464", "CIRCLED DIGIT FIVE", "rendered"),
    ("combinumerals", "y"): SymbolProposal("\u2465", "CIRCLED DIGIT SIX", "rendered"),
    ("combinumerals", "u"): SymbolProposal("\u2466", "CIRCLED DIGIT SEVEN", "rendered"),
    # The row continues, but no document here uses these, so they are inferred
    # from the unbroken run above rather than seen. Marked accordingly.
    ("combinumerals", "i"): SymbolProposal("\u2467", "CIRCLED DIGIT EIGHT", "inferred"),
    ("combinumerals", "o"): SymbolProposal("\u2468", "CIRCLED DIGIT NINE", "inferred"),
    ("combinumerals", "p"): SymbolProposal("\u2469", "CIRCLED NUMBER TEN", "inferred"),
}


def propose_declared_text(font_name: str, declared: str) -> Optional[SymbolProposal]:
    """Return what a symbol font really means where its own map misleads."""
    family = canonical_symbol_family(font_name)
    return _DECLARED_TEXT_PROPOSALS.get((family, str(declared or "").strip()))


# Families whose glyphs are decorative or symbolic rather than textual. Used to
# decide whether to offer the "mark as artifact" path at all.
SYMBOLIC_FAMILIES = frozenset(
    {
        "wingdings",
        "wingdings2",
        "wingdings3",
        "webdings",
        "symbol",
        "zapfdingbats",
        "combinumerals",
    }
)


def canonical_symbol_family(font_name: str) -> str:
    """Reduce a PDF BaseFont name to a bare family key.

    Strips the six-letter subset prefix and any style suffix, so
    ``/CELEJJ+Webdings,Bold`` and ``Webdings`` both resolve to ``webdings``.
    """
    name = re.sub(r"^[A-Z]{6}\+", "", str(font_name or "").lstrip("/"))
    name = name.split(",")[0].split("-")[0]
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def is_symbolic_family(font_name: str) -> bool:
    """Report whether this font is a known symbol/dingbat family."""
    return canonical_symbol_family(font_name) in SYMBOLIC_FAMILIES


def propose_character(font_name: str, code: int) -> Optional[SymbolProposal]:
    """Return the curated proposal for a font code, if one is established.

    ``code`` is the font's own single-byte character code, i.e. the low byte of
    the private-use code point. Returning ``None`` is the common case and is
    not an error -- it means the reviewer must supply the character.
    """
    table = _TABLES.get(canonical_symbol_family(font_name))
    if table is None:
        return None
    return table.get(code)


def propose_outline_character(
    font_name: str, outline_path: str
) -> Optional[SymbolProposal]:
    """Return a reviewed proposal for an exact embedded glyph outline."""
    digest = hashlib.sha256(str(outline_path or "").encode("utf-8")).hexdigest()
    return _OUTLINE_PROPOSALS.get((canonical_symbol_family(font_name), digest))


def curated_code_count(font_name: str) -> int:
    """Report how many codes are curated for a family, for UI messaging."""
    return len(_TABLES.get(canonical_symbol_family(font_name)) or {})

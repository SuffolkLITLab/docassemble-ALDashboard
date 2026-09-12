"""Group translation fragments into batches, and check what comes back.

Translating one fragment per API call is correct but slow: a mid-sized
interview has a couple of thousand fragments, so a draft takes a couple of
thousand round trips. Translating everything in one call is fast but was
abandoned because rows came back mixed up (see issue #285).

The middle ground is small batches whose responses are *verified* rather than
trusted. `plan_batches` decides how much goes in each request; `alignment_problems`
reports which rows of a response cannot be trusted, so the caller can retry just
those. A batch that fails verification degrades to smaller batches and finally to
one-fragment-per-call, which is the alignment-proof case.

This module lives apart from ``translation.py`` so it can be imported (and
tested) without pulling in the docassemble web application.
"""

import re
from functools import lru_cache
from typing import Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

__all__ = [
    "ModelPricing",
    "BatchLimits",
    "pricing_for_model",
    "batch_limits",
    "plan_batches",
    "estimate_tokens",
    "estimate_cost",
    "alignment_problems",
    "DEFAULT_MAX_FRAGMENTS_PER_BATCH",
]


# How many fragments go in one request by default. The number is a reliability
# choice, not a capacity one: the token budget below would allow far more, but a
# response the model has to keep aligned over hundreds of entries is where the
# row mix-ups in issue #285 came from. Twenty-five cuts the request count by
# ~25x while keeping each response short enough to stay aligned.
DEFAULT_MAX_FRAGMENTS_PER_BATCH = 25

# Translated text runs about as long as the source, longer in some languages,
# and the JSON scaffolding adds to it. Used to keep a batch's expected *output*
# inside the model's output cap, which for translation binds long before the
# input cap does.
DEFAULT_OUTPUT_RATIO = 2.0

# Per-entry JSON overhead: the id, the field names, the punctuation.
JSON_ITEM_OVERHEAD_TOKENS = 12

# Never plan a batch smaller than this, even if the caller passes silly limits.
MIN_CONTENT_BUDGET_TOKENS = 256


class ModelPricing(NamedTuple):
    """What a model costs and how much it can be given at once.

    ``surcharge_threshold_tokens`` is the interesting field: on the GPT-5.6 and
    GPT-6 families a request whose input goes over it is billed at
    ``surcharge_input_multiplier`` on *the whole request*, not just the excess.
    A translation batch has no reason to be that big, so the planner treats the
    threshold as a hard ceiling.
    """

    input_per_million: float
    cached_input_per_million: float
    output_per_million: float
    max_input_tokens: int
    max_output_tokens: int
    surcharge_threshold_tokens: Optional[int] = None
    surcharge_input_multiplier: float = 1.0
    surcharge_output_multiplier: float = 1.0


def _large_context(
    input_price: float, cached_input_price: float, output_price: float
) -> ModelPricing:
    """Pricing for a model on the shared GPT-5.6 / GPT-6 envelope.

    Verified against developers.openai.com/api/docs/models on 2026-09-12: every
    member of these two families has a 1.05M context, 922K in, 128K out, and is
    billed at 2x input / 1.5x output on the whole request once input passes 272K.
    """
    return ModelPricing(
        input_per_million=input_price,
        cached_input_per_million=cached_input_price,
        output_per_million=output_price,
        max_input_tokens=922_000,
        max_output_tokens=128_000,
        surcharge_threshold_tokens=272_000,
        surcharge_input_multiplier=2.0,
        surcharge_output_multiplier=1.5,
    )


MODEL_PRICING: Dict[str, ModelPricing] = {
    "gpt-5.6-luna": _large_context(0.20, 0.02, 1.20),
    "gpt-5.6-terra": _large_context(2.00, 0.20, 12.00),
    "gpt-5.6-sol": _large_context(4.00, 0.40, 20.00),
    "gpt-6-astra": _large_context(10.00, 1.00, 50.00),
    # The older models the fallback chain drops back to. None of these carry a
    # context-size surcharge. The 4.1 pair matter for batch planning as much as
    # for cost: they cap output at 32K rather than 128K, so a batch that is fine
    # for luna has to be a quarter of the size for them.
    "gpt-5.4-nano": ModelPricing(0.20, 0.02, 1.25, 272_000, 128_000),
    "gpt-5-nano": ModelPricing(0.05, 0.005, 0.40, 272_000, 128_000),
    "gpt-4.1-nano": ModelPricing(0.10, 0.025, 0.40, 1_014_808, 32_768),
    "gpt-4.1": ModelPricing(2.00, 0.50, 8.00, 1_014_808, 32_768),
}

# Anything we have no figures for. The token limits are the conservative ones
# every current OpenAI chat model meets; the prices are only used for the
# estimate shown to the user, so they are left at zero rather than guessed.
FALLBACK_PRICING = ModelPricing(
    input_per_million=0.0,
    cached_input_per_million=0.0,
    output_per_million=0.0,
    max_input_tokens=128_000,
    max_output_tokens=16_384,
)


def pricing_for_model(model: Optional[str]) -> ModelPricing:
    """Return what we know about a model, falling back to a safe envelope.

    Deployment names often carry a date suffix (``gpt-5.6-luna-2026-07-09``), so
    a prefix match is what actually matches in practice.
    """
    if not model:
        return FALLBACK_PRICING
    name = model.strip().lower()
    if name in MODEL_PRICING:
        return MODEL_PRICING[name]
    # Longest first, so "gpt-4.1-nano-2025-04-14" matches gpt-4.1-nano and not
    # the much pricier gpt-4.1 that it also starts with.
    for known in sorted(MODEL_PRICING, key=len, reverse=True):
        if name.startswith(known):
            return MODEL_PRICING[known]
    return FALLBACK_PRICING


@lru_cache(maxsize=16)
def _encoder(model: Optional[str]):
    """Return a tiktoken encoder, or None if tiktoken cannot supply one.

    Cached because looking an encoder up costs more than encoding a short
    fragment, and the old code looked one up once per fragment.
    """
    try:
        import tiktoken
    except ImportError:  # pragma: no cover - tiktoken is a hard dependency
        return None
    if model:
        try:
            return tiktoken.encoding_for_model(model)
        except Exception:
            pass
    try:
        return tiktoken.get_encoding("o200k_base")
    except Exception:  # pragma: no cover - defensive
        return None


def estimate_tokens(text: str, model: Optional[str] = None) -> int:
    """Estimate the token count of a string."""
    if not text:
        return 0
    encoder = _encoder(model)
    if encoder is None:
        # Four characters per token is the usual rule of thumb for English.
        return max(1, len(text) // 4)
    return len(encoder.encode(text))


class BatchLimits(NamedTuple):
    """The token budget one request may use, and why it came out that way."""

    content_budget_tokens: int
    max_fragments: int
    reason: str


def batch_limits(
    model: Optional[str] = None,
    overhead_tokens: int = 0,
    max_fragments_per_batch: int = DEFAULT_MAX_FRAGMENTS_PER_BATCH,
    max_input_tokens: Optional[int] = None,
    max_output_tokens: Optional[int] = None,
    output_ratio: float = DEFAULT_OUTPUT_RATIO,
) -> BatchLimits:
    """Work out how many tokens of fragment text one request may carry."""
    pricing = pricing_for_model(model)

    input_cap = pricing.max_input_tokens
    reason = "model input limit"
    if max_input_tokens:
        input_cap = min(input_cap, max_input_tokens)
        reason = "caller input limit"

    # Past the surcharge threshold every token in the request is billed at the
    # higher rate, and a translation batch gains nothing from a window that big.
    threshold = pricing.surcharge_threshold_tokens
    if threshold and threshold < input_cap:
        input_cap = threshold
        reason = "surcharge threshold"

    output_cap = pricing.max_output_tokens
    if max_output_tokens:
        output_cap = min(output_cap, max_output_tokens)
    # The reply restates every fragment in the target language, so the output
    # cap limits how much source text we can send. For translation this binds
    # long before any input limit does.
    output_bound = int(output_cap / max(output_ratio, 0.1))
    if output_bound < input_cap:
        input_cap = output_bound
        reason = "output limit"

    content_budget = max(input_cap - overhead_tokens, MIN_CONTENT_BUDGET_TOKENS)
    return BatchLimits(content_budget, max(1, max_fragments_per_batch), reason)


def plan_batches(
    fragments: Sequence[Tuple[int, str]],
    model: Optional[str] = None,
    overhead_tokens: int = 0,
    max_fragments_per_batch: int = DEFAULT_MAX_FRAGMENTS_PER_BATCH,
    max_input_tokens: Optional[int] = None,
    max_output_tokens: Optional[int] = None,
    output_ratio: float = DEFAULT_OUTPUT_RATIO,
) -> List[List[Tuple[int, str]]]:
    """Split fragments into batches in a single pass.

    Each fragment is measured once and the batches are filled greedily, so the
    cost is linear in the total length of the text. The previous implementation
    re-measured the whole list per chunk and then sliced it by a *token* count
    as though it were an item count, which is how entire chunks came out empty.
    """
    if not fragments:
        return []

    limits = batch_limits(
        model=model,
        overhead_tokens=overhead_tokens,
        max_fragments_per_batch=max_fragments_per_batch,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        output_ratio=output_ratio,
    )

    batches: List[List[Tuple[int, str]]] = []
    current: List[Tuple[int, str]] = []
    current_tokens = 0

    for row_number, text in fragments:
        cost = estimate_tokens(text, model) + JSON_ITEM_OVERHEAD_TOKENS
        too_many = len(current) >= limits.max_fragments
        too_big = current_tokens + cost > limits.content_budget_tokens
        if current and (too_many or too_big):
            batches.append(current)
            current = []
            current_tokens = 0
        current.append((row_number, text))
        current_tokens += cost

    if current:
        batches.append(current)
    return batches


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    model: Optional[str] = None,
    cached_input_tokens: int = 0,
) -> float:
    """Estimate the dollar cost of a set of requests, surcharge included.

    ``input_tokens`` is the total across every request. The surcharge applies
    per request, so this is only exact when batches stay under the threshold --
    which is what `plan_batches` arranges.
    """
    pricing = pricing_for_model(model)
    input_rate = pricing.input_per_million
    cached_rate = pricing.cached_input_per_million
    output_rate = pricing.output_per_million
    threshold = pricing.surcharge_threshold_tokens
    if threshold and input_tokens > threshold:
        input_rate *= pricing.surcharge_input_multiplier
        cached_rate *= pricing.surcharge_input_multiplier
        output_rate *= pricing.surcharge_output_multiplier
    fresh_input = max(input_tokens - cached_input_tokens, 0)
    return (
        fresh_input * input_rate
        + cached_input_tokens * cached_rate
        + output_tokens * output_rate
    ) / 1_000_000


# Code that has to survive translation verbatim. A fragment's placeholders are
# close to a fingerprint of it, so comparing them catches a translation that
# belongs to a different row far more reliably than reading the prose would.
_MAKO_EXPRESSION = re.compile(r"\$\{.*?\}", re.DOTALL)
_JINJA_TAG = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
_MAKO_DIRECTIVE = re.compile(
    r"^[ \t]*%[ \t]*(if|elif|else|endif|for|endfor|while|endwhile)\b",
    re.MULTILINE,
)
_WHITESPACE = re.compile(r"\s+")
# A quoted string inside a placeholder is user-visible text that a translator is
# right to translate: `${'do' if x else 'do not'}` properly becomes
# `${'sí' if x else 'no'}`. Compare the code around the quotes, not the quotes.
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

# How far a translation may stray from the size of its source before we stop
# believing it belongs to that source. Wide on purpose: real translations swing
# a long way between languages, and this only needs to catch gross truncation
# and two rows merged into one.
#
# Measured in tokens rather than characters, because characters are not
# comparable across scripts: "Excessive foot traffic" -> "過多客人" is a correct
# translation that is 0.18x the characters but 0.80x the tokens. Checked against
# the human-translated files shipped with AssemblyLine, where the character
# ratio flagged good CJK translations and the token ratio does not.
MIN_LENGTH_RATIO = 0.3
MAX_LENGTH_RATIO = 4.0
LENGTH_CHECK_MIN_TOKENS = 5


def _placeholders(text: str) -> List[str]:
    """Return a fragment's code placeholders, reduced to their code skeleton.

    Two things are normalised away before comparing. Whitespace, because
    `${ user.name }` and `${user.name}` are the same expression. And the
    contents of quoted strings, because those are user-visible text that a
    translator is supposed to translate -- real translation files turn
    `${'do' if claim_jurytrial else 'do not'}` into
    `${'sí' if claim_jurytrial else 'no'}`, and that is correct.

    What is left is the variable names and the structure, which is what has to
    survive intact and what makes a fragment identifiable.
    """
    found = _MAKO_EXPRESSION.findall(text) + _JINJA_TAG.findall(text)
    return sorted(_WHITESPACE.sub("", _QUOTED.sub("''", item)) for item in found)


def _has_prose(text: str) -> bool:
    """True if anything but code and punctuation is left once placeholders go."""
    without_code = _MAKO_EXPRESSION.sub(" ", text)
    without_code = _JINJA_TAG.sub(" ", without_code)
    without_code = _MAKO_DIRECTIVE.sub(" ", without_code)
    return bool(re.search(r"[^\W\d_]{2,}", without_code))


def _directive_counts(text: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for keyword in _MAKO_DIRECTIVE.findall(text):
        counts[keyword] = counts.get(keyword, 0) + 1
    return counts


def alignment_problems(
    fragments: Sequence[Tuple[int, str]],
    translations: Dict[int, str],
    model: Optional[str] = None,
) -> Dict[int, str]:
    """Return the rows of a response that cannot be trusted, and why.

    A row is reported when it is missing, empty, not a string, or when the
    translation does not look like it came from that row's source. The caller
    retries the reported rows in smaller batches rather than writing a
    translation that belongs to some other question into the workbook.
    """
    problems: Dict[int, str] = {}
    sources = {row_number: text for row_number, text in fragments}
    other_sources: Set[str] = {
        text.strip() for _row_number, text in fragments if text.strip()
    }

    for row_number, source in fragments:
        if row_number not in translations:
            problems[row_number] = "missing from the response"
            continue
        translated = translations[row_number]
        if not isinstance(translated, str):
            problems[row_number] = f"not text ({type(translated).__name__})"
            continue
        if source.strip() and not translated.strip():
            problems[row_number] = "empty translation"
            continue

        if _placeholders(source) != _placeholders(translated):
            problems[row_number] = "placeholders do not match the source"
            continue
        if _directive_counts(source) != _directive_counts(translated):
            problems[row_number] = "Mako directives do not match the source"
            continue

        stripped = translated.strip()
        if (
            stripped != source.strip()
            and stripped in other_sources
            and _has_prose(source)
        ):
            # It matched some *other* fragment's source text exactly. Only
            # meaningful for fragments that carry prose: a fragment that is
            # nothing but code, such as `${ today().format("YYYY-MM-dd") }`,
            # legitimately comes back looking like a different code fragment.
            problems[row_number] = "translation is another fragment's source text"
            continue

        source_tokens = estimate_tokens(source.strip(), model)
        if source_tokens >= LENGTH_CHECK_MIN_TOKENS:
            ratio = estimate_tokens(stripped, model) / source_tokens
            if ratio < MIN_LENGTH_RATIO or ratio > MAX_LENGTH_RATIO:
                problems[row_number] = f"length {ratio:.2f}x the source"
                continue

    for row_number in translations:
        if row_number not in sources:
            problems[row_number] = "not one of the requested rows"

    return problems

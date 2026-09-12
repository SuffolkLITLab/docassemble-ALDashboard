# do not pre-load
"""The Mako-retry fallback chain must not escalate cost.

`translation.py` cannot be imported without the docassemble web application, so
the chain is read out of the source rather than imported.
"""

import ast
import unittest
from pathlib import Path

from docassemble.ALDashboard.translation_batching import pricing_for_model

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _module_constant(name: str):
    tree = ast.parse((PACKAGE_ROOT / "translation.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in translation.py")


def _blended_price(model: str) -> float:
    """Cost of a million tokens in and a million out, which is roughly the
    shape of a translation workload."""
    pricing = pricing_for_model(model)
    return pricing.input_per_million + pricing.output_per_million


class TestFallbackChain(unittest.TestCase):
    def setUp(self):
        self.default = _module_constant("DEFAULT_TRANSLATION_MODEL")
        self.chain = list(_module_constant("TRANSLATION_MODEL_FALLBACK_CHAIN"))

    def test_default_model_is_luna(self):
        self.assertEqual(self.default, "gpt-5.6-luna")

    def test_chain_starts_at_the_default_model(self):
        """The start_index logic in translate_with_retries depends on this."""
        self.assertEqual(self.chain[0], self.default)

    def test_no_fallback_costs_more_than_the_default(self):
        """A retry must never escalate to a pricier tier."""
        budget = _blended_price(self.default)
        for model in self.chain[1:]:
            self.assertLessEqual(
                _blended_price(model),
                budget,
                f"{model} costs more than the default {self.default}",
            )

    def test_premium_tiers_are_not_in_the_chain(self):
        for model in ("gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra"):
            self.assertNotIn(model, self.chain)

    def test_every_model_in_the_chain_has_known_pricing(self):
        """An unpriced model would silently get the zero-cost fallback."""
        for model in self.chain:
            self.assertGreater(_blended_price(model), 0.0, model)

    def test_chain_is_reachable_within_the_retry_budget(self):
        """Entries past MAX_MAKO_RETRIES would never be tried."""
        max_retries = _module_constant("MAX_MAKO_RETRIES")
        # models_to_try is the configured model followed by the chain after it.
        self.assertLessEqual(len(self.chain) - 1, max_retries)


if __name__ == "__main__":
    unittest.main()

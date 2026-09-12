# do not pre-load
"""Batching and response verification for AI translation. See issue #285."""

import unittest

from docassemble.ALDashboard.translation_batching import (
    DEFAULT_MAX_FRAGMENTS_PER_BATCH,
    alignment_problems,
    batch_limits,
    estimate_cost,
    estimate_tokens,
    plan_batches,
    pricing_for_model,
)


class TestPricing(unittest.TestCase):
    def test_known_model(self):
        pricing = pricing_for_model("gpt-5.6-luna")
        self.assertEqual(pricing.input_per_million, 0.20)
        self.assertEqual(pricing.output_per_million, 1.20)
        self.assertEqual(pricing.max_input_tokens, 922_000)
        self.assertEqual(pricing.max_output_tokens, 128_000)
        self.assertEqual(pricing.surcharge_threshold_tokens, 272_000)

    def test_dated_deployment_name_matches(self):
        """Azure and OpenAI both hand out dated deployment names."""
        self.assertEqual(
            pricing_for_model("gpt-5.6-luna-2026-07-09"),
            pricing_for_model("gpt-5.6-luna"),
        )

    def test_case_insensitive(self):
        self.assertEqual(
            pricing_for_model("GPT-5.6-Luna"), pricing_for_model("gpt-5.6-luna")
        )

    def test_older_fallback_models_are_priced(self):
        """The chain drops back to these, so their cost has to be known."""
        for name, in_price, out_price in [
            ("gpt-5.4-nano", 0.20, 1.25),
            ("gpt-5-nano", 0.05, 0.40),
            ("gpt-4.1-nano", 0.10, 0.40),
            ("gpt-4.1", 2.00, 8.00),
        ]:
            pricing = pricing_for_model(name)
            self.assertEqual(pricing.input_per_million, in_price, name)
            self.assertEqual(pricing.output_per_million, out_price, name)
            self.assertIsNone(pricing.surcharge_threshold_tokens, name)

    def test_a_longer_model_name_wins_the_prefix_match(self):
        """gpt-4.1-nano also starts with gpt-4.1, which costs 20x as much."""
        self.assertEqual(
            pricing_for_model("gpt-4.1-nano-2025-04-14").output_per_million, 0.40
        )
        self.assertEqual(
            pricing_for_model("gpt-4.1-2025-04-14").output_per_million, 8.00
        )

    def test_unknown_model_gets_a_conservative_envelope(self):
        pricing = pricing_for_model("some-other-model")
        self.assertEqual(pricing.max_input_tokens, 128_000)
        self.assertIsNone(pricing.surcharge_threshold_tokens)
        # Prices we do not know are reported as zero rather than guessed.
        self.assertEqual(pricing.input_per_million, 0.0)

    def test_no_model_named(self):
        self.assertEqual(pricing_for_model(None).max_input_tokens, 128_000)

    def test_cost_is_linear_below_the_threshold(self):
        cost = estimate_cost(100_000, 100_000, model="gpt-5.6-luna")
        self.assertAlmostEqual(cost, (0.20 + 1.20) / 10, places=6)

    def test_cost_applies_the_surcharge_above_the_threshold(self):
        """>272K input is billed at 2x input and 1.5x output for the whole request."""
        under = estimate_cost(272_000, 100_000, model="gpt-5.6-luna")
        over = estimate_cost(272_001, 100_000, model="gpt-5.6-luna")
        self.assertAlmostEqual(under, (272_000 * 0.20 + 100_000 * 1.20) / 1e6, places=9)
        # The multipliers differ, so the two halves scale differently.
        self.assertAlmostEqual(over, (272_001 * 0.40 + 100_000 * 1.80) / 1e6, places=9)
        self.assertGreater(over, under)

    def test_cached_input_is_cheaper(self):
        full = estimate_cost(100_000, 0, model="gpt-5.6-luna")
        cached = estimate_cost(
            100_000, 0, model="gpt-5.6-luna", cached_input_tokens=100_000
        )
        self.assertAlmostEqual(cached, 0.002, places=6)
        self.assertLess(cached, full)


class TestBatchLimits(unittest.TestCase):
    def test_never_plans_a_request_over_the_surcharge_threshold(self):
        limits = batch_limits(model="gpt-5.6-luna", max_fragments_per_batch=10_000)
        self.assertLessEqual(limits.content_budget_tokens, 272_000)

    def test_output_limit_binds_before_the_input_limit(self):
        """A translation's reply is as long as its source, so 128K out rules."""
        limits = batch_limits(model="gpt-5.6-luna", max_fragments_per_batch=10_000)
        self.assertEqual(limits.reason, "output limit")
        self.assertLessEqual(limits.content_budget_tokens, 128_000)

    def test_caller_limits_are_respected(self):
        limits = batch_limits(model="gpt-5.6-luna", max_input_tokens=5_000)
        self.assertLessEqual(limits.content_budget_tokens, 5_000)

    def test_system_prompt_overhead_comes_out_of_the_budget(self):
        without = batch_limits(model="gpt-5.6-luna", max_input_tokens=10_000)
        with_overhead = batch_limits(
            model="gpt-5.6-luna", max_input_tokens=10_000, overhead_tokens=1_000
        )
        self.assertEqual(
            with_overhead.content_budget_tokens, without.content_budget_tokens - 1_000
        )

    def test_a_smaller_output_cap_means_smaller_batches(self):
        """gpt-4.1 caps output at 32K, a quarter of what luna allows."""
        luna = batch_limits(model="gpt-5.6-luna", max_fragments_per_batch=10_000)
        older = batch_limits(model="gpt-4.1", max_fragments_per_batch=10_000)
        self.assertLess(older.content_budget_tokens, luna.content_budget_tokens)
        self.assertLessEqual(older.content_budget_tokens, 32_768)

    def test_prompt_overhead_cannot_override_a_hard_caller_limit(self):
        limits = batch_limits(
            model="gpt-5.6-luna", max_output_tokens=100, overhead_tokens=60
        )
        self.assertEqual(limits.content_budget_tokens, 50)

    def test_input_overhead_and_output_limit_are_separate_bounds(self):
        limits = batch_limits(
            model="gpt-5.6-luna",
            max_input_tokens=100,
            max_output_tokens=1_000,
            overhead_tokens=60,
        )
        self.assertEqual(limits.content_budget_tokens, 40)


class TestPlanBatches(unittest.TestCase):
    def test_every_fragment_appears_exactly_once_and_in_order(self):
        """The old implementation sliced a list by a token count and lost rows."""
        fragments = [
            (row, f"Segment number {row} of the interview") for row in range(97)
        ]
        batches = plan_batches(fragments, model="gpt-5.6-luna")
        flattened = [item for batch in batches for item in batch]
        self.assertEqual(flattened, fragments)

    def test_no_batch_is_empty(self):
        fragments = [(row, "Hello there") for row in range(51)]
        for batch in plan_batches(fragments, model="gpt-5.6-luna"):
            self.assertTrue(batch)

    def test_respects_the_fragment_cap(self):
        fragments = [(row, "Hello there") for row in range(130)]
        batches = plan_batches(
            fragments, model="gpt-5.6-luna", max_fragments_per_batch=25
        )
        self.assertEqual(len(batches), 6)
        for batch in batches:
            self.assertLessEqual(len(batch), 25)

    def test_respects_the_token_budget(self):
        long_text = "word " * 400
        fragments = [(row, long_text) for row in range(20)]
        batches = plan_batches(
            fragments,
            model="gpt-5.6-luna",
            max_input_tokens=2_000,
            max_fragments_per_batch=1_000,
        )
        self.assertGreater(len(batches), 1)
        for batch in batches:
            total = sum(estimate_tokens(text, "gpt-5.6-luna") for _row, text in batch)
            # One oversized fragment is allowed to stand alone; otherwise the
            # batch has to fit.
            if len(batch) > 1:
                self.assertLessEqual(total, 2_000)

    def test_one_oversized_fragment_fails_instead_of_exceeding_the_limit(self):
        fragments = [(0, "short"), (1, "word " * 5_000), (2, "short")]
        with self.assertRaisesRegex(ValueError, "row 1"):
            plan_batches(fragments, model="gpt-5.6-luna", max_input_tokens=1_000)

    def test_empty_input(self):
        self.assertEqual(plan_batches([], model="gpt-5.6-luna"), [])

    def test_default_batch_size_is_a_middle_ground(self):
        """Not one call per fragment, not one call for the whole interview."""
        self.assertGreater(DEFAULT_MAX_FRAGMENTS_PER_BATCH, 1)
        self.assertLess(DEFAULT_MAX_FRAGMENTS_PER_BATCH, 100)


class TestAlignmentProblems(unittest.TestCase):
    def test_a_clean_response_has_no_problems(self):
        fragments = [(0, "What is your name?"), (1, "Where do you live?")]
        translations = {0: "¿Cómo se llama?", 1: "¿Dónde vive?"}
        self.assertEqual(alignment_problems(fragments, translations), {})

    def test_missing_row_is_reported(self):
        fragments = [(0, "What is your name?"), (1, "Where do you live?")]
        problems = alignment_problems(fragments, {0: "¿Cómo se llama?"})
        self.assertIn(1, problems)
        self.assertNotIn(0, problems)

    def test_extra_row_is_reported(self):
        fragments = [(0, "What is your name?")]
        problems = alignment_problems(fragments, {0: "¿Cómo se llama?", 7: "¿Qué?"})
        self.assertIn(7, problems)

    def test_swapped_rows_are_caught_by_their_placeholders(self):
        """The mix-up issue #285 describes, caught because the code differs."""
        fragments = [
            (0, "Hello ${ user.name }, welcome to the interview about your case"),
            (1, "Your case number is ${ case.number } and it is now filed"),
        ]
        swapped = {
            0: "Su número de caso es ${ case.number } y ya está presentado",
            1: "Hola ${ user.name }, bienvenido a la entrevista sobre su caso",
        }
        problems = alignment_problems(fragments, swapped)
        self.assertEqual(set(problems), {0, 1})

    def test_placeholder_whitespace_is_not_a_problem(self):
        fragments = [(0, "Hello ${ user.name }")]
        translations = {0: "Hola ${user.name}"}
        self.assertEqual(alignment_problems(fragments, translations), {})

    def test_dropped_placeholder_is_reported(self):
        fragments = [(0, "Hello ${ user.name }, welcome")]
        self.assertIn(0, alignment_problems(fragments, {0: "Hola, bienvenido"}))

    def test_mako_directives_must_survive(self):
        fragments = [(0, "% if user.is_tenant:\nYou are a tenant\n% endif")]
        good = {0: "% if user.is_tenant:\nUsted es inquilino\n% endif"}
        bad = {0: "Usted es inquilino"}
        self.assertEqual(alignment_problems(fragments, good), {})
        self.assertIn(0, alignment_problems(fragments, bad))

    def test_mako_directive_expressions_must_survive(self):
        fragments = [(0, "% if user.is_tenant:\nYou are a tenant\n% endif")]
        swapped_condition = {
            0: "% if user.is_landlord:\nUsted es inquilino\n% endif"
        }
        self.assertIn(0, alignment_problems(fragments, swapped_condition))

    def test_mako_directive_whitespace_may_change(self):
        fragments = [(0, "% if user.is_tenant:\nYou are a tenant\n% endif")]
        translated = {0: "%if  user.is_tenant :\nUsted es inquilino\n% endif"}
        self.assertEqual(alignment_problems(fragments, translated), {})

    def test_whitespace_inside_a_directive_string_is_significant(self):
        fragments = [(0, '% if answer == "a b":\nMatch\n% endif')]
        translated = {0: '% if answer == "ab":\nCoincide\n% endif'}
        self.assertIn(0, alignment_problems(fragments, translated))

    def test_other_python_line_directives_must_survive(self):
        fragments = [(0, "% try:\nDo something\n% except ValueError:\nRecover")]
        translated = {0: "% try:\nHaga algo\n% except TypeError:\nRecupérese"}
        self.assertIn(0, alignment_problems(fragments, translated))

    def test_empty_translation_of_nonempty_source_is_reported(self):
        self.assertIn(0, alignment_problems([(0, "Hello")], {0: "   "}))

    def test_non_string_value_is_reported(self):
        self.assertIn(0, alignment_problems([(0, "Hello")], {0: ["Hola"]}))

    def test_translation_that_is_another_fragments_source_is_reported(self):
        fragments = [(0, "What is your name?"), (1, "Where do you live?")]
        translations = {0: "Where do you live?", 1: "¿Dónde vive?"}
        self.assertIn(0, alignment_problems(fragments, translations))

    def test_untranslated_text_matching_its_own_source_is_allowed(self):
        """Plenty of segments are the same in both languages."""
        fragments = [(0, "OK"), (1, "Boston")]
        self.assertEqual(alignment_problems(fragments, {0: "OK", 1: "Boston"}), {})

    def test_two_rows_merged_into_one_is_caught_by_length(self):
        """Only for sources long enough to check; short ones rely on the rest."""
        fragments = [
            (0, "What is your full legal name?"),
            (1, "Where do you live and how long have you lived there?"),
        ]
        merged = {
            0: "¿Cuál es su nombre legal completo? ¿Dónde vive y cuánto tiempo "
            "hace que vive allí? "
            "Por favor responda ambas preguntas con todo el detalle que pueda "
            "para que podamos completar el formulario correctamente.",
            1: "¿Dónde vive y cuánto tiempo hace que vive allí?",
        }
        self.assertIn(0, alignment_problems(fragments, merged))

    def test_short_segments_are_not_length_checked(self):
        """Short strings swing wildly between languages; do not flag them."""
        fragments = [(0, "Yes")]
        self.assertEqual(alignment_problems(fragments, {0: "Sí, por supuesto"}), {})

    def test_string_literals_inside_a_placeholder_may_be_translated(self):
        """Real translation files do this, and they are right to."""
        fragments = [
            (0, "You said you ${'do' if claim_jurytrial else 'do not'} want a jury")
        ]
        translations = {
            0: "Dijiste que ${'sí' if claim_jurytrial else 'no'} quieres jurado"
        }
        self.assertEqual(alignment_problems(fragments, translations), {})

    def test_renaming_a_variable_inside_a_placeholder_is_still_flagged(self):
        fragments = [
            (0, "You said you ${'do' if claim_jurytrial else 'do not'} want a jury")
        ]
        translations = {0: "Dijiste que ${'sí' if otra_cosa else 'no'} quieres jurado"}
        self.assertIn(0, alignment_problems(fragments, translations))

    def test_machine_facing_string_argument_must_not_be_translated(self):
        fragments = [
            (0, '${ action_button_html(url_action("save_changes"), label="Save") }')
        ]
        translations = {
            0: '${ action_button_html(url_action("guardar_cambios"), label="Guardar") }'
        }
        self.assertIn(0, alignment_problems(fragments, translations))

    def test_html_tags_must_survive(self):
        fragments = [(0, '<a href="/help">Read more</a>')]
        translations = {0: '<a href="/ayuda">Leer más</a>'}
        self.assertIn(0, alignment_problems(fragments, translations))

    def test_dense_scripts_are_not_flagged_as_too_short(self):
        """Characters are not comparable across scripts; tokens are."""
        fragments = [(0, "Excessive foot traffic"), (1, "Destruction of property")]
        translations = {0: "過多客人", 1: "破壞房產"}
        self.assertEqual(alignment_problems(fragments, translations), {})

    def test_truncated_translation_is_still_flagged(self):
        fragments = [
            (0, "Green words are legal terms or a short way of referring to something")
        ]
        self.assertIn(0, alignment_problems(fragments, {0: "mo vèt"}))

    def test_a_code_only_fragment_cannot_change_a_format_string(self):
        """Quoted machine-facing arguments are executable data, not prose."""
        fragments = [
            (0, '${ today().format("YYYY-MM-dd") }'),
            (1, '${ today().format("yyyy-MM-dd") }'),
        ]
        translations = {
            0: '${ today().format("yyyy-MM-dd") }',
            1: '${ today().format("YYYY-MM-dd") }',
        }
        self.assertEqual(set(alignment_problems(fragments, translations)), {0, 1})

    def test_prose_swapped_for_a_stable_value_is_flagged(self):
        """Seen for real in a shipped eviction_vi.xlsx."""
        fragments = [
            (0, "My name is misspelled on the summons."),
            (1, "fixed_term"),
        ]
        translations = {0: "fixed_term", 1: "fixed_term"}
        self.assertIn(0, alignment_problems(fragments, translations))

    def test_normal_language_expansion_is_not_flagged(self):
        fragments = [(0, "Please describe what happened in your own words.")]
        translations = {
            0: "Por favor, describa lo que sucedió con sus propias palabras."
        }
        self.assertEqual(alignment_problems(fragments, translations), {})


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import MagicMock, patch

import docx
from docx.oxml import parse_xml

from docassemble.ALDashboard.docx_wrangling import (
    _chat_completion_with_model_compat,
    _is_max_completion_tokens_unsupported_error,
    _is_mistral_model,
    _merge_label_candidates,
    _regex_placeholder_fallback,
    get_docx_run_items,
    update_docx,
)


class TestDocxWranglingUpdateDocx(unittest.TestCase):
    def test_update_docx_replaces_existing_run(self):
        document = docx.Document()
        paragraph = document.add_paragraph()
        paragraph.add_run("Name: ____")

        updated = update_docx(document, [(0, 0, "Name: {{ users[0] }}", 0)])

        self.assertEqual(updated.paragraphs[0].runs[0].text, "Name: {{ users[0] }}")

    def test_update_docx_inserts_wordprocessingml_safe_paragraphs(self):
        document = docx.Document()
        paragraph = document.add_paragraph("Anchor")

        updated = update_docx(
            document,
            [
                (0, 0, "{%p if has_value %}\t", -1),
                (0, 0, "{%p endif %}\n", 1),
            ],
        )

        self.assertEqual(updated.paragraphs[0].text, "{%p if has_value %}\t")
        self.assertEqual(updated.paragraphs[1].text, "Anchor")
        self.assertEqual(updated.paragraphs[2].text, "{%p endif %}\n")

        before_xml = updated.paragraphs[0]._p.xml
        after_xml = updated.paragraphs[2]._p.xml

        # New paragraphs should contain proper run/text elements, not raw text directly under <w:p>.
        self.assertIn("<w:r>", before_xml)
        self.assertIn("<w:t", before_xml)
        self.assertIn("<w:tab/>", before_xml)
        self.assertIn("<w:br/>", after_xml)

    def test_update_docx_appends_run_when_run_index_is_out_of_bounds(self):
        document = docx.Document()
        document.add_paragraph("Only one run")

        updated = update_docx(document, [(0, 99, "Fallback run", 0)])

        self.assertEqual(updated.paragraphs[0].runs[-1].text, "Fallback run")

    def test_update_docx_ignores_invalid_items_and_accepts_dict_items(self):
        document = docx.Document()
        document.add_paragraph("Original")

        updated = update_docx(
            document,
            [
                {"paragraph": 0, "run": 0, "text": "From dict", "new_paragraph": 0},
                ["bad", "item"],
                None,
            ],
        )

        self.assertEqual(updated.paragraphs[0].runs[0].text, "From dict")

    def test_regex_fallback_labels_obvious_placeholders(self):
        items = [
            [0, 0, "Date: {date}"],
            [1, 0, "Name: ____"],
            [2, 0, "Already {{ labeled }}"],
        ]
        fallback = _regex_placeholder_fallback(items)
        self.assertEqual(len(fallback), 2)
        self.assertIn("{{ date }}", fallback[0][2])
        self.assertIn("{{ value }}", fallback[1][2])

    def test_merge_candidates_prefers_jinja_text(self):
        merged = _merge_label_candidates(
            [(0, 0, "Name: ____", 0)],
            [(0, 0, "Name: {{ users[0].name.full() }}", 0)],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0][2], "Name: {{ users[0].name.full() }}")

    def test_get_docx_run_items_adds_form_control_hints(self):
        document = docx.Document()
        paragraph = document.add_paragraph()
        paragraph.add_run("Field anchor")
        paragraph._element.append(
            parse_xml(
                '<w:fldSimple xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:instr="FORMTEXT customer_name"/>'
            )
        )

        items = get_docx_run_items(document)
        synthetic = [item for item in items if "FORM_CONTROL_HINT" in str(item[2])]
        self.assertEqual(len(synthetic), 1)
        self.assertIn("customer_name", synthetic[0][2])


class TestDocxWranglingModelCompat(unittest.TestCase):
    def test_is_mistral_model(self):
        self.assertTrue(_is_mistral_model("Mistral-Large-3"))
        self.assertFalse(_is_mistral_model("gpt-5-nano"))

    def test_detects_max_completion_tokens_error(self):
        exc = Exception('{"type":"extra_forbidden","loc":["body","max_completion_tokens"]}')
        self.assertTrue(_is_max_completion_tokens_unsupported_error(exc))
        self.assertFalse(_is_max_completion_tokens_unsupported_error(Exception("timeout")))

    @patch("docassemble.ALDashboard.docx_wrangling.OpenAI")
    @patch("docassemble.ALDashboard.docx_wrangling.chat_completion")
    def test_mistral_retries_with_max_tokens(self, mock_chat_completion, mock_openai):
        mock_chat_completion.side_effect = Exception(
            '{"detail":[{"type":"extra_forbidden","loc":["body","max_completion_tokens"]}]}'
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"results": []}'))]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        result = _chat_completion_with_model_compat(
            model="Mistral-Large-3",
            messages=[{"role": "user", "content": "[]"}],
            json_mode=True,
            temperature=0.5,
            max_output_tokens=777,
            openai_client=None,
            openai_api="sk-test",
            openai_base_url="https://example.invalid/openai/v1",
        )

        self.assertEqual(result, '{"results": []}')
        self.assertTrue(mock_client.chat.completions.create.called)
        kwargs = mock_client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 777)
        self.assertNotIn("max_completion_tokens", kwargs)

    @patch("docassemble.ALDashboard.docx_wrangling.chat_completion")
    def test_non_mistral_does_not_retry(self, mock_chat_completion):
        mock_chat_completion.side_effect = Exception("max_completion_tokens not accepted")
        with self.assertRaises(Exception):
            _chat_completion_with_model_compat(
                model="gpt-5-nano",
                messages=[{"role": "user", "content": "[]"}],
                json_mode=True,
                temperature=0.5,
                max_output_tokens=200,
                openai_client=None,
                openai_api="sk-test",
                openai_base_url="https://example.invalid/openai/v1",
            )


if __name__ == "__main__":
    unittest.main()

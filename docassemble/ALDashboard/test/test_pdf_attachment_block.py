# do not pre-load
import unittest

from ruamel.yaml import YAML

from docassemble.ALDashboard.pdf_attachment_block import (
    assembly_line_expression,
    generate_attachment_block,
)


class TestPDFAttachmentBlock(unittest.TestCase):
    def test_repeated_appearance_suffix_is_only_removed_from_expression(self):
        self.assertEqual(assembly_line_expression("users1_name__1"), "users[0]")
        self.assertEqual(
            generate_attachment_block(
                ["users1_name__1"],
                pdf_filename="motion_to_continue.pdf",
            ),
            "---\n"
            "attachment:\n"
            "  name: motion to continue\n"
            "  filename: motion_to_continue\n"
            "  pdf template file: motion_to_continue.pdf\n"
            "  fields:\n"
            '    - "users1_name__1": ${ users[0] }',
        )

    def test_maps_common_assembly_line_person_fields(self):
        self.assertEqual(
            assembly_line_expression("other_parties2_address_city__27"),
            "other_parties[1].address.city",
        )
        self.assertEqual(
            assembly_line_expression("children3_name_first"),
            "children[2].name.first",
        )

    def test_preserves_order_and_skips_exact_duplicate_keys(self):
        self.assertEqual(
            generate_attachment_block(
                ["docket_number", "docket_number", 'field "quoted"'],
                pdf_filename="/tmp/my_form.pdf",
            ),
            "---\n"
            "attachment:\n"
            "  name: my form\n"
            "  filename: my_form\n"
            "  pdf template file: my_form.pdf\n"
            "  fields:\n"
            '    - "docket_number": ${ docket_number }\n'
            '    - "field \\"quoted\\"": ${ field_quoted }',
        )

    def test_adds_pdf_extension_when_missing(self):
        block = generate_attachment_block([], pdf_filename="court_form")
        self.assertIn("pdf template file: court_form.pdf", block)
        self.assertIn("filename: court_form", block)

    def test_generated_attachment_is_valid_yaml_with_list_fields(self):
        block = generate_attachment_block(
            ["users1_name__1", "docket_number"],
            pdf_filename="court_form.pdf",
        )
        parsed = YAML(typ="safe").load(block)

        self.assertEqual(parsed["attachment"]["name"], "court form")
        self.assertEqual(parsed["attachment"]["filename"], "court_form")
        self.assertEqual(parsed["attachment"]["pdf template file"], "court_form.pdf")
        self.assertEqual(
            parsed["attachment"]["fields"],
            [
                {"users1_name__1": "${ users[0] }"},
                {"docket_number": "${ docket_number }"},
            ],
        )


if __name__ == "__main__":
    unittest.main()

# do not pre-load
import io
import os
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from docassemble.ALDashboard.symbol_fonts import (
    is_symbolic_family,
    propose_character,
    propose_declared_text,
    propose_outline_character,
)
from docassemble.ALDashboard.standard_font_metrics import standard_14_widths
from docassemble.ALDashboard.pdf_accessibility import (
    PDFAccessibilityError,
    _expected_font_widths,
    _font_width_match_score,
    _curated_symbol_unicode_cmap,
    _system_embeddable_fonts,
    find_exact_system_font,
    find_metric_compatible_fonts,
    substitute_fonts,
    apply_pdf_accessibility_settings,
    apply_manual_structure_repairs,
    apply_unicode_map_decisions,
    build_accessibility_report,
    collect_symbolic_font_review,
    build_default_field_order,
    create_draft_structure_tree,
    default_pdf_field_tooltip,
    render_image_assets,
    describe_images_with_ai,
    draft_field_tooltips_with_ai,
    review_pdf_accessibility_with_ai,
    embed_fonts_and_rebuild_unicode,
    extract_pdf_field_tooltips,
    ocr_image_only_pages,
    inspect_pdf_accessibility,
    _artifact_untagged_content,
    _iter_pdf_fonts,
    _content_blocks_from_xml,
    _heading_candidates_from_xml,
    _repair_embedded_cidsets,
    _repair_identity_cid_to_gid_maps,
    _font_code_usage,
    _title_from_text_sample,
    analyze_screen_reader_readback,
    _strip_field_name_distinguisher,
    repair_duplicate_field_names,
    repair_readback_text,
    _quoted_title_from_finding,
    _simple_font_unicode_cmap,
    _to_unicode_mappings,
)


def _metric_compatible_sans_path():
    """Find an installed TrueType face with Helvetica metrics, if any."""
    from docassemble.ALDashboard.pdf_accessibility import _font_width_match_score

    import pikepdf

    probe = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type1"),
            "/BaseFont": pikepdf.Name("/Helvetica"),
        }
    )
    for path in (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ):
        if not os.path.exists(path):
            continue
        score = _font_width_match_score(probe, path)
        if score is not None and score <= 2.0:
            return path
    return None


def _webdings_like_program():
    """Build a minimal TrueType subset with outlines but no cmap.

    Real subsetters routinely drop the cmap table, which is exactly why a
    symbol font's codes cannot be resolved from the PDF alone. The square drawn
    at glyph 1 stands in for a checkbox.
    """
    import io

    from fontTools.fontBuilder import FontBuilder  # type: ignore[import-untyped]
    from fontTools.pens.ttGlyphPen import TTGlyphPen  # type: ignore[import-untyped]

    builder = FontBuilder(1000, isTTF=True)
    order = [".notdef", "box"]
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({})
    pen = TTGlyphPen(None)
    pen.moveTo((100, 0))
    pen.lineTo((100, 800))
    pen.lineTo((900, 800))
    pen.lineTo((900, 0))
    pen.closePath()
    empty = TTGlyphPen(None).glyph()
    builder.setupGlyf({".notdef": empty, "box": pen.glyph()})
    builder.setupHorizontalMetrics({name: (1000, 100) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Webdings", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    buffer = io.BytesIO()
    builder.save(buffer)
    program = buffer.getvalue()

    # Strip the cmap the builder inserts, mirroring a real subset font.
    from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

    font = TTFont(io.BytesIO(program))
    if "cmap" in font:
        del font["cmap"]
    stripped = io.BytesIO()
    font.save(stripped)
    return stripped.getvalue()


def _simple_symbol_program(*, box_glyph_id, decoy_glyph_id, code):
    """A symbolic TrueType that keeps its (3,0) cmap, as Word's Wingdings does.

    ``code`` selects ``box`` through the cmap, while ``decoy_glyph_id`` holds a
    different outline at the glyph index equal to the character code. Reading
    the code as a glyph id therefore shows the wrong shape.
    """
    import io

    from fontTools.fontBuilder import FontBuilder  # type: ignore[import-untyped]
    from fontTools.pens.ttGlyphPen import TTGlyphPen  # type: ignore[import-untyped]

    order = [".notdef"]
    while len(order) <= max(box_glyph_id, decoy_glyph_id):
        index = len(order)
        order.append(
            "box"
            if index == box_glyph_id
            else "decoy" if index == decoy_glyph_id else f"g{index}"
        )
    box = TTGlyphPen(None)
    box.moveTo((100, 0))
    box.lineTo((100, 800))
    box.lineTo((900, 800))
    box.lineTo((900, 0))
    box.closePath()
    decoy = TTGlyphPen(None)
    decoy.moveTo((0, 0))
    decoy.lineTo((500, 500))
    decoy.lineTo((0, 500))
    decoy.closePath()

    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({0xF000 | code: "box"}, allowFallback=True)
    glyphs = {name: TTGlyphPen(None).glyph() for name in order}
    glyphs["box"] = box.glyph()
    glyphs["decoy"] = decoy.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (1000, 100) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Wingdings", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    buffer = io.BytesIO()
    builder.save(buffer)

    # Replace the builder's Unicode subtables with the (3,0) symbol subtable a
    # real symbolic font carries.
    from fontTools.ttLib import TTFont  # type: ignore[import-untyped]
    from fontTools.ttLib.tables._c_m_a_p import (  # type: ignore[import-untyped]
        CmapSubtable,
    )

    font = TTFont(io.BytesIO(buffer.getvalue()))
    subtable = CmapSubtable.newSubtable(4)
    subtable.platformID = 3
    subtable.platEncID = 0
    subtable.language = 0
    subtable.cmap = {0xF000 | code: "box"}
    font["cmap"].tables = [subtable]
    symbolic = io.BytesIO()
    font.save(symbolic)
    return symbolic.getvalue()


class TestPDFAccessibilityHelpers(unittest.TestCase):
    def test_ai_reasonableness_review_preserves_only_allowlisted_changes(self):
        with patch(
            "docassemble.ALToolbox.llms.chat_completion",
            return_value={
                "findings": [
                    {
                        "id": "poor-title",
                        "category": "metadata",
                        "severity": "warning",
                        "title": "Use the document heading as its title",
                        "explanation": "The current title is a working filename.",
                        "change": {
                            "kind": "metadata",
                            "target": "title",
                            "value": "Petition for Child Custody",
                        },
                    },
                    {
                        "id": "unsafe-declaration",
                        "category": "catalog",
                        "severity": "warning",
                        "title": "Invalid unsupported change",
                        "explanation": "The model must not set the declaration.",
                        "change": {
                            "kind": "catalog_flags",
                            "target": "marked",
                            "value": True,
                        },
                    },
                ]
            },
        ) as completion:
            result = review_pdf_accessibility_with_ai(
                {
                    "filename": "Form draft 1.docx.pdf",
                    "metadata": {"title": "Form draft 1.docx", "language": "en-US"},
                    "textSample": "Petition for Child Custody",
                    "fields": [],
                    "headings": [],
                    "images": [],
                }
            )

        self.assertEqual(result[0]["change"]["value"], "Petition for Child Custody")
        self.assertNotIn("change", result[1])
        system_prompt = completion.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Preserve a reasonable existing value", system_prompt)
        self.assertIn("BCP 47 language", system_prompt)
        self.assertIn("never propose changing MarkInfo", system_prompt)

    def test_h1_title_finding_is_actionable_without_the_word_metadata(self):
        """A prose-only suggestion still has to be applicable in one click."""
        with patch(
            "docassemble.ALToolbox.llms.chat_completion",
            return_value={
                "findings": [
                    {
                        "id": "title-from-heading",
                        "category": "document-title",
                        "severity": "warning",
                        "title": "The H1 would make a better document title",
                        "explanation": (
                            "The document opens with a clear heading; the stored "
                            "title is the working filename."
                        ),
                    }
                ]
            },
        ):
            result = review_pdf_accessibility_with_ai(
                {
                    "filename": "scan0001.pdf",
                    "metadata": {"title": "scan0001", "language": "en-US"},
                    "textSample": "Petition for Name Change",
                    "fields": [],
                    "headings": [
                        {
                            "text": "Petition for Name Change",
                            "tag": "H1",
                            "status": "approved",
                        }
                    ],
                    "images": [],
                }
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["change"],
            {
                "kind": "metadata",
                "target": "title",
                "value": "Petition for Name Change",
            },
        )

    def test_generic_filename_title_is_applicable_without_a_heading_review(self):
        """Verbatim finding from a real run; no heading review had been done."""
        with patch(
            "docassemble.ALToolbox.llms.chat_completion",
            return_value={
                "findings": [
                    {
                        "id": "generic-title",
                        "category": "metadata",
                        "severity": "warning",
                        "title": "Document title appears to be a generic filename",
                        "explanation": (
                            'The PDF metadata title is currently "Microsoft Word - '
                            'PS-05.doc", which appears to be a generic filename '
                            "rather than the document's actual approved heading."
                        ),
                    }
                ]
            },
        ):
            result = review_pdf_accessibility_with_ai(
                {
                    "filename": "PS-05.pdf",
                    "metadata": {"title": "Microsoft Word - PS-05.doc"},
                    "textSample": (
                        "Microsoft Word - PS-05.doc\n"
                        "Petition for Protection from Abuse\n"
                        "Name of petitioner:"
                    ),
                    "fields": [],
                    "headings": [],
                    "images": [],
                }
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["change"],
            {
                "kind": "metadata",
                "target": "title",
                "value": "Petition for Protection from Abuse",
            },
        )

    def test_bcp47_language_finding_falls_back_to_the_editor_language(self):
        """The finding names no language, so the tag has to come from context."""
        with patch(
            "docassemble.ALToolbox.llms.chat_completion",
            return_value={
                "findings": [
                    {
                        "id": "bcp47",
                        "category": "metadata",
                        "severity": "warning",
                        "title": "Metadata language should use a specific BCP 47 tag",
                        "explanation": "The declared language is not a specific tag.",
                    }
                ]
            },
        ):
            result = review_pdf_accessibility_with_ai(
                {
                    "filename": "form.pdf",
                    "metadata": {"title": "A form", "language": "en"},
                    "documentLanguage": "en-US",
                    "textSample": "A form",
                    "fields": [],
                    "headings": [],
                    "images": [],
                }
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["change"],
            {"kind": "metadata", "target": "language", "value": "en-US"},
        )

    def test_a_change_that_fails_the_allow_list_still_gets_reconstructed(self):
        """A near-miss target used to suppress the fallback and leave no button."""
        row = {
            "id": "lang",
            "category": "metadata",
            "severity": "warning",
            "title": "Document language metadata is too generic",
            "explanation": (
                'The file metadata currently lists language as "en". The text is '
                "US English (dates, addresses, and references to Alabama)."
            ),
        }
        for broken in (
            {"kind": "metadata", "target": "locale", "value": "en-US"},
            {"kind": "language", "target": "language", "value": "en-US"},
            {"kind": "metadata", "target": "language", "value": {"tag": "en-US"}},
            {"kind": "metadata", "target": "language", "value": "not a tag!"},
        ):
            with self.subTest(broken=broken):
                with patch(
                    "docassemble.ALToolbox.llms.chat_completion",
                    return_value={"findings": [dict(row, change=broken)]},
                ):
                    result = review_pdf_accessibility_with_ai(
                        {
                            "filename": "form.pdf",
                            "metadata": {"title": "A form", "language": "en"},
                            "textSample": "A form",
                            "fields": [],
                            "headings": [],
                            "images": [],
                        }
                    )
                self.assertEqual(
                    result[0]["change"],
                    {"kind": "metadata", "target": "language", "value": "en-US"},
                )

    def test_title_finding_uses_the_h1_it_quotes_when_candidates_are_gone(self):
        """Verbatim finding from a real run; the H1 is named in the prose."""
        with patch(
            "docassemble.ALToolbox.llms.chat_completion",
            return_value={
                "findings": [
                    {
                        "id": "title",
                        "category": "metadata",
                        "severity": "warning",
                        "title": "Document title appears to be a filename",
                        "explanation": (
                            'The document title in metadata is "Microsoft Word - '
                            'PS-05.doc", which is a generic, filename-like title. '
                            "The document's approved top-level heading (H1) is "
                            "'First Petition for Child Custody' and should be used "
                            "as the metadata title."
                        ),
                    }
                ]
            },
        ):
            result = review_pdf_accessibility_with_ai(
                {
                    "filename": "PS-05.pdf",
                    "metadata": {"title": "Microsoft Word - PS-05.doc"},
                    "textSample": "",
                    "fields": [],
                    "headings": [],
                    "images": [],
                }
            )

        self.assertEqual(
            result[0]["change"],
            {
                "kind": "metadata",
                "target": "title",
                "value": "First Petition for Child Custody",
            },
        )

    def test_quoted_title_ignores_apostrophes_and_the_current_title(self):
        self.assertEqual(
            _quoted_title_from_finding(
                "The document's heading is 'First Petition for Child Custody'.",
                "Microsoft Word - PS-05.doc",
            ),
            "First Petition for Child Custody",
        )
        # The only quoted value is the title being complained about.
        self.assertEqual(
            _quoted_title_from_finding('The title "scan0001" is poor.', "scan0001"),
            "",
        )
        self.assertEqual(
            _quoted_title_from_finding('Replace "form.pdf" with it.', "x"), ""
        )

    def test_title_candidate_skips_filenames_and_form_labels(self):
        self.assertEqual(
            _title_from_text_sample(
                "Microsoft Word - PS-05.doc\n  \n123\nName of petitioner:\n"
                "Petition for Protection from Abuse"
            ),
            "Petition for Protection from Abuse",
        )
        self.assertEqual(_title_from_text_sample(""), "")

    def test_title_finding_stays_advisory_when_the_title_already_matches(self):
        with patch(
            "docassemble.ALToolbox.llms.chat_completion",
            return_value={
                "findings": [
                    {
                        "id": "title-from-heading",
                        "category": "document-title",
                        "severity": "info",
                        "title": "Check the document title",
                        "explanation": "The title should match the heading.",
                    }
                ]
            },
        ):
            result = review_pdf_accessibility_with_ai(
                {
                    "filename": "petition.pdf",
                    "metadata": {
                        "title": "Petition for Name Change",
                        "language": "en-US",
                    },
                    "textSample": "Petition for Name Change",
                    "fields": [],
                    "headings": [
                        {
                            "text": "Petition for Name Change",
                            "tag": "H1",
                            "status": "approved",
                        }
                    ],
                    "images": [],
                }
            )

        self.assertEqual(len(result), 1)
        self.assertNotIn("change", result[0])

    def test_ai_reasonableness_review_recovers_safe_metadata_actions(self):
        with patch(
            "docassemble.ALToolbox.llms.chat_completion",
            return_value={
                "findings": [
                    {
                        "id": "title",
                        "category": "metadata",
                        "severity": "warning",
                        "title": "Document title is a filename",
                        "explanation": "Use the real H1 instead.",
                    },
                    {
                        "id": "language",
                        "category": "metadata",
                        "severity": "warning",
                        "title": "Document language is not declared",
                        "explanation": "The document is written in US English.",
                    },
                ]
            },
        ):
            result = review_pdf_accessibility_with_ai(
                {
                    "metadata": {},
                    "textSample": "First Petition for Child Custody",
                    "headings": [
                        {
                            "candidateId": "heading-1",
                            "text": "First Petition for Child Custody",
                            "status": "approved",
                            "tag": "H1",
                        }
                    ],
                }
            )

        self.assertEqual(result[0]["change"]["target"], "title")
        self.assertEqual(
            result[0]["change"]["value"], "First Petition for Child Custody"
        )
        self.assertEqual(result[1]["change"]["value"], "en-US")

    def test_ai_tooltip_prompt_requires_short_labels_not_instructions(self):
        with patch(
            "docassemble.ALToolbox.llms.chat_completion",
            return_value={
                "tooltips": [{"name": "users1_name", "tooltip": "Full name"}]
            },
        ) as completion:
            result = draft_field_tooltips_with_ai(
                [
                    {
                        "name": "users1_name",
                        "type": "text",
                        "nearby_text": ["Your full legal name"],
                    }
                ]
            )

        self.assertEqual(result, {"users1_name": "Full name"})
        system_prompt = completion.call_args.kwargs["messages"][0]["content"]
        self.assertIn("at most 45 characters", system_prompt)
        self.assertIn("not an instruction", system_prompt)
        self.assertIn("Do not begin with Enter", system_prompt)
        self.assertIn("sentence fragment", system_prompt)
        self.assertGreaterEqual(completion.call_args.kwargs["max_output_tokens"], 8192)

    def test_direct_font_objects_use_resource_fallback_identity(self):
        import pikepdf

        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(612, 792))
        page.obj["/Resources"] = pikepdf.Dictionary(
            {
                "/Font": pikepdf.Dictionary(
                    {
                        "/F1": pikepdf.Dictionary(
                            {
                                "/Type": pikepdf.Name("/Font"),
                                "/Subtype": pikepdf.Name("/Type1"),
                                "/BaseFont": pikepdf.Name("/Helvetica"),
                            }
                        ),
                        "/F2": pikepdf.Dictionary(
                            {
                                "/Type": pikepdf.Name("/Font"),
                                "/Subtype": pikepdf.Name("/Type1"),
                                "/BaseFont": pikepdf.Name("/Courier"),
                            }
                        ),
                    }
                )
            }
        )
        self.assertEqual(
            [resource for resource, _font in _iter_pdf_fonts(pdf)], ["p1/F1", "p1/F2"]
        )
        pdf.close()

    def test_exact_font_match_requires_the_same_style(self):
        import pikepdf

        pdf_font = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
        inventory = [
            {
                "path": "/fonts/Helvetica-Oblique.ttf",
                "canonical_names": {"helvetica", "helveticaoblique"},
                "embeddable": True,
                "bold": False,
                "italic": True,
            },
            {
                "path": "/fonts/Helvetica.ttf",
                "canonical_names": {"helvetica"},
                "embeddable": True,
                "bold": False,
                "italic": False,
            },
        ]
        with patch(
            "docassemble.ALDashboard.pdf_accessibility._font_width_match_score",
            return_value=0.0,
        ):
            match = find_exact_system_font(pdf_font, inventory)

        self.assertEqual(match["path"], "/fonts/Helvetica.ttf")

    def test_font_inventory_includes_appearance_state_streams(self):
        import pikepdf

        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(612, 792))
        font = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/ZapfDingbats"),
            }
        )
        off = pdf.make_stream(b"")
        on = pdf.make_stream(b"BT /ZaDb 12 Tf (4) Tj ET")
        on["/Resources"] = pikepdf.Dictionary(
            {"/Font": pikepdf.Dictionary({"/ZaDb": font})}
        )
        widget = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/Widget"),
                "/AP": pikepdf.Dictionary(
                    {"/N": pikepdf.Dictionary({"/Off": off, "/Yes": on})}
                ),
            }
        )
        page.obj["/Annots"] = pikepdf.Array([widget])

        fonts = list(_iter_pdf_fonts(pdf))

        self.assertEqual(len(fonts), 1)
        self.assertEqual(str(fonts[0][1]["/BaseFont"]), "/ZapfDingbats")
        pdf.close()

    def test_artifact_layout_runs_inside_form_xobjects_without_hiding_text(self):
        import pikepdf

        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(612, 792))
        layout = pdf.make_stream(b"0 0 20 20 re S")
        layout["/Type"] = pikepdf.Name("/XObject")
        layout["/Subtype"] = pikepdf.Name("/Form")
        text = pdf.make_stream(b"BT (Visible text) Tj ET")
        text["/Type"] = pikepdf.Name("/XObject")
        text["/Subtype"] = pikepdf.Name("/Form")
        page.obj["/Resources"] = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Layout": layout, "/Text": text})}
        )

        changed = _artifact_untagged_content(pdf)

        self.assertEqual(changed, 1)
        self.assertIn(b"/Artifact BMC", layout.read_bytes())
        self.assertNotIn(b"/Artifact", text.read_bytes())
        pdf.close()

    def test_artifact_wrapping_leaves_a_run_that_draws_an_image_alone(self):
        """An image in the run may be meaningful, and the whole run is wrapped."""
        import pikepdf

        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(612, 792))
        image = pdf.make_stream(b"\x00")
        image["/Type"] = pikepdf.Name("/XObject")
        image["/Subtype"] = pikepdf.Name("/Image")
        image["/Width"] = 1
        image["/Height"] = 1
        page.obj["/Resources"] = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Im0": image})}
        )
        page.obj["/Contents"] = pdf.make_stream(b"0 0 20 20 re S /Im0 Do")

        changed = _artifact_untagged_content(pdf)

        self.assertEqual(changed, 0)
        self.assertNotIn(b"/Artifact", page.obj["/Contents"].read_bytes())
        pdf.close()

    def test_metadata_repair_keeps_docinfo_keys_that_have_no_xmp_entry(self):
        """pikepdf rewrites docinfo from XMP on exit unless that is turned off."""
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = tmp.name
        try:
            pdf = pikepdf.new()
            pdf.add_blank_page(page_size=(612, 792))
            pdf.save(pdf_path)
            pdf.close()

            apply_pdf_accessibility_settings(
                input_pdf_path=pdf_path,
                output_pdf_path=pdf_path,
                metadata={
                    "title": "Court form",
                    "language": "en-US",
                    "author": "Clerk of Court",
                    "subject": "Civil docketing",
                },
                auto_fill_missing_tooltips=False,
            )

            with pikepdf.open(pdf_path) as repaired:
                self.assertEqual(str(repaired.docinfo["/Title"]), "Court form")
                self.assertEqual(str(repaired.docinfo["/Author"]), "Clerk of Court")
                self.assertEqual(str(repaired.docinfo["/Subject"]), "Civil docketing")
        finally:
            os.remove(pdf_path)

    def test_declaring_pdfua_keeps_an_existing_title_with_no_dc_title(self):
        """Certifying must not delete the title the document-title check reads."""
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = tmp.name
        try:
            pdf = pikepdf.new()
            pdf.add_blank_page(page_size=(612, 792))
            pdf.docinfo["/Title"] = "Existing title"
            with pdf.open_metadata(
                set_pikepdf_as_editor=False, update_docinfo=False
            ) as meta:
                meta["dc:creator"] = ["Clerk of Court"]
            pdf.save(pdf_path)
            pdf.close()

            apply_pdf_accessibility_settings(
                input_pdf_path=pdf_path,
                output_pdf_path=pdf_path,
                metadata={},
                auto_fill_missing_tooltips=False,
                mark_as_tagged=True,
            )

            with pikepdf.open(pdf_path) as repaired:
                self.assertEqual(str(repaired.docinfo["/Title"]), "Existing title")
        finally:
            os.remove(pdf_path)

    def test_draft_structure_uses_only_approved_heading_decisions(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            page.obj["/Resources"] = pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary(
                        {
                            "/F1": pikepdf.Dictionary(
                                {
                                    "/Type": pikepdf.Name("/Font"),
                                    "/Subtype": pikepdf.Name("/Type1"),
                                    "/BaseFont": pikepdf.Name("/Helvetica"),
                                }
                            )
                        }
                    )
                }
            )
            page.obj["/Contents"] = pdf.make_stream(
                b"BT /F1 18 Tf 72 700 Td (Approved title) Tj ET "
                b"BT /F1 16 Tf 72 660 Td (Rejected label) Tj ET"
            )
            pdf.save(source_path)
            pdf.close()
            candidates = [
                {
                    "candidateId": "title",
                    "pageIndex": 0,
                    "text": "Approved title",
                    "suggestedTag": "H1",
                },
                {
                    "candidateId": "label",
                    "pageIndex": 0,
                    "text": "Rejected label",
                    "suggestedTag": "H2",
                },
            ]
            with patch(
                "docassemble.ALDashboard.pdf_accessibility.suggest_heading_candidates",
                return_value=candidates,
            ):
                result = create_draft_structure_tree(
                    source_path,
                    output_path,
                    heading_decisions=[
                        {"candidateId": "title", "status": "approved", "tag": "H3"},
                        {"candidateId": "label", "status": "rejected", "tag": "H2"},
                    ],
                )
            self.assertEqual(result["headings_drafted"], 1)
            with pikepdf.open(output_path) as tagged:
                part = tagged.Root.StructTreeRoot.K.K[0]
                self.assertEqual([str(child.S) for child in part.K], ["/H1", "/P"])
                self.assertEqual(result["heading_levels_normalized"], 1)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_visual_content_decisions_set_roles_order_and_artifacts(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            page.obj["/Resources"] = pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary(
                        {
                            "/F1": pikepdf.Dictionary(
                                {
                                    "/Type": pikepdf.Name("/Font"),
                                    "/Subtype": pikepdf.Name("/Type1"),
                                    "/BaseFont": pikepdf.Name("/Helvetica"),
                                }
                            )
                        }
                    )
                }
            )
            page.obj["/Contents"] = pdf.make_stream(
                b"BT /F1 12 Tf 72 700 Td (Alpha) Tj ET "
                b"BT /F1 12 Tf 72 660 Td (Beta) Tj ET "
                b"BT /F1 12 Tf 72 620 Td (Decoration) Tj ET"
            )
            pdf.save(source_path)
            pdf.close()
            with patch(
                "docassemble.ALDashboard.pdf_accessibility.suggest_heading_candidates",
                return_value=[],
            ):
                result = create_draft_structure_tree(
                    source_path,
                    output_path,
                    heading_decisions=[],
                    content_decisions=[
                        {
                            "pageIndex": 0,
                            "text": "Alpha",
                            "occurrence": 0,
                            "role": "LI",
                            "roleReviewed": True,
                            "order": 1,
                        },
                        {
                            "pageIndex": 0,
                            "text": "Beta",
                            "occurrence": 0,
                            "role": "LI",
                            "roleReviewed": True,
                            "order": 0,
                        },
                        {
                            "pageIndex": 0,
                            "text": "Decoration",
                            "occurrence": 0,
                            "role": "Artifact",
                            "roleReviewed": True,
                            "order": 2,
                        },
                    ],
                )
            self.assertEqual(result["manual_artifact_blocks"], 1)
            with pikepdf.open(output_path) as tagged:
                part = tagged.Root.StructTreeRoot.K.K[0]
                self.assertEqual([str(child.S) for child in part.K], ["/L"])
                list_items = list(part.K[0].K)
                self.assertEqual([str(item.S) for item in list_items], ["/LI", "/LI"])
                self.assertEqual(
                    [int(item.K[0].K) for item in list_items],
                    [1, 0],
                )
                self.assertIn(b"/Artifact BMC", tagged.pages[0].Contents.read_bytes())
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_visual_content_blocks_have_stable_boxes_and_occurrences(self):
        root = ET.fromstring(
            """<pdf2xml><fontspec id="0" size="12" family="Arial"/>
            <page width="600" height="800">
              <text top="100" left="50" width="80" height="14" font="0">Same</text>
              <text top="150" left="50" width="80" height="14" font="0">Same</text>
            </page></pdf2xml>"""
        )
        blocks = _content_blocks_from_xml(root)
        self.assertEqual([block["occurrence"] for block in blocks], [0, 1])
        self.assertNotEqual(blocks[0]["blockId"], blocks[1]["blockId"])
        self.assertAlmostEqual(blocks[0]["box"]["x"], 50 / 600)

    def test_draft_structure_prevents_heading_sequence_gaps(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            page.obj["/Resources"] = pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary(
                        {
                            "/F1": pikepdf.Dictionary(
                                {
                                    "/Type": pikepdf.Name("/Font"),
                                    "/Subtype": pikepdf.Name("/Type1"),
                                    "/BaseFont": pikepdf.Name("/Helvetica"),
                                }
                            )
                        }
                    )
                }
            )
            page.obj["/Contents"] = pdf.make_stream(
                b"BT /F1 18 Tf 72 700 Td (Title) Tj ET "
                b"BT /F1 14 Tf 72 660 Td (Section) Tj ET "
                b"BT /F1 16 Tf 72 620 Td (Later section) Tj ET"
            )
            pdf.save(source_path)
            pdf.close()
            candidates = [
                {
                    "candidateId": "title",
                    "pageIndex": 0,
                    "text": "Title",
                    "suggestedTag": "H1",
                },
                {
                    "candidateId": "section",
                    "pageIndex": 0,
                    "text": "Section",
                    "suggestedTag": "H3",
                },
                {
                    "candidateId": "later",
                    "pageIndex": 0,
                    "text": "Later section",
                    "suggestedTag": "H2",
                },
            ]
            with patch(
                "docassemble.ALDashboard.pdf_accessibility.suggest_heading_candidates",
                return_value=candidates,
            ):
                result = create_draft_structure_tree(source_path, output_path)

            self.assertEqual(result["heading_levels_normalized"], 1)
            with pikepdf.open(output_path) as tagged:
                part = tagged.Root.StructTreeRoot.K.K[0]
                self.assertEqual(
                    [str(child.S) for child in part.K], ["/H1", "/H2", "/H2"]
                )
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_draft_structure_starts_after_existing_content_mcids(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            page.obj["/Resources"] = pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary(
                        {
                            "/F1": pikepdf.Dictionary(
                                {
                                    "/Type": pikepdf.Name("/Font"),
                                    "/Subtype": pikepdf.Name("/Type1"),
                                    "/BaseFont": pikepdf.Name("/Helvetica"),
                                }
                            )
                        }
                    )
                }
            )
            page.obj["/Contents"] = pdf.make_stream(
                b"/Span <</MCID 5>> BDC EMC "
                b"BT /F1 12 Tf 72 700 Td (New paragraph) Tj ET"
            )
            pdf.save(source_path)
            pdf.close()

            create_draft_structure_tree(source_path, output_path)

            with pikepdf.open(output_path) as tagged:
                page_part = tagged.Root.StructTreeRoot.K.K[0]
                paragraph = next(child for child in page_part.K if str(child.S) == "/P")
                self.assertEqual(int(paragraph.K), 6)
                parent_entries = tagged.Root.StructTreeRoot.ParentTree.Nums[1]
                self.assertEqual(len(parent_entries), 7)
                self.assertIsNone(parent_entries[5])
                self.assertEqual(str(parent_entries[6].S), "/P")
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_draft_structure_overwrite_removes_stale_mcids(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            page["/Resources"] = pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary(
                        {
                            "/F1": pikepdf.Dictionary(
                                {
                                    "/Type": pikepdf.Name("/Font"),
                                    "/Subtype": pikepdf.Name("/Type1"),
                                    "/BaseFont": pikepdf.Name("/Helvetica"),
                                }
                            )
                        }
                    )
                }
            )
            page["/Contents"] = pdf.make_stream(
                b"/P <</MCID 9>> BDC BT /F1 12 Tf (Retag me) Tj ET EMC"
            )
            pdf.Root["/StructTreeRoot"] = pikepdf.Dictionary()
            pdf.save(source_path)
            pdf.close()

            result = create_draft_structure_tree(
                source_path, output_path, overwrite=True
            )

            self.assertEqual(result["stale_mcid_wrappers_removed"], 2)
            with pikepdf.open(output_path) as tagged:
                content = tagged.pages[0].Contents.read_bytes()
                self.assertNotIn(b"/MCID 9", content)
                paragraph = tagged.Root.StructTreeRoot.K.K[0].K[0]
                self.assertEqual(int(paragraph.K), 0)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_draft_structure_tags_links_and_other_annotations(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            link = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Annot"),
                        "/Subtype": pikepdf.Name("/Link"),
                        "/Rect": pikepdf.Array([0, 0, 100, 20]),
                        "/A": pikepdf.Dictionary(
                            {
                                "/S": pikepdf.Name("/URI"),
                                "/URI": pikepdf.String("https://example.org"),
                            }
                        ),
                    }
                )
            )
            stamp = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Annot"),
                        "/Subtype": pikepdf.Name("/Stamp"),
                        "/Rect": pikepdf.Array([0, 30, 100, 50]),
                    }
                )
            )
            page["/Annots"] = pikepdf.Array([link, stamp])
            pdf.save(source_path)
            pdf.close()

            result = create_draft_structure_tree(source_path, output_path)

            self.assertEqual(result["annotations_tagged"], 2)
            self.assertEqual(result["annotation_descriptions_added"], 2)
            with pikepdf.open(output_path) as tagged:
                part = tagged.Root.StructTreeRoot.K.K[0]
                self.assertEqual(
                    [str(child.S) for child in part.K], ["/Link", "/Annot"]
                )
                self.assertEqual(
                    str(tagged.pages[0].Annots[0].Contents), "https://example.org"
                )
                self.assertEqual(
                    str(tagged.pages[0].Annots[1].Contents), "Stamp annotation"
                )
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_draft_structure_matches_multiline_heading_in_one_text_block(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            page.obj["/Resources"] = pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary(
                        {
                            "/F1": pikepdf.Dictionary(
                                {
                                    "/Type": pikepdf.Name("/Font"),
                                    "/Subtype": pikepdf.Name("/Type1"),
                                    "/BaseFont": pikepdf.Name("/Helvetica"),
                                }
                            )
                        }
                    )
                }
            )
            page.obj["/Contents"] = pdf.make_stream(
                b"BT /F1 18 Tf 72 700 Td (Multi) Tj T* "
                b"(line heading) Tj /F1 12 Tf T* (Body text) Tj ET"
            )
            pdf.save(source_path)
            pdf.close()

            candidates = [
                {
                    "candidateId": "multiline",
                    "pageIndex": 0,
                    "text": "Multi line heading",
                    "suggestedTag": "H2",
                }
            ]
            with patch(
                "docassemble.ALDashboard.pdf_accessibility.suggest_heading_candidates",
                return_value=candidates,
            ):
                result = create_draft_structure_tree(
                    source_path,
                    output_path,
                    heading_decisions=[
                        {
                            "candidateId": "multiline",
                            "status": "approved",
                            "tag": "H2",
                        }
                    ],
                )

            self.assertEqual(result["headings_drafted"], 1)
            with pikepdf.open(output_path) as tagged:
                part = tagged.Root.StructTreeRoot.K.K[0]
                self.assertEqual([str(child.S) for child in part.K], ["/H1", "/P"])
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_draft_structure_keeps_artifact_text_out_of_tag_tree(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            page.obj["/Resources"] = pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary(
                        {
                            "/F1": pikepdf.Dictionary(
                                {
                                    "/Type": pikepdf.Name("/Font"),
                                    "/Subtype": pikepdf.Name("/Type1"),
                                    "/BaseFont": pikepdf.Name("/Helvetica"),
                                }
                            )
                        }
                    )
                }
            )
            page.obj["/Contents"] = pdf.make_stream(
                b"/Artifact BMC BT /F1 10 Tf 72 750 Td (Repeated header) Tj ET EMC "
                b"BT /F1 12 Tf 72 700 Td (Body text) Tj ET"
            )
            pdf.save(source_path)
            pdf.close()

            candidates = [
                {
                    "candidateId": "header",
                    "pageIndex": 0,
                    "text": "Repeated header",
                    "suggestedTag": "H1",
                }
            ]
            with patch(
                "docassemble.ALDashboard.pdf_accessibility.suggest_heading_candidates",
                return_value=candidates,
            ):
                result = create_draft_structure_tree(
                    source_path,
                    output_path,
                    heading_decisions=[
                        {
                            "candidateId": "header",
                            "status": "approved",
                            "tag": "H1",
                        }
                    ],
                )

            self.assertEqual(result["headings_drafted"], 0)
            self.assertEqual(result["text_blocks_tagged"], 1)
            with pikepdf.open(output_path) as tagged:
                part = tagged.Root.StructTreeRoot.K.K[0]
                self.assertEqual([str(child.S) for child in part.K], ["/P"])
                content = bytes(tagged.pages[0].Contents.read_bytes())
                self.assertIn(b"/Artifact BMC", content)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_field_order_updates_acroform_annotations_and_form_tags(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tagged:
            tagged_path = tagged.name
        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            fields = []
            for index, name in enumerate(("first", "second")):
                field = pdf.make_indirect(
                    pikepdf.Dictionary(
                        {
                            "/FT": pikepdf.Name("/Tx"),
                            "/T": pikepdf.String(name),
                            "/Type": pikepdf.Name("/Annot"),
                            "/Subtype": pikepdf.Name("/Widget"),
                            "/Rect": pikepdf.Array(
                                [0, index * 30, 100, index * 30 + 20]
                            ),
                        }
                    )
                )
                fields.append(field)
            link = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Annot"),
                        "/Subtype": pikepdf.Name("/Link"),
                        "/Rect": pikepdf.Array([110, 0, 150, 20]),
                    }
                )
            )
            page.obj["/Annots"] = pikepdf.Array([fields[0], link, fields[1]])
            pdf.Root["/AcroForm"] = pikepdf.Dictionary(
                {"/Fields": pikepdf.Array(fields)}
            )
            pdf.save(source_path)
            pdf.close()
            create_draft_structure_tree(source_path, tagged_path)

            result = apply_pdf_accessibility_settings(
                input_pdf_path=tagged_path,
                output_pdf_path=tagged_path,
                field_order=["second", "first"],
                auto_fill_missing_tooltips=False,
                set_structure_tab_order=True,
            )

            self.assertEqual(result["structure_order_updates"], 2)
            self.assertEqual(result["tab_order_updates"], 0)
            with pikepdf.open(tagged_path) as reordered:
                self.assertEqual(
                    [str(field["/T"]) for field in reordered.Root.AcroForm.Fields],
                    ["second", "first"],
                )
                self.assertEqual(
                    [
                        (
                            str(annotation["/T"])
                            if "/T" in annotation
                            else str(annotation["/Subtype"])
                        )
                        for annotation in reordered.pages[0].Annots
                    ],
                    ["second", "/Link", "first"],
                )
                part = reordered.Root.StructTreeRoot.K.K[0]
                form_names = [
                    str(child.K.Obj.T) for child in part.K if str(child.S) == "/Form"
                ]
                self.assertEqual(form_names, ["second", "first"])
                self.assertEqual(str(reordered.pages[0].Tabs), "/S")
        finally:
            os.remove(source_path)
            os.remove(tagged_path)

    def test_manual_structure_repairs_cover_tables_figures_and_annotations(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            link = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Annot"),
                        "/Subtype": pikepdf.Name("/Link"),
                        "/Rect": pikepdf.Array([10, 10, 40, 30]),
                    }
                )
            )
            note = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Annot"),
                        "/Subtype": pikepdf.Name("/Text"),
                        "/Rect": pikepdf.Array([50, 10, 80, 30]),
                    }
                )
            )
            widget = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Annot"),
                        "/Subtype": pikepdf.Name("/Widget"),
                        "/FT": pikepdf.Name("/Btn"),
                        "/Rect": pikepdf.Array([90, 10, 110, 30]),
                    }
                )
            )
            page.obj["/Annots"] = pikepdf.Array([link, note, widget])
            struct_root = pdf.make_indirect(
                pikepdf.Dictionary({"/Type": pikepdf.Name("/StructTreeRoot")})
            )
            document = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/StructElem"),
                        "/S": pikepdf.Name("/Document"),
                        "/P": struct_root,
                    }
                )
            )
            table = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/StructElem"),
                        "/S": pikepdf.Name("/Table"),
                        "/P": document,
                        "/Pg": page.obj,
                    }
                )
            )
            first_row = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/StructElem"),
                        "/S": pikepdf.Name("/TR"),
                        "/P": table,
                        "/Pg": page.obj,
                    }
                )
            )
            second_row = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/StructElem"),
                        "/S": pikepdf.Name("/TR"),
                        "/P": table,
                        "/Pg": page.obj,
                    }
                )
            )

            def cell(role, parent):
                return pdf.make_indirect(
                    pikepdf.Dictionary(
                        {
                            "/Type": pikepdf.Name("/StructElem"),
                            "/S": pikepdf.Name(f"/{role}"),
                            "/P": parent,
                            "/Pg": page.obj,
                        }
                    )
                )

            header = cell("TH", first_row)
            data = cell("TD", first_row)
            invalid = cell("P", first_row)
            first_row["/K"] = pikepdf.Array([header, data, invalid])
            second_row["/K"] = pikepdf.Array([cell("TD", second_row)])
            table["/K"] = pikepdf.Array([first_row, second_row])
            figure = cell("Figure", document)
            document["/K"] = pikepdf.Array([table, figure])
            struct_root["/K"] = document
            struct_root["/ParentTree"] = pdf.make_indirect(
                pikepdf.Dictionary({"/Nums": pikepdf.Array([])})
            )
            struct_root["/ParentTreeNextKey"] = 0
            pdf.Root["/StructTreeRoot"] = struct_root
            pdf.save(source_path)
            pdf.close()

            before = inspect_pdf_accessibility(source_path)["structure_editor"]
            self.assertEqual(before["tables"][0]["targetColumns"], 2)
            self.assertEqual(
                set(before["tables"][0]["issueIds"]),
                {
                    "table-row-children",
                    "table-columns",
                    "table-header-scope",
                },
            )
            self.assertEqual(before["figures"][0]["issueIds"], ["figure-structure-alt"])
            self.assertFalse(before["annotations"][0]["tagged"])
            self.assertEqual(before["widgets"][0]["issueIds"], ["field_tooltips"])
            before_report = {
                issue["id"]: issue
                for issue in inspect_pdf_accessibility(source_path)["report"]["issues"]
            }
            for issue_id in (
                "table-row-children",
                "table-columns",
                "table-header-scope",
                "figure-structure-alt",
                "link-tags",
                "annotation-tags",
            ):
                self.assertGreater(
                    before_report[issue_id]["editorTargetCount"], 0, issue_id
                )

            with self.assertRaises(PDFAccessibilityError):
                apply_manual_structure_repairs(
                    source_path,
                    output_path,
                    [{"action": "set_annotation_contents", "contents": "Wrong target"}],
                )
            result = apply_manual_structure_repairs(
                source_path,
                output_path,
                [
                    {"action": "set_role", "path": "0/0/0/2", "role": "TD"},
                    {
                        "action": "set_scope",
                        "path": "0/0/0/0",
                        "scope": "Column",
                    },
                    {
                        "action": "set_figure_alt",
                        "path": "0/1",
                        "altText": "Court seal",
                    },
                    {"action": "pad_table", "path": "0/0"},
                    {
                        "action": "set_annotation_contents",
                        "pageIndex": 0,
                        "index": 0,
                        "contents": "Court website",
                    },
                    {
                        "action": "tag_annotation",
                        "pageIndex": 0,
                        "index": 0,
                        "role": "Link",
                    },
                    {
                        "action": "tag_annotation",
                        "pageIndex": 0,
                        "index": 1,
                        "role": "Annot",
                    },
                    {
                        "action": "set_widget_description",
                        "pageIndex": 0,
                        "index": 2,
                        "description": "Request a hearing",
                    },
                ],
            )
            self.assertEqual(result["roles_changed"], 1)
            self.assertEqual(result["scopes_changed"], 1)
            self.assertEqual(result["figure_alts_changed"], 1)
            self.assertEqual(result["annotations_tagged"], 2)
            self.assertEqual(result["widget_descriptions_changed"], 1)
            after = inspect_pdf_accessibility(output_path)
            table_after = after["structure_editor"]["tables"][0]
            self.assertEqual(
                {row["validCellCount"] for row in table_after["rows"]}, {3}
            )
            self.assertEqual(
                after["structure_editor"]["figures"][0]["altText"], "Court seal"
            )
            self.assertTrue(
                all(
                    annotation["tagged"]
                    for annotation in after["structure_editor"]["annotations"]
                )
            )
            self.assertEqual(
                after["structure_editor"]["widgets"][0]["tooltip"], "Request a hearing"
            )
            issues = {issue["id"]: issue for issue in after["report"]["issues"]}
            for issue_id in (
                "table-row-children",
                "table-columns",
                "table-header-scope",
                "figure-structure-alt",
                "link-tags",
                "annotation-tags",
            ):
                self.assertEqual(issues[issue_id]["status"], "pass", issue_id)
                self.assertEqual(issues[issue_id]["editorTargetCount"], 0, issue_id)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_unicode_only_font_repair_does_not_report_embedding_failure(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            page.obj["/Resources"] = pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary(
                        {
                            "/F1": pikepdf.Dictionary(
                                {
                                    "/Type": pikepdf.Name("/Font"),
                                    "/Subtype": pikepdf.Name("/Type1"),
                                    "/BaseFont": pikepdf.Name("/Helvetica"),
                                    "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
                                }
                            )
                        }
                    )
                }
            )
            page.obj["/Contents"] = pdf.make_stream(
                b"BT /F1 12 Tf 72 700 Td (Text) Tj ET"
            )
            pdf.save(source_path)
            pdf.close()

            with (
                patch(
                    "docassemble.ALDashboard.pdf_accessibility._system_truetype_fonts"
                ) as font_inventory,
                patch(
                    "docassemble.ALDashboard.pdf_accessibility.inspect_pdf_accessibility",
                    side_effect=AssertionError("full inspection should not run"),
                ),
            ):
                result = embed_fonts_and_rebuild_unicode(
                    source_path,
                    output_path,
                    embed_exact_fonts=False,
                    add_unicode_maps=True,
                )

            font_inventory.assert_not_called()
            self.assertEqual(result["unresolved"], [])
            self.assertEqual(result["unicode_maps_added"], ["p1/F1"])
            self.assertFalse(result["requested"]["embed_exact_fonts"])
            with pikepdf.open(output_path) as repaired:
                self.assertIn("/ToUnicode", repaired.pages[0].Resources.Font.F1)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_heading_candidates_merge_lines_and_suppress_form_noise(self):
        root = ET.fromstring("""<pdf2xml>
              <fontspec id="0" size="12" family="Arial"/>
              <fontspec id="1" size="18" family="Arial Bold"/>
              <fontspec id="2" size="16" family="Arial Bold"/>
              <fontspec id="3" size="15" family="Arial"/>
              <fontspec id="4" size="27" family="Arial Bold"/>
              <page number="1" width="612" height="792">
                <text top="20" left="20" width="80" height="18" font="1">Form PS–05</text>
                <text top="45" left="160" width="292" height="18" font="1">First Petition for Child Custody</text>
                <text top="100" left="20" width="300" height="12" font="0">This ordinary sentence supplies enough body text for sizing.</text>
                <text top="140" left="20" width="80" height="15" font="3">Your Name</text>
                <text top="180" left="20" width="40" height="16" font="2">Please</text>
                <text top="180" left="64" width="25" height="16" font="2">do</text>
                <text top="180" left="93" width="20" height="16" font="2">not</text>
                <text top="180" left="117" width="120" height="16" font="2">write here</text>
                <text top="210" left="20" width="160" height="18" font="1">Print your answers.</text>
                <text top="240" left="20" width="20" height="27" font="4">q</text>
                <text top="240" left="70" width="175" height="16" font="2">Mother’s information</text>
                <text top="300" left="20" width="12" height="18" font="1">•</text>
              </page>
              <page number="2" width="612" height="792">
                <text top="20" left="20" width="80" height="18" font="1">Form PS–05</text>
                <text top="45" left="150" width="312" height="18" font="1">First Petition for Child Custody</text>
                <text top="100" left="20" width="300" height="12" font="0">Another ordinary sentence contributes body text on page two.</text>
                <text top="180" left="20" width="170" height="16" font="2">*If Yes, fill out below:</text>
                <text top="240" left="20" width="20" height="27" font="4">w</text>
                <text top="240" left="70" width="170" height="16" font="2">Father’s information</text>
              </page>
            </pdf2xml>""")

        candidates = _heading_candidates_from_xml(root)
        texts = [candidate["text"] for candidate in candidates]

        self.assertEqual(texts.count("First Petition for Child Custody"), 1)
        self.assertNotIn("Form PS–05", texts)
        self.assertIn("Your Name", texts)
        self.assertNotIn("q", texts)
        self.assertNotIn("w", texts)
        self.assertNotIn("•", texts)
        self.assertNotIn("not", texts)
        self.assertIn("Please do not write here", texts)
        self.assertIn("Print your answers.", texts)
        self.assertIn("*If Yes, fill out below:", texts)
        self.assertIn("Mother’s information", texts)
        self.assertIn("Father’s information", texts)
        by_text = {candidate["text"]: candidate for candidate in candidates}
        self.assertEqual(
            by_text["First Petition for Child Custody"]["suggestedTag"], "H1"
        )
        self.assertEqual(by_text["Mother’s information"]["suggestedTag"], "H2")
        self.assertIn("reason", by_text["Mother’s information"])
        self.assertIn("candidateId", by_text["Mother’s information"])
        self.assertIn("box", by_text["Mother’s information"])

    def test_default_tooltip_replaces_underscores(self):
        self.assertEqual(
            default_pdf_field_tooltip("users1_name_first"),
            "users1 name first",
        )

    def test_default_tooltip_handles_empty(self):
        self.assertEqual(default_pdf_field_tooltip(""), "Field")
        self.assertEqual(default_pdf_field_tooltip(None), "Field")

    def test_build_default_field_order_sorts_page_then_top_then_left(self):
        fields = [
            {"name": "third", "pageIndex": 1, "x": 10, "y": 5},
            {"name": "second", "pageIndex": 0, "x": 20, "y": 10},
            {"name": "first", "pageIndex": 0, "x": 5, "y": 10},
            {"name": "zero", "pageIndex": 0, "x": 2, "y": 1},
        ]
        self.assertEqual(
            build_default_field_order(fields),
            ["zero", "first", "second", "third"],
        )

    def test_winansi_unicode_map_is_deterministic_and_includes_euro(self):
        import pikepdf

        font = pikepdf.Dictionary({"/Encoding": pikepdf.Name("/WinAnsiEncoding")})
        cmap = _simple_font_unicode_cmap(font)
        self.assertIsNotNone(cmap)
        self.assertIn(b"<80> <20AC>", cmap)
        self.assertTrue(cmap.endswith(b"end\n"))

    def test_unicode_map_refuses_unknown_encoding(self):
        """StandardEncoding is not reproduced by any codec, so it is refused."""
        import pikepdf

        font = pikepdf.Dictionary({"/Encoding": pikepdf.Name("/StandardEncoding")})
        self.assertIsNone(_simple_font_unicode_cmap(font))

    def test_unicode_map_supports_macroman(self):
        import pikepdf

        font = pikepdf.Dictionary({"/Encoding": pikepdf.Name("/MacRomanEncoding")})
        cmap = _simple_font_unicode_cmap(font)
        self.assertIsNotNone(cmap)
        # Byte 128 is A-diaeresis on the Mac and the euro sign on Windows.
        self.assertIn(b"<80> <00C4>", cmap)
        self.assertIn(b"<D4> <2018>", cmap)

    def test_macroman_follows_the_pdf_spec_not_the_python_codec(self):
        """Python's mac_roman is Mac OS 8.5+, which moved 0xDB to the euro."""
        import pikepdf

        self.assertEqual(bytes([0xDB]).decode("mac_roman"), "\u20ac")
        font = pikepdf.Dictionary({"/Encoding": pikepdf.Name("/MacRomanEncoding")})
        cmap = _simple_font_unicode_cmap(font)
        # PDF's MacRomanEncoding keeps the currency sign at that code.
        self.assertIn(b"<DB> <00A4>", cmap)
        self.assertNotIn(b"<DB> <20AC>", cmap)

    def test_differences_still_win_over_a_macroman_base(self):
        import pikepdf

        font = pikepdf.Dictionary(
            {
                "/Encoding": pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Encoding"),
                        "/BaseEncoding": pikepdf.Name("/MacRomanEncoding"),
                        "/Differences": pikepdf.Array([0xDB, pikepdf.Name("/Euro")]),
                    }
                )
            }
        )
        cmap = _simple_font_unicode_cmap(font)
        self.assertIn(b"<DB> <20AC>", cmap)

    def test_unicode_map_accepts_winansi_declared_as_a_base(self):
        """A dictionary naming WinAnsi as its base is still a WinAnsi font."""
        import pikepdf

        font = pikepdf.Dictionary(
            {
                "/Encoding": pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Encoding"),
                        "/BaseEncoding": pikepdf.Name("/WinAnsiEncoding"),
                    }
                )
            }
        )
        cmap = _simple_font_unicode_cmap(font)
        self.assertIsNotNone(cmap)
        self.assertIn(b"<80> <20AC>", cmap)

    def test_unicode_ligature_and_metric_encoding(self):
        import pikepdf

        font = pikepdf.Dictionary(
            {
                "/Encoding": pikepdf.Dictionary(
                    {
                        "/BaseEncoding": pikepdf.Name("/MacRomanEncoding"),
                        "/Differences": [65, pikepdf.Name("/f_f_i")],
                    }
                ),
                "/FirstChar": 128,
                "/Widths": [700],
            }
        )
        self.assertIn(b"<41> <006600660069>", _simple_font_unicode_cmap(font))
        self.assertEqual(_expected_font_widths(font), ({0xC4: 700.0}, "pdf-widths"))

    def test_field_names_with_whitespace_are_distinct(self):
        import pikepdf

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fields.pdf")
            pdf = pikepdf.new()
            page = pdf.add_blank_page()
            fields = [
                pdf.make_indirect(
                    pikepdf.Dictionary(
                        {
                            "/Subtype": pikepdf.Name("/Widget"),
                            "/FT": pikepdf.Name("/Tx"),
                            "/T": name,
                            "/Rect": [0, 0, 10, 10],
                        }
                    )
                )
                for name in ("name", " name ")
            ]
            page["/Annots"] = fields
            pdf.Root["/AcroForm"] = pikepdf.Dictionary({"/Fields": fields})
            pdf.save(path)
            pdf.close()
            apply_pdf_accessibility_settings(
                input_pdf_path=path,
                output_pdf_path=path,
                field_tooltips={"name": "First", " name ": "Second"},
                field_order=[" name ", "name"],
            )
            self.assertEqual(
                extract_pdf_field_tooltips(path), {"name": "First", " name ": "Second"}
            )
            with pikepdf.open(path) as result:
                self.assertEqual(
                    [str(f.T) for f in result.Root.AcroForm.Fields], [" name ", "name"]
                )

    def test_unicode_map_honours_differences_over_the_base(self):
        import pikepdf

        font = pikepdf.Dictionary(
            {
                "/Encoding": pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Encoding"),
                        "/BaseEncoding": pikepdf.Name("/WinAnsiEncoding"),
                        "/Differences": pikepdf.Array([65, pikepdf.Name("/breve")]),
                    }
                )
            }
        )
        cmap = _simple_font_unicode_cmap(font)
        self.assertIsNotNone(cmap)
        # Code 65 is "A" in WinAnsi, but /Differences reassigns it to U+02D8.
        self.assertIn(b"<41> <02D8>", cmap)
        self.assertNotIn(b"<41> <0041>", cmap)

    def test_unicode_map_from_differences_without_a_declared_base(self):
        """An unknown built-in encoding permits only explicit overrides."""
        import pikepdf

        font = pikepdf.Dictionary(
            {
                "/Encoding": pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Encoding"),
                        "/Differences": pikepdf.Array([24, pikepdf.Name("/breve")]),
                    }
                )
            }
        )
        cmap = _simple_font_unicode_cmap(font)
        self.assertIsNotNone(cmap)
        self.assertIn(b"<18> <02D8>", cmap)
        self.assertNotIn(b"<41> <0041>", cmap)
        # Nothing in the upper half may be invented from an unknown base.
        self.assertNotIn(b"<80> <20AC>", cmap)

    def test_extract_pdf_field_tooltips_reads_parent_and_widget_tu(self):
        import pikepdf
        from pikepdf import Array, Dictionary, Name, String

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = tmp.name

        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            parent_tooltip_field = pdf.make_indirect(
                Dictionary(
                    {
                        "/FT": Name("/Tx"),
                        "/T": String("parent_field"),
                        "/TU": String("Parent tooltip"),
                    }
                )
            )
            parent_tooltip_widget = pdf.make_indirect(
                Dictionary(
                    {
                        "/Parent": parent_tooltip_field,
                        "/Type": Name("/Annot"),
                        "/Subtype": Name("/Widget"),
                        "/Rect": Array([0, 0, 100, 20]),
                    }
                )
            )
            widget_tooltip_field = pdf.make_indirect(
                Dictionary(
                    {
                        "/FT": Name("/Tx"),
                        "/T": String("widget_field"),
                        "/TU": String("Widget tooltip"),
                        "/Type": Name("/Annot"),
                        "/Subtype": Name("/Widget"),
                        "/Rect": Array([0, 30, 100, 50]),
                    }
                )
            )
            unlabeled_field = pdf.make_indirect(
                Dictionary(
                    {
                        "/FT": Name("/Tx"),
                        "/T": String("unlabeled_field"),
                        "/Type": Name("/Annot"),
                        "/Subtype": Name("/Widget"),
                        "/Rect": Array([0, 60, 100, 80]),
                    }
                )
            )
            page.obj["/Annots"] = Array(
                [parent_tooltip_widget, widget_tooltip_field, unlabeled_field]
            )
            pdf.Root["/AcroForm"] = Dictionary(
                {
                    "/Fields": Array(
                        [parent_tooltip_field, widget_tooltip_field, unlabeled_field]
                    )
                }
            )
            pdf.save(pdf_path)
            pdf.close()

            self.assertEqual(
                extract_pdf_field_tooltips(pdf_path),
                {
                    "parent_field": "Parent tooltip",
                    "widget_field": "Widget tooltip",
                },
            )
        finally:
            os.remove(pdf_path)

    def test_apply_pdf_accessibility_settings_can_reapply_copied_tooltips(self):
        import pikepdf
        from pikepdf import Array, Dictionary, Name, String

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = tmp.name

        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            field = pdf.make_indirect(
                Dictionary(
                    {
                        "/FT": Name("/Tx"),
                        "/T": String("copied_field"),
                        "/Type": Name("/Annot"),
                        "/Subtype": Name("/Widget"),
                        "/Rect": Array([0, 0, 100, 20]),
                    }
                )
            )
            page.obj["/Annots"] = Array([field])
            pdf.Root["/AcroForm"] = Dictionary({"/Fields": Array([field])})
            pdf.save(pdf_path)
            pdf.close()
            create_draft_structure_tree(pdf_path, pdf_path)
            with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
                replacement = pdf.make_indirect(
                    Dictionary(
                        {
                            "/FT": Name("/Tx"),
                            "/T": String("copied_field"),
                            "/Type": Name("/Annot"),
                            "/Subtype": Name("/Widget"),
                            "/Rect": Array([0, 0, 100, 20]),
                        }
                    )
                )
                pdf.pages[0]["/Annots"] = Array([replacement])
                pdf.Root["/AcroForm"]["/Fields"] = Array([replacement])
                pdf.save(pdf_path)

            before = inspect_pdf_accessibility(pdf_path)["report"]["issues"]
            self.assertEqual(
                next(
                    issue["count"]
                    for issue in before
                    if issue["id"] == "form-structure-objects"
                ),
                1,
            )

            result = apply_pdf_accessibility_settings(
                input_pdf_path=pdf_path,
                output_pdf_path=pdf_path,
                field_tooltips={"copied_field": "Copied source tooltip"},
                auto_fill_missing_tooltips=False,
            )

            self.assertEqual(result["form_alt_updates"], 1)
            self.assertEqual(result["form_object_updates"], 1)
            with pikepdf.open(pdf_path) as pdf:
                widget = pdf.pages[0]["/Annots"][0]
                self.assertEqual(str(widget["/TU"]), "Copied source tooltip")
                self.assertEqual(
                    str(pdf.Root["/AcroForm"]["/Fields"][0]["/TU"]),
                    "Copied source tooltip",
                )
                form = pdf.Root.StructTreeRoot.K.K[0].K[0]
                self.assertEqual(str(form["/Alt"]), "Copied source tooltip")
                self.assertEqual(tuple(form.K.Obj.objgen), tuple(widget.objgen))
                self.assertIn("/StructParent", widget)
        finally:
            os.remove(pdf_path)

    def test_accessibility_metadata_updates_dublin_core_xmp(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = tmp.name
        try:
            pdf = pikepdf.new()
            pdf.add_blank_page()
            with pdf.open_metadata(set_pikepdf_as_editor=False) as metadata:
                metadata["xmp:CreatorTool"] = "Legacy exporter"
            pdf.save(pdf_path)
            pdf.close()

            apply_pdf_accessibility_settings(
                input_pdf_path=pdf_path,
                output_pdf_path=pdf_path,
                metadata={
                    "title": "Petition for Child Custody",
                    "language": "en-US",
                },
            )

            with pikepdf.open(pdf_path) as updated:
                xml = updated.Root.Metadata.read_bytes()
                self.assertIn(b"<dc:title", xml)
                self.assertIn(b"Petition for Child Custody", xml)
                self.assertIn(b"<dc:language", xml)
                self.assertIn(b"en-US", xml)
                self.assertEqual(
                    str(updated.docinfo.Title), "Petition for Child Custody"
                )
                self.assertEqual(str(updated.Root.Lang), "en-US")
        finally:
            os.remove(pdf_path)

    def test_repair_embedded_cidset_uses_true_type_glyph_count(self):
        import pikepdf

        pdf = pikepdf.new()
        page = pdf.add_blank_page()
        descriptor = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/ABCDEF+TestSubset"),
                "/FontFile2": pdf.make_stream(_webdings_like_program()),
                "/CIDSet": pdf.make_stream(b""),
            }
        )
        descendant = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/CIDFontType2"),
                    "/BaseFont": pikepdf.Name("/ABCDEF+TestSubset"),
                    "/FontDescriptor": descriptor,
                }
            )
        )
        type_zero = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/Type0"),
                    "/BaseFont": pikepdf.Name("/ABCDEF+TestSubset"),
                    "/DescendantFonts": pikepdf.Array([descendant]),
                }
            )
        )
        page["/Resources"] = pikepdf.Dictionary(
            {"/Font": pikepdf.Dictionary({"/F1": type_zero})}
        )

        repaired = _repair_embedded_cidsets(pdf)

        self.assertEqual(repaired, ["p1/F1"])
        self.assertEqual(descriptor.CIDSet.read_bytes(), b"\xc0")
        pdf.close()

    def test_repair_identity_cid_to_gid_map_when_used_cids_are_valid(self):
        import pikepdf

        pdf = pikepdf.new()
        page = pdf.add_blank_page()
        descriptor = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/ABCDEF+TestSubset"),
                "/FontFile2": pdf.make_stream(_webdings_like_program()),
            }
        )
        descendant = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/CIDFontType2"),
                    "/BaseFont": pikepdf.Name("/ABCDEF+TestSubset"),
                    "/FontDescriptor": descriptor,
                }
            )
        )
        type_zero = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/Type0"),
                    "/BaseFont": pikepdf.Name("/ABCDEF+TestSubset"),
                    "/Encoding": pikepdf.Name("/Identity-H"),
                    "/DescendantFonts": pikepdf.Array([descendant]),
                }
            )
        )
        page["/Resources"] = pikepdf.Dictionary(
            {"/Font": pikepdf.Dictionary({"/F1": type_zero})}
        )
        page["/Contents"] = pdf.make_stream(b"BT /F1 12 Tf <0001> Tj ET")

        repaired = _repair_identity_cid_to_gid_maps(pdf, _font_code_usage(pdf))

        self.assertEqual(repaired, ["p1/F1"])
        self.assertEqual(str(descendant.CIDToGIDMap), "/Identity")
        pdf.close()

    def test_preflight_reports_catalog_metadata_and_structure(self):
        import pikepdf

        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(612, 792))
        report = build_accessibility_report(pdf)
        by_id = {issue["id"]: issue for issue in report["issues"]}
        self.assertEqual(by_id["mark-info"]["status"], "fail")
        self.assertEqual(by_id["structure-tree"]["status"], "fail")
        self.assertEqual(
            by_id["structure-tree"]["title"], "No semantic structures to inspect"
        )
        self.assertEqual(by_id["document-language"]["status"], "fail")
        for issue_id in (
            "table-row-children",
            "table-columns",
            "table-header-scope",
            "figure-structure-alt",
            "link-tags",
            "annotation-tags",
        ):
            self.assertNotIn(issue_id, by_id)
        self.assertIn("disclaimer", report["summary"])
        pdf.close()

    def test_metadata_repair_sets_display_title_without_claiming_tags(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = tmp.name
        try:
            pdf = pikepdf.new()
            pdf.add_blank_page(page_size=(612, 792))
            pdf.save(pdf_path)
            pdf.close()
            apply_pdf_accessibility_settings(
                input_pdf_path=pdf_path,
                output_pdf_path=pdf_path,
                metadata={"title": "Court form", "language": "en-US"},
                auto_fill_missing_tooltips=False,
            )
            with pikepdf.open(pdf_path) as repaired:
                self.assertEqual(str(repaired.docinfo["/Title"]), "Court form")
                self.assertEqual(str(repaired.Root["/Lang"]), "en-US")
                self.assertTrue(
                    bool(repaired.Root["/ViewerPreferences"]["/DisplayDocTitle"])
                )
                self.assertNotIn("/MarkInfo", repaired.Root)
        finally:
            os.remove(pdf_path)

    def test_metadata_repair_does_not_display_a_missing_title(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = tmp.name
        try:
            pdf = pikepdf.new()
            pdf.add_blank_page(page_size=(612, 792))
            pdf.save(pdf_path)
            pdf.close()

            apply_pdf_accessibility_settings(
                input_pdf_path=pdf_path,
                output_pdf_path=pdf_path,
                metadata={"language": "en-US", "title": ""},
                auto_fill_missing_tooltips=False,
                set_display_doc_title=True,
            )

            with pikepdf.open(pdf_path) as repaired:
                self.assertNotIn("/ViewerPreferences", repaired.Root)
        finally:
            os.remove(pdf_path)

    def test_tagged_declaration_sets_and_removes_pdfua_identifier(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = tmp.name
        try:
            pdf = pikepdf.new()
            pdf.add_blank_page(page_size=(612, 792))
            pdf.save(pdf_path)
            pdf.close()

            result = apply_pdf_accessibility_settings(
                input_pdf_path=pdf_path,
                output_pdf_path=pdf_path,
                auto_fill_missing_tooltips=False,
                mark_as_tagged=True,
            )
            self.assertTrue(result["pdfua_declared"])
            with pikepdf.open(pdf_path) as repaired:
                self.assertTrue(bool(repaired.Root.MarkInfo.Marked))
                with repaired.open_metadata() as metadata:
                    self.assertEqual(
                        metadata["{http://www.aiim.org/pdfua/ns/id/}part"], "1"
                    )

            apply_pdf_accessibility_settings(
                input_pdf_path=pdf_path,
                output_pdf_path=pdf_path,
                auto_fill_missing_tooltips=False,
                mark_as_tagged=False,
            )
            with pikepdf.open(pdf_path) as repaired:
                self.assertFalse(bool(repaired.Root.MarkInfo.Marked))
                with repaired.open_metadata() as metadata:
                    self.assertNotIn("{http://www.aiim.org/pdfua/ns/id/}part", metadata)
        finally:
            os.remove(pdf_path)

    def test_artifact_draft_does_not_hide_untagged_meaningful_text(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = tmp.name
        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            page.obj["/Contents"] = pdf.make_stream(
                b"BT (Meaningful untagged text) Tj ET"
            )
            pdf.Root["/StructTreeRoot"] = pikepdf.Dictionary(
                {"/Type": pikepdf.Name("/StructTreeRoot")}
            )
            pdf.save(pdf_path)
            pdf.close()

            result = apply_pdf_accessibility_settings(
                input_pdf_path=pdf_path,
                output_pdf_path=pdf_path,
                auto_fill_missing_tooltips=False,
                mark_untagged_as_artifacts=True,
            )

            self.assertEqual(result["content_artifact_runs"], 0)
            with pikepdf.open(pdf_path) as repaired:
                self.assertNotIn(b"/Artifact", repaired.pages[0].Contents.read_bytes())
        finally:
            os.remove(pdf_path)

    def test_draft_structure_tags_each_page_without_declaring_pdf_tagged(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            pdf = pikepdf.new()
            first_page = pdf.add_blank_page(page_size=(612, 792))
            pdf.add_blank_page(page_size=(612, 792))
            first_page.obj["/Resources"] = pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary(
                        {
                            "/F1": pikepdf.Dictionary(
                                {
                                    "/Type": pikepdf.Name("/Font"),
                                    "/Subtype": pikepdf.Name("/Type1"),
                                    "/BaseFont": pikepdf.Name("/Helvetica"),
                                }
                            )
                        }
                    )
                }
            )
            first_page.obj["/Contents"] = pdf.make_stream(
                b"BT /F1 12 Tf 72 700 Td (First block) Tj ET "
                b"BT /F1 12 Tf 72 680 Td (Second block) Tj ET "
                b"0 0 m 20 20 l S"
            )
            widget = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/FT": pikepdf.Name("/Tx"),
                        "/T": pikepdf.String("field_one"),
                        "/TU": pikepdf.String("Field one"),
                        "/Type": pikepdf.Name("/Annot"),
                        "/Subtype": pikepdf.Name("/Widget"),
                        "/Rect": pikepdf.Array([0, 0, 100, 20]),
                    }
                )
            )
            first_page.obj["/Annots"] = pikepdf.Array([widget])
            pdf.Root["/AcroForm"] = pikepdf.Dictionary(
                {"/Fields": pikepdf.Array([widget])}
            )
            pdf.save(source_path)
            pdf.close()
            result = create_draft_structure_tree(source_path, output_path)
            self.assertEqual(result["pages_tagged"], 2)
            self.assertEqual(result["text_blocks_tagged"], 2)
            self.assertEqual(result["form_alts_added"], 1)
            self.assertGreater(result["content_artifact_runs"], 0)
            self.assertTrue(result["review_required"])
            second_pass = apply_pdf_accessibility_settings(
                input_pdf_path=output_path,
                output_pdf_path=output_path,
                auto_fill_missing_tooltips=False,
                mark_untagged_as_artifacts=True,
            )
            self.assertEqual(second_pass["content_artifact_runs"], 0)
            with pikepdf.open(output_path) as tagged:
                self.assertIn("/StructTreeRoot", tagged.Root)
                self.assertFalse(bool(tagged.Root["/MarkInfo"]["/Marked"]))
                self.assertEqual(int(tagged.pages[0]["/StructParents"]), 0)
                self.assertEqual(int(tagged.pages[1]["/StructParents"]), 1)
                self.assertEqual(str(tagged.pages[0]["/Tabs"]), "/S")
                self.assertIn("/StructParent", tagged.pages[0]["/Annots"][0])
                document = tagged.Root["/StructTreeRoot"]["/K"]
                first_page_part = document["/K"][0]
                self.assertEqual(str(first_page_part["/S"]), "/Part")
                self.assertEqual(
                    [str(child["/S"]) for child in first_page_part["/K"]],
                    ["/P", "/P", "/Form"],
                )
                self.assertEqual(str(first_page_part["/K"][2]["/Alt"]), "Field one")
                self.assertIn(b"/Artifact BMC", tagged.pages[0].Contents.read_bytes())
            issue_ids = {
                issue["id"]
                for issue in inspect_pdf_accessibility(output_path)["report"]["issues"]
            }
            self.assertTrue(
                {
                    "table-row-children",
                    "table-columns",
                    "table-header-scope",
                    "figure-structure-alt",
                    "link-tags",
                    "annotation-tags",
                }.isdisjoint(issue_ids)
            )
        finally:
            os.remove(source_path)
            os.remove(output_path)


if __name__ == "__main__":
    unittest.main()


def _readback_tounicode(mapping):
    """Build a minimal ToUnicode CMap for the given code -> text mapping."""
    entries = "".join(
        f"<{code:02X}> <{''.join(f'{ord(ch):04X}' for ch in text)}>\n"
        for code, text in mapping.items()
    )
    return (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        "1 begincodespacerange <00> <FF> endcodespacerange\n"
        f"{len(mapping)} beginbfchar\n{entries}endbfchar\n"
        "endcmap CMapName currentdict /CMap defineresource pop end end"
    ).encode("latin-1")


def _readback_pdf(
    path,
    *,
    order,
    texts=None,
    tooltips=None,
    tounicode=None,
    symbolic=False,
    base_font="Helvetica",
    untag=False,
    image_only=False,
):
    """Build a one-page PDF and tag its runs in a chosen announcement order.

    ``order`` lists run indexes in the order the tag tree should announce them,
    so a test can state the property under test rather than depend on a
    particular document.
    """
    import pikepdf

    runs = texts or ["Alpha one", "Beta two", "Gamma three", "Delta four"]
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    font_dict = {
        "/Type": pikepdf.Name("/Font"),
        "/Subtype": pikepdf.Name("/Type1"),
        "/BaseFont": pikepdf.Name("/" + base_font),
        # What the real fonts in a Word export declare.
        "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
    }
    if tounicode is not None:
        font_dict["/ToUnicode"] = pdf.make_stream(_readback_tounicode(tounicode))
    if symbolic:
        font_dict["/FontDescriptor"] = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/FontDescriptor"),
                    "/FontName": pikepdf.Name("/" + base_font),
                    # Bit 3: the font declares its glyphs are symbols.
                    "/Flags": 4,
                }
            )
        )
    font = pdf.make_indirect(pikepdf.Dictionary(font_dict))
    page.obj["/Resources"] = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )
    if image_only:
        image = pdf.make_stream(b"\x00")
        image["/Type"] = pikepdf.Name("/XObject")
        image["/Subtype"] = pikepdf.Name("/Image")
        image["/Width"] = 1
        image["/Height"] = 1
        page.obj["/Resources"]["/XObject"] = pikepdf.Dictionary({"/Im0": image})
        page.obj["/Contents"] = pdf.make_stream(b"q 400 0 0 400 72 300 cm /Im0 Do Q")
        runs = []
    body = b"BT /F1 12 Tf\n"
    for index, text in enumerate(runs):
        # Two runs per line, so a torn line is expressible.
        x = 72 + (index % 2) * 200
        y = 700 - (index // 2) * 20
        escaped = text.replace("\\", "").replace("(", "").replace(")", "")
        if untag:
            # Drawn, but never wrapped in marked content.
            body += f"1 0 0 1 {x} {y} Tm ({escaped}) Tj\n".encode("latin-1")
        else:
            body += (
                f"1 0 0 1 {x} {y} Tm /P <</MCID {index}>> BDC "
                f"({escaped}) Tj EMC\n".encode("latin-1")
            )
    body += b"ET"
    if not image_only:
        page.obj["/Contents"] = pdf.make_stream(body)

    struct_root = pdf.make_indirect(
        pikepdf.Dictionary(
            {"/Type": pikepdf.Name("/StructTreeRoot"), "/K": pikepdf.Array()}
        )
    )
    document = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/StructElem"),
                "/S": pikepdf.Name("/Document"),
                "/P": struct_root,
                "/K": pikepdf.Array(),
            }
        )
    )
    kids = []
    for mcid in order:
        kids.append(
            pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/StructElem"),
                        "/S": pikepdf.Name("/P"),
                        "/P": document,
                        "/Pg": page.obj,
                        "/K": mcid,
                    }
                )
            )
        )
    if tooltips:
        widgets = []
        for name, tooltip in tooltips:
            annot = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Annot"),
                        "/Subtype": pikepdf.Name("/Widget"),
                        "/FT": pikepdf.Name("/Tx"),
                        "/T": pikepdf.String(name),
                        "/TU": pikepdf.String(tooltip),
                        "/Rect": pikepdf.Array([300, 690, 400, 706]),
                    }
                )
            )
            widgets.append(annot)
            kids.append(
                pdf.make_indirect(
                    pikepdf.Dictionary(
                        {
                            "/Type": pikepdf.Name("/StructElem"),
                            "/S": pikepdf.Name("/Form"),
                            "/P": document,
                            "/Pg": page.obj,
                            "/Alt": pikepdf.String(tooltip),
                            "/K": pikepdf.Dictionary(
                                {
                                    "/Type": pikepdf.Name("/OBJR"),
                                    "/Obj": annot,
                                    "/Pg": page.obj,
                                }
                            ),
                        }
                    )
                )
            )
        page.obj["/Annots"] = pikepdf.Array(widgets)
    document["/K"] = pikepdf.Array(kids)
    struct_root["/K"] = pikepdf.Array([document])
    pdf.Root["/StructTreeRoot"] = struct_root
    pdf.Root["/MarkInfo"] = pikepdf.Dictionary({"/Marked": True})
    pdf.save(path)
    pdf.close()


class TestScreenReaderReadback(unittest.TestCase):
    """The read-back simulation states properties, not known bugs."""

    def test_array_content_references_keep_owner_and_order(self):
        import pikepdf
        from docassemble.ALDashboard.pdf_accessibility import _readback_sequence

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "references.pdf")
            _readback_pdf(path, order=[0, 1], tooltips=[("name", "Your name")])
            with pikepdf.open(path) as pdf:
                elements = pdf.Root.StructTreeRoot.K[0].K
                baseline = _readback_sequence(pdf)
                for element in elements:
                    element.K = pikepdf.Array([element.K])
                self.assertEqual(_readback_sequence(pdf), baseline)

                owner = elements[0]
                nested = elements[1]
                owner.K = pikepdf.Array([
                    0,
                    pikepdf.Dictionary(Type=pikepdf.Name.MCR, MCID=1, Pg=pdf.pages[0].obj),
                    nested,
                ])
                pdf.Root.StructTreeRoot.K[0].K = pikepdf.Array([owner, elements[2]])
                sequence = _readback_sequence(pdf, keep_elements=True)
                self.assertEqual([item["text"] for item in sequence],
                                 ["Alpha one", "Beta two", "Beta two", "Your name"])
                self.assertEqual([item["role"] for item in sequence], ["P", "P", "P", "Form"])
                self.assertEqual(sequence[1]["element"], owner)
                self.assertEqual(sequence[2]["element"], nested)
                self.assertEqual([item["page"] for item in sequence], [0, 0, 0, 0])

    def _run(self, **kwargs):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            path = handle.name
        try:
            _readback_pdf(path, **kwargs)
            return analyze_screen_reader_readback(path)
        finally:
            os.remove(path)

    def titles(self, result):
        return [finding["title"] for finding in result["findings"]]

    def test_an_order_that_follows_the_page_reports_nothing(self):
        result = self._run(order=[0, 1, 2, 3])
        self.assertTrue(result["available"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["summary"]["textRuns"], 4)

    def test_a_run_pulled_away_from_its_line_is_flagged(self):
        # Run 1 shares a line with run 0 but is announced last.
        result = self._run(order=[0, 2, 3, 1])
        self.assertIn("A line is announced in pieces, far apart", self.titles(result))

    def test_order_that_climbs_back_up_the_page_is_flagged(self):
        result = self._run(order=[2, 3, 0, 1])
        self.assertIn("Reading order moves back up the page", self.titles(result))

    def test_controls_sharing_an_announced_name_are_flagged(self):
        result = self._run(
            order=[0, 1, 2, 3],
            tooltips=[("a", "Reason for the request"), ("b", "Reason for the request")],
        )
        self.assertIn("Several controls announce the same name", self.titles(result))

    def test_text_is_decoded_the_way_a_conforming_extractor_would(self):
        """Byte 0x92 is a right single quote here, not damage to report."""
        result = self._run(
            order=[0, 1, 2, 3], texts=["Mother\x92s information", "B", "C", "D"]
        )
        self.assertEqual(
            result["announcements"][0]["text"], "Mother\u2019s information"
        )
        self.assertEqual(result["findings"], [])

    def test_a_character_map_that_really_says_trademark_is_flagged(self):
        """The finding belongs to the mapping, not to the raw bytes."""
        result = self._run(
            order=[0, 1, 2, 3],
            texts=["AZB", "Beta", "Gamma", "Delta"],
            tounicode={0x41: "A", 0x5A: "\u2122", 0x42: "B"},
        )
        finding = next(
            item
            for item in result["findings"]
            if item["title"] == "Text would be spoken as something it does not say"
        )
        self.assertEqual(finding["suggestion"], "A\u2019B")

    def test_a_glyph_with_no_character_behind_it_is_flagged(self):
        result = self._run(
            order=[0, 1, 2, 3],
            texts=["A\x01B", "Beta", "Gamma", "Delta"],
            tounicode={0x41: "A", 0x42: "B"},
        )
        self.assertIn("Content on the page is never spoken", self.titles(result))

    def test_a_symbol_font_that_claims_to_spell_words_is_flagged(self):
        """Circled numbers announced as "q w e" is the shape this catches."""
        result = self._run(
            order=[0, 1, 2, 3],
            texts=["q", "123", "456", "789"],
            tounicode={0x71: "q"},
            symbolic=True,
            base_font="CELEGI+CombiNumerals",
        )
        self.assertIn("A symbol is announced as a letter", self.titles(result))

    def test_a_subset_text_font_flagged_symbolic_is_not_reported(self):
        """Subset CID text fonts set the symbolic flag as a matter of course."""
        result = self._run(
            order=[0, 1, 2, 3],
            texts=["PETITION FOR JUDGMENT", "Beta", "Gamma", "Delta"],
            symbolic=True,
            base_font="CIDFont+F3",
        )
        self.assertNotIn("A symbol is announced as a letter", self.titles(result))

    def test_a_page_whose_text_is_entirely_untagged_is_reported(self):
        """Counting marked content alone cannot see a page with none."""
        result = self._run(order=[0], untag=True)
        self.assertIn("None of the page's text is tagged", self.titles(result))

    def test_a_page_that_is_only_a_picture_is_reported(self):
        result = self._run(order=[], image_only=True)
        self.assertIn("The page is a picture with no text in it", self.titles(result))

    def test_ordinary_text_is_not_mistaken_for_a_symbol(self):
        result = self._run(
            order=[0, 1, 2, 3], texts=["Case Number", "Beta", "Gamma", "Delta"]
        )
        self.assertNotIn("A symbol is announced as a letter", self.titles(result))

    def _repair(
        self,
        *,
        texts,
        decisions=None,
        tounicode=None,
        symbolic=False,
        base_font="Helvetica",
    ):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            _readback_pdf(
                source_path,
                order=[0, 1, 2, 3],
                texts=texts,
                tounicode=tounicode,
                symbolic=symbolic,
                base_font=base_font,
            )
            result = repair_readback_text(source_path, output_path, decisions=decisions)
            return result, analyze_screen_reader_readback(output_path)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_replacement_text_is_written_for_the_unambiguous_case(self):
        """A stand-in glyph between letters cannot be what the author wrote."""
        result, after = self._repair(
            texts=["AZB", "Beta", "Gamma", "Delta"],
            tounicode={0x41: "A", 0x5A: "\u2122", 0x42: "B"},
        )
        self.assertEqual(result["actual_text_added"], 1)
        self.assertEqual(result["applied"][0]["actualText"], "A\u2019B")
        # The replay honours /ActualText, so the finding is genuinely gone.
        self.assertEqual(
            [
                finding
                for finding in after["findings"]
                if finding["category"] == "text-encoding"
            ],
            [],
        )
        self.assertEqual(after["announcements"][0]["text"], "A\u2019B")

    def test_a_control_character_alone_waits_for_a_person(self):
        result, after = self._repair(
            texts=["\x01Yes", "Beta", "Gamma", "Delta"],
            tounicode={0x01: "\x01", 0x59: "Y", 0x65: "e", 0x73: "s"},
        )
        self.assertEqual(result["actual_text_added"], 0)
        self.assertEqual(len(result["needs_review"]), 1)
        self.assertEqual(result["needs_review"][0]["suggestion"], "Yes")
        # Still reported, because nothing was changed.
        self.assertTrue(
            any(finding["category"] == "text-encoding" for finding in after["findings"])
        )

    def test_a_reviewer_can_override_or_decline_each_replacement(self):
        result, _after = self._repair(
            texts=["Mother\x92s information", "\x00Yes", "Gamma", "Delta"],
            decisions=[
                {"announcedIndex": 0, "actualText": "Parent's information"},
                {"announcedIndex": 1, "actualText": "Yes", "apply": False},
            ],
        )
        self.assertEqual(result["actual_text_added"], 1)
        self.assertEqual(result["applied"][0]["actualText"], "Parent's information")

    def test_the_page_is_never_redrawn_by_a_replacement(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            _readback_pdf(
                source_path,
                order=[0, 1, 2, 3],
                texts=["AZB", "B", "C", "D"],
            )
            with pikepdf.open(source_path) as before:
                drawn = before.pages[0].Contents.read_bytes()
            repair_readback_text(source_path, output_path)
            with pikepdf.open(output_path) as after:
                self.assertEqual(after.pages[0].Contents.read_bytes(), drawn)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def _rename(self, tooltips, **kwargs):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            _readback_pdf(source_path, order=[0, 1, 2, 3], tooltips=tooltips)
            result = repair_duplicate_field_names(source_path, output_path, **kwargs)
            return result, analyze_screen_reader_readback(output_path)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_controls_that_share_a_name_are_numbered_by_position(self):
        result, after = self._rename(
            [("a", "Reason for the request"), ("b", "Reason for the request")]
        )
        self.assertEqual(result["tooltips_renamed"], 2)
        announced = sorted(item["tooltip"] for item in result["applied"])
        self.assertEqual(
            announced,
            [
                "Reason for the request (option 1 of 2)",
                "Reason for the request (option 2 of 2)",
            ],
        )
        # The duplicate is genuinely resolved, not merely relabelled.
        self.assertEqual(
            [
                finding
                for finding in after["findings"]
                if finding["category"] == "field-names"
            ],
            [],
        )

    def test_renaming_twice_does_not_stack_the_numbering(self):
        self.assertEqual(
            _strip_field_name_distinguisher("Notes (continued) (line 1 of 3)"),
            "Notes",
        )
        self.assertEqual(_strip_field_name_distinguisher("Amount (3 of 5)"), "Amount")
        # An ordinary parenthetical is not a distinguisher.
        self.assertEqual(
            _strip_field_name_distinguisher("Name (as it appears)"),
            "Name (as it appears)",
        )

    def test_widgets_sharing_one_field_name_are_each_renamed(self):
        """Radio kids and repeated widgets share a /T; each still needs a name."""
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            _readback_pdf(
                source_path,
                order=[0, 1, 2, 3],
                tooltips=[("shared", "Pick one"), ("shared", "Pick one")],
            )
            result = repair_duplicate_field_names(source_path, output_path)
            self.assertEqual(result["tooltips_renamed"], 2)
            # Two distinct announced positions, two distinct names.
            self.assertEqual(
                len({item["announcedIndex"] for item in result["applied"]}), 2
            )
            self.assertEqual(len({item["tooltip"] for item in result["applied"]}), 2)
            with pikepdf.open(output_path) as after:
                alts = []

                def walk(node):
                    if isinstance(node, pikepdf.Array):
                        for kid in node:
                            walk(kid)
                    elif isinstance(node, pikepdf.Dictionary):
                        if "/Alt" in node:
                            alts.append(str(node["/Alt"]))
                        if "/K" in node:
                            walk(node["/K"])

                walk(after.Root.StructTreeRoot.K)
                self.assertEqual(len(set(alts)), 2)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_a_reviewer_can_replace_the_numbering_with_a_real_name(self):
        result, _after = self._rename(
            [("a", "Reason for the request"), ("b", "Reason for the request")],
            decisions=[{"fieldName": "a", "tooltip": "Reason you are asking"}],
        )
        names = {item["fieldName"]: item["tooltip"] for item in result["applied"]}
        self.assertEqual(names["a"], "Reason you are asking")
        self.assertEqual(names["b"], "Reason for the request (option 2 of 2)")

    def test_distinct_names_are_left_alone(self):
        result, _after = self._rename([("a", "First reason"), ("b", "Second reason")])
        self.assertEqual(result["tooltips_renamed"], 0)

    def test_a_curated_symbol_meaning_is_applied_without_asking(self):
        """CombiNumerals declares the key you press, not what it draws."""
        result, after = self._repair(
            # The other runs use digits, which the table has no entry for, so
            # the fixture's single font does not rewrite them too.
            texts=["q", "123", "456", "789"],
            tounicode={0x71: "q"},
            symbolic=True,
            base_font="CELEGI+CombiNumerals",
        )
        self.assertEqual(result["actual_text_added"], 1)
        self.assertEqual(result["applied"][0]["actualText"], "\u2460")
        self.assertEqual(after["announcements"][0]["text"], "\u2460")
        # The run that was fixed is no longer reported.
        self.assertEqual(
            [
                finding
                for finding in after["findings"]
                if finding.get("announcedIndex") == 0
            ],
            [],
        )

    def test_an_uncurated_symbol_font_still_waits_for_a_person(self):
        result, _after = self._repair(
            texts=["z", "123", "456", "789"],
            tounicode={0x7A: "z"},
            symbolic=True,
            base_font="ABCDEF+SomeUnknownDingbat",
        )
        self.assertEqual(result["actual_text_added"], 0)

    def test_an_untagged_pdf_reports_that_there_is_nothing_to_replay(self):
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            path = handle.name
        try:
            pdf = pikepdf.new()
            pdf.add_blank_page(page_size=(612, 792))
            pdf.save(path)
            pdf.close()
            result = analyze_screen_reader_readback(path)
            self.assertFalse(result["available"])
            self.assertIn("no tag tree", result["reason"])
        finally:
            os.remove(path)


class TestReadbackCoordinateHandling(unittest.TestCase):
    """Every check downstream is coordinate-driven, so the spots must be right."""

    def test_a_new_text_object_resets_the_text_matrix(self):
        """BT starts an identity matrix; carrying the old one moved runs."""
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            path = handle.name
        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            font = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Font"),
                        "/Subtype": pikepdf.Name("/Type1"),
                        "/BaseFont": pikepdf.Name("/Helvetica"),
                        "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
                    }
                )
            )
            page.obj["/Resources"] = pikepdf.Dictionary(
                {"/Font": pikepdf.Dictionary({"/F1": font})}
            )
            # Two text objects, each positioning with a relative Td.
            page.obj["/Contents"] = pdf.make_stream(
                b"BT /F1 12 Tf 72 720 Td (first) Tj ET "
                b"BT /F1 12 Tf 72 700 Td (second) Tj ET"
            )
            pdf.save(path)
            pdf.close()
            runs = _readback_page_runs_for_test(path)
            self.assertEqual(sorted(round(run["y"]) for run in runs), [700, 720])
        finally:
            os.remove(path)


def _readback_page_runs_for_test(path):
    import pikepdf
    from docassemble.ALDashboard.pdf_accessibility import _readback_page_runs

    with pikepdf.open(path) as pdf:
        page = pdf.pages[0]
        # The fixture marks nothing, so wrap each run to make it collectable.
        instructions = list(pikepdf.parse_content_stream(page))
        rebuilt = []
        mcid = 0
        for instruction in instructions:
            if str(instruction.operator) == "BT":
                rebuilt.append(
                    pikepdf.ContentStreamInstruction(
                        [pikepdf.Name("/P"), pikepdf.Dictionary({"/MCID": mcid})],
                        pikepdf.Operator("BDC"),
                    )
                )
                mcid += 1
            rebuilt.append(instruction)
            if str(instruction.operator) == "ET":
                rebuilt.append(
                    pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC"))
                )
        page["/Contents"] = pdf.make_stream(pikepdf.unparse_content_stream(rebuilt))
        return list(_readback_page_runs(page).values())


class TestNestedFormXObjectTagging(unittest.TestCase):
    """Plenty of government forms draw their whole body inside a form."""

    def _nested_form_pdf(self, path, *, depth=2, repeat_form=False):
        """A page whose text lives inside a chain of Form XObjects."""
        import pikepdf

        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(612, 792))
        font = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/Type1"),
                    "/BaseFont": pikepdf.Name("/Helvetica"),
                    "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
                }
            )
        )
        # The innermost form carries the text, in its own coordinate space.
        inner = pdf.make_stream(
            b"BT /F1 12 Tf 1 0 0 1 20 -40 Tm (Second line) Tj "
            b"1 0 0 1 20 -20 Tm (First line) Tj ET"
        )
        inner["/Type"] = pikepdf.Name("/XObject")
        inner["/Subtype"] = pikepdf.Name("/Form")
        inner["/BBox"] = pikepdf.Array([0, -100, 500, 0])
        inner["/Resources"] = pikepdf.Dictionary(
            {"/Font": pikepdf.Dictionary({"/F1": font})}
        )
        current = inner
        for level in range(depth - 1):
            wrapper = pdf.make_stream(b"q /Fm0 Do Q")
            wrapper["/Type"] = pikepdf.Name("/XObject")
            wrapper["/Subtype"] = pikepdf.Name("/Form")
            wrapper["/BBox"] = pikepdf.Array([0, -100, 500, 0])
            wrapper["/Resources"] = pikepdf.Dictionary(
                {"/XObject": pikepdf.Dictionary({"/Fm0": current})}
            )
            current = wrapper
        page.obj["/Resources"] = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Fm0": current})}
        )
        body = b"q 1 0 0 1 0 700 cm /Fm0 Do Q"
        if repeat_form:
            body += b" q 1 0 0 1 0 400 cm /Fm0 Do Q"
        page.obj["/Contents"] = pdf.make_stream(body)
        pdf.save(path)
        pdf.close()

    def _tag(self, **kwargs):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._nested_form_pdf(source_path, **kwargs)
            result = create_draft_structure_tree(
                input_pdf_path=source_path,
                output_pdf_path=output_path,
                heading_decisions=[],
                mark_as_tagged=False,
            )
            return result, analyze_screen_reader_readback(output_path)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_text_inside_a_nested_form_is_tagged_and_announced(self):
        result, readback = self._tag(depth=3)
        self.assertGreaterEqual(result["form_xobjects_tagged"], 1)
        self.assertEqual(result["text_blocks_tagged"], 2)
        spoken = [
            item["text"]
            for item in readback["announcements"]
            if item["kind"] == "text" and item["text"].strip()
        ]
        self.assertEqual(spoken, ["First line", "Second line"])

    def test_a_form_drawn_twice_is_left_alone(self):
        """Its marked content could not say which copy an id belongs to."""
        result, readback = self._tag(depth=2, repeat_form=True)
        self.assertEqual(result["form_xobjects_tagged"], 0)
        self.assertIn(
            "None of the page's text is tagged",
            [finding["title"] for finding in readback["findings"]],
        )


class TestImageDescriptionWithAi(unittest.TestCase):
    """Pixels leave the server only here, and the answer is only a draft."""

    def _image_pdf(self, path, *, width=64, height=64):
        import pikepdf

        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(612, 792))
        stream = pdf.make_stream(bytes([200, 40, 40] * width * height))
        stream["/Type"] = pikepdf.Name("/XObject")
        stream["/Subtype"] = pikepdf.Name("/Image")
        stream["/Width"] = width
        stream["/Height"] = height
        stream["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
        stream["/BitsPerComponent"] = 8
        page.obj["/Resources"] = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Im0": stream})}
        )
        page.obj["/Contents"] = pdf.make_stream(b"q 100 0 0 100 40 600 cm /Im0 Do Q")
        pdf.save(path)
        pdf.close()

    def test_images_are_rendered_small_and_tiny_ones_are_skipped(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            path = handle.name
        try:
            self._image_pdf(path, width=400, height=400)
            previews = render_image_assets(path, max_pixels=64)
            self.assertEqual(list(previews), ["p1:Im0"])
            from PIL import Image

            with Image.open(io.BytesIO(previews["p1:Im0"])) as rendered:
                self.assertLessEqual(max(rendered.size), 64)
        finally:
            os.remove(path)

    def test_a_hairline_image_is_never_sent(self):
        """A 2px rule carries nothing; it should not cost a model call."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            path = handle.name
        try:
            self._image_pdf(path, width=2, height=40)
            self.assertEqual(render_image_assets(path), {})
        finally:
            os.remove(path)

    def test_the_picture_is_sent_and_the_answer_becomes_a_draft(self):
        with (
            patch(
                "docassemble.ALToolbox.llms.chat_completion",
                return_value="  Seal of the Commonwealth of Massachusetts.  ",
            ) as completion,
            patch(
                "docassemble.ALToolbox.llms.get_first_small_model",
                return_value="a-small-model",
            ),
        ):
            result = describe_images_with_ai(
                {"p1:Im0": b"fake-png"}, context={"filename": "209A.pdf"}
            )
        self.assertEqual(
            result,
            [
                {
                    "assetId": "p1:Im0",
                    "decorative": False,
                    "altText": "Seal of the Commonwealth of Massachusetts.",
                    "model": "a-small-model",
                }
            ],
        )
        messages = completion.call_args.kwargs["messages"]
        parts = messages[1]["content"]
        self.assertEqual([part["type"] for part in parts], ["text", "image_url"])
        self.assertTrue(
            parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
        )
        # JSON mode assumes string content, which cannot carry a picture.
        self.assertNotIn("json_mode", completion.call_args.kwargs)
        # A small model is enough to say what a seal is.
        self.assertEqual(completion.call_args.kwargs["model"], "a-small-model")

    def test_decorative_is_reported_rather_than_invented(self):
        with (
            patch(
                "docassemble.ALToolbox.llms.chat_completion", return_value="DECORATIVE"
            ),
            patch(
                "docassemble.ALToolbox.llms.get_first_small_model", return_value="small"
            ),
        ):
            result = describe_images_with_ai({"p1:Im0": b"x"})
        self.assertTrue(result[0]["decorative"])
        self.assertEqual(result[0]["altText"], "")

    def test_a_model_failure_is_reported_per_image(self):
        with (
            patch(
                "docassemble.ALToolbox.llms.chat_completion",
                side_effect=RuntimeError("no vision support"),
            ),
            patch(
                "docassemble.ALToolbox.llms.get_first_small_model", return_value="small"
            ),
        ):
            result = describe_images_with_ai({"p1:Im0": b"x", "p1:Im1": b"y"})
        self.assertEqual(len(result), 2)
        self.assertIn("no vision support", result[0]["error"])


class TestScannedPageOcr(unittest.TestCase):
    """A page of pixels has nothing to announce until something reads it."""

    def _image_only_pdf(self, path, words):
        """Draw text, rasterise it, and rebuild it as a picture-only page."""
        import pikepdf

        with tempfile.TemporaryDirectory() as workspace:
            source = os.path.join(workspace, "text.pdf")
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            font = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Font"),
                        "/Subtype": pikepdf.Name("/Type1"),
                        "/BaseFont": pikepdf.Name("/Helvetica"),
                    }
                )
            )
            page.obj["/Resources"] = pikepdf.Dictionary(
                {"/Font": pikepdf.Dictionary({"/F1": font})}
            )
            body = b"BT /F1 36 Tf\n"
            for index, word in enumerate(words):
                body += f"1 0 0 1 72 {650 - index * 60} Tm ({word}) Tj\n".encode()
            page.obj["/Contents"] = pdf.make_stream(body + b"ET")
            pdf.save(source)
            pdf.close()

            rendered = os.path.join(workspace, "page")
            subprocess.run(
                [
                    "pdftoppm",
                    "-r",
                    "150",
                    "-png",
                    "-f",
                    "1",
                    "-l",
                    "1",
                    source,
                    rendered,
                ],
                check=True,
                timeout=120,
                capture_output=True,
            )
            png = sorted(Path(workspace).glob("page*.png"))[0]
            raw = png.read_bytes()
            from PIL import Image

            with Image.open(png) as image:
                width, height = image.size
                flat = image.convert("RGB").tobytes()

            out = pikepdf.new()
            out_page = out.add_blank_page(page_size=(612, 792))
            stream = out.make_stream(flat)
            stream["/Type"] = pikepdf.Name("/XObject")
            stream["/Subtype"] = pikepdf.Name("/Image")
            stream["/Width"] = width
            stream["/Height"] = height
            stream["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
            stream["/BitsPerComponent"] = 8
            out_page.obj["/Resources"] = pikepdf.Dictionary(
                {"/XObject": pikepdf.Dictionary({"/Im0": stream})}
            )
            out_page.obj["/Contents"] = out.make_stream(
                b"q 612 0 0 792 0 0 cm /Im0 Do Q"
            )
            out.save(path)
            out.close()
            self.assertTrue(raw)

    def test_a_scanned_page_gains_a_text_layer_it_can_be_read_from(self):
        if not shutil.which("tesseract") or not shutil.which("pdftoppm"):
            self.skipTest("tesseract and pdftoppm are needed to read a page image.")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._image_only_pdf(source_path, ["Petition", "Custody", "Hearing"])
            before = analyze_screen_reader_readback(source_path)
            self.assertNotIn(
                "The page is a picture with no text in it",
                [finding["title"] for finding in before["findings"]],
                "fixture has no tag tree, so the readback cannot judge it yet",
            )
            result = ocr_image_only_pages(source_path, output_path)
            self.assertEqual(result["pages_read"], 1)
            self.assertTrue(result["review_required"])
            recognised = " ".join(page["sample"] for page in result["pages"]).lower()
            self.assertIn("petition", recognised)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_a_page_that_already_has_text_is_left_alone(self):
        if not shutil.which("tesseract"):
            self.skipTest("tesseract is needed to read a page image.")
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            _readback_pdf(source_path, order=[0, 1, 2, 3])
            result = ocr_image_only_pages(source_path, output_path)
            self.assertEqual(result["pages_read"], 0)
            self.assertEqual(
                result["skipped"][0]["reason"], "The page already has text."
            )
            with (
                pikepdf.open(source_path) as before,
                pikepdf.open(output_path) as after,
            ):
                self.assertEqual(
                    before.pages[0].Contents.read_bytes(),
                    after.pages[0].Contents.read_bytes(),
                )
        finally:
            os.remove(source_path)
            os.remove(output_path)


class TestSymbolicFontGlyphReview(unittest.TestCase):
    """Cover the reviewed-Unicode path for symbol fonts like Webdings."""

    def test_artifact_repair_respects_nesting_and_graphics_state(self):
        import pikepdf
        from docassemble.ALDashboard.pdf_accessibility import (
            _mark_font_runs_as_artifact,
            _pdf_object_identity,
            _font_code_usage,
        )

        with pikepdf.new() as pdf:
            page = pdf.add_blank_page()
            fonts = {
                name: pdf.make_indirect(
                    pikepdf.Dictionary(
                        {
                            "/Subtype": pikepdf.Name("/TrueType"),
                        }
                    )
                )
                for name in ("/S1", "/F1")
            }
            page["/Resources"] = pikepdf.Dictionary({"/Font": fonts})
            page["/Contents"] = pdf.make_stream(
                b"BT /S1 12 Tf q /F1 12 Tf (A) Tj Q (B) Tj "
                b"/P <</MCID 0>> BDC /Span BMC (C) Tj EMC (D) Tj EMC "
                b"/Artifact BMC (E) Tj EMC ET"
            )
            identity = _pdf_object_identity(fonts["/S1"], "p1/S1")
            usage = _font_code_usage(pdf)
            self.assertEqual(usage[identity]["counts"], {66: 1, 67: 1, 68: 1, 69: 1})
            result = _mark_font_runs_as_artifact(pdf, {identity: "p1/S1"})
            self.assertEqual(result["wrapped"], {"p1/S1": 1})
            self.assertEqual(result["skipped_tagged"], {"p1/S1": 2})
            # Existing artifacts stay intact and a second run is idempotent.
            self.assertEqual(page.Contents.read_bytes().count(b"/Artifact BMC"), 2)
            self.assertEqual(
                _mark_font_runs_as_artifact(pdf, {identity: "p1/S1"})["wrapped"], {}
            )

    def _symbolic_pdf(self, path, *, tagged=False):
        """Build a one-page PDF drawing one Identity-H symbol glyph twice."""
        import pikepdf

        program = _webdings_like_program()
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(612, 792))
        descriptor = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/FontDescriptor"),
                    "/FontName": pikepdf.Name("/ABCDEF+Webdings"),
                    "/Flags": 4,
                    "/FontBBox": [0, -200, 1000, 900],
                    "/ItalicAngle": 0,
                    "/Ascent": 900,
                    "/Descent": -200,
                    "/CapHeight": 700,
                    "/StemV": 80,
                }
            )
        )
        stream = pdf.make_stream(program)
        stream["/Length1"] = len(program)
        descriptor["/FontFile2"] = stream
        descendant = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/CIDFontType2"),
                    "/BaseFont": pikepdf.Name("/ABCDEF+Webdings"),
                    "/CIDSystemInfo": pikepdf.Dictionary(
                        {
                            "/Registry": "Adobe",
                            "/Ordering": "Identity",
                            "/Supplement": 0,
                        }
                    ),
                    "/CIDToGIDMap": pikepdf.Name("/Identity"),
                    "/FontDescriptor": descriptor,
                    "/DW": 1000,
                }
            )
        )
        page.obj["/Resources"] = pikepdf.Dictionary(
            {
                "/Font": pikepdf.Dictionary(
                    {
                        "/S1": pikepdf.Dictionary(
                            {
                                "/Type": pikepdf.Name("/Font"),
                                "/Subtype": pikepdf.Name("/Type0"),
                                "/BaseFont": pikepdf.Name("/ABCDEF+Webdings"),
                                "/Encoding": pikepdf.Name("/Identity-H"),
                                "/DescendantFonts": pikepdf.Array([descendant]),
                            }
                        )
                    }
                )
            }
        )
        body = b"BT /S1 12 Tf 72 700 Td <0001> Tj 0 -20 Td <0001> Tj ET"
        if tagged:
            body = b"/P <</MCID 0>> BDC " + body + b" EMC"
        page.obj["/Contents"] = pdf.make_stream(body)
        pdf.save(path)
        pdf.close()

    def test_simple_symbol_font_code_resolves_through_the_cmap(self):
        """A simple font's show-text code is a character code, not a glyph id."""
        import pikepdf

        code = 0xFC
        program = _simple_symbol_program(box_glyph_id=1, decoy_glyph_id=code, code=code)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            descriptor = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/FontDescriptor"),
                        "/FontName": pikepdf.Name("/ABCDEF+Wingdings"),
                        "/Flags": 4,
                        "/FontBBox": [0, -200, 1000, 900],
                        "/ItalicAngle": 0,
                        "/Ascent": 900,
                        "/Descent": -200,
                        "/CapHeight": 700,
                        "/StemV": 80,
                    }
                )
            )
            stream = pdf.make_stream(program)
            stream["/Length1"] = len(program)
            descriptor["/FontFile2"] = stream
            page.obj["/Resources"] = pikepdf.Dictionary(
                {
                    "/Font": pikepdf.Dictionary(
                        {
                            "/S1": pikepdf.Dictionary(
                                {
                                    "/Type": pikepdf.Name("/Font"),
                                    "/Subtype": pikepdf.Name("/TrueType"),
                                    "/BaseFont": pikepdf.Name("/ABCDEF+Wingdings"),
                                    "/FirstChar": code,
                                    "/LastChar": code,
                                    "/Widths": pikepdf.Array([1000]),
                                    "/FontDescriptor": descriptor,
                                }
                            )
                        }
                    )
                }
            )
            page.obj["/Contents"] = pdf.make_stream(
                b"BT /S1 12 Tf 72 700 Td <FC> Tj ET"
            )
            pdf.save(source_path)
            pdf.close()

            review = collect_symbolic_font_review(source_path)
            glyph = review["fonts"][0]["glyphs"][0]
            self.assertEqual(glyph["code"], code)
            # The outline is the one the cmap selects, not glyph number 0xFC.
            self.assertEqual(glyph["outline"]["glyphName"], "box")
            self.assertEqual(glyph["charCode"], code)
            self.assertEqual(glyph["charCodeSource"], "pdf-code")
        finally:
            os.remove(source_path)

    def test_review_reports_used_codes_with_outlines(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        try:
            self._symbolic_pdf(source_path)
            review = collect_symbolic_font_review(source_path)
            fonts = review["fonts"]
            self.assertEqual(len(fonts), 1)
            font = fonts[0]
            self.assertTrue(font["symbolic"])
            self.assertEqual(font["encoding"], "Identity-H")
            self.assertEqual(font["runs"], 2)
            # Only the code the page actually draws is offered for review.
            self.assertEqual(font["glyphCount"], 1)
            glyph = font["glyphs"][0]
            self.assertEqual(glyph["code"], 1)
            self.assertEqual(glyph["count"], 2)
            self.assertIsNotNone(glyph["outline"])
            self.assertTrue(glyph["outline"]["path"].startswith("M"))
            self.assertEqual(len(glyph["outline"]["bbox"]), 4)
        finally:
            os.remove(source_path)

    def test_reviewed_alabama_webdings_outline_is_a_ballot_box(self):
        outline = "M2048 -410H0V1638H2048ZM1920 -282V1510H128V-282Z"
        proposal = propose_outline_character("CELEJJ+Webdings", outline)
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.character, "☐")
        self.assertEqual(proposal.evidence, "rendered-outline")

    def test_reviewed_alabama_wingdings_outline_is_a_ballot_box(self):
        outline = "M321 148H1505V1332H321ZM173 0V1480H1653V0Z"
        proposal = propose_outline_character("CELEIJ+Wingdings", outline)
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.character, "☐")
        self.assertEqual(proposal.evidence, "rendered-outline")

    def test_reviewed_map_preserves_existing_codes_and_fills_missing_code(self):
        import pikepdf

        existing = b"""1 beginbfchar
<0003> <0020>
endbfchar
"""
        with pikepdf.new() as pdf:
            font = pikepdf.Dictionary(
                {
                    "/Subtype": pikepdf.Name("/Type0"),
                    "/Encoding": pikepdf.Name("/Identity-H"),
                    "/ToUnicode": pdf.make_stream(existing),
                }
            )
            self.assertEqual(_to_unicode_mappings(font), {3: " "})
            source = {"maxp": type("Maxp", (), {"numGlyphs": 200})()}
            with (
                patch(
                    "docassemble.ALDashboard.pdf_accessibility._font_program_bytes",
                    return_value=(b"font", "FontFile2"),
                ),
                patch(
                    "docassemble.ALDashboard.pdf_accessibility._load_glyph_source",
                    return_value=source,
                ),
                patch(
                    "docassemble.ALDashboard.pdf_accessibility._installed_symbol_codes",
                    return_value={},
                ),
                patch(
                    "docassemble.ALDashboard.pdf_accessibility._embedded_char_code",
                    return_value=None,
                ),
                patch(
                    "docassemble.ALDashboard.pdf_accessibility._glyph_outline",
                    return_value={"path": "M321 148H1505V1332H321ZM173 0V1480H1653V0Z"},
                ),
            ):
                cmap = _curated_symbol_unicode_cmap(font, "Wingdings", [3, 133])
        self.assertIn(b"<0003> <0020>", cmap)
        self.assertIn(b"<0085> <2610>", cmap)

    def test_reviewed_outline_builds_identity_font_unicode_map(self):
        import pikepdf

        font = pikepdf.Dictionary(
            {
                "/Subtype": pikepdf.Name("/Type0"),
                "/Encoding": pikepdf.Name("/Identity-H"),
            }
        )
        source = {"maxp": type("Maxp", (), {"numGlyphs": 100})()}
        outline = {"path": "M2048 -410H0V1638H2048ZM1920 -282V1510H128V-282Z"}
        with (
            patch(
                "docassemble.ALDashboard.pdf_accessibility._font_program_bytes",
                return_value=(b"font", "FontFile2"),
            ),
            patch(
                "docassemble.ALDashboard.pdf_accessibility._load_glyph_source",
                return_value=source,
            ),
            patch(
                "docassemble.ALDashboard.pdf_accessibility._installed_symbol_codes",
                return_value={},
            ),
            patch(
                "docassemble.ALDashboard.pdf_accessibility._embedded_char_code",
                return_value=None,
            ),
            patch(
                "docassemble.ALDashboard.pdf_accessibility._glyph_outline",
                return_value=outline,
            ),
        ):
            cmap = _curated_symbol_unicode_cmap(font, "Webdings", [70])
        self.assertIn(b"<0046> <2610>", cmap)

    def test_review_offers_no_proposal_without_evidence(self):
        """A subset with no cmap and no installed match must not guess."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        try:
            self._symbolic_pdf(source_path)
            with patch(
                "docassemble.ALDashboard.pdf_accessibility._system_truetype_fonts",
                return_value=[],
            ):
                review = collect_symbolic_font_review(source_path)
            glyph = review["fonts"][0]["glyphs"][0]
            self.assertIsNone(glyph["proposal"])
            self.assertIsNone(glyph["charCode"])
            self.assertEqual(glyph["charCodeSource"], "")
        finally:
            os.remove(source_path)

    def test_confirmed_mapping_writes_two_byte_tounicode(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._symbolic_pdf(source_path)
            result = apply_unicode_map_decisions(
                source_path,
                output_path,
                [{"resource": "p1/S1", "action": "map", "mappings": {"1": "☐"}}],
            )
            self.assertEqual(len(result["fonts_mapped"]), 1)
            self.assertEqual(result["fonts_mapped"][0]["characters"], 1)

            import pikepdf

            with pikepdf.open(output_path) as pdf:
                font = pdf.pages[0]["/Resources"]["/Font"]["/S1"]
                cmap = bytes(font["/ToUnicode"].read_bytes()).decode("ascii")
            # Identity-H codes are two bytes, so the codespace must be too.
            self.assertIn("<0000> <FFFF>", cmap)
            self.assertIn("<0001> <2610>", cmap)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_unconfirmed_codes_are_left_unmapped(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._symbolic_pdf(source_path)
            result = apply_unicode_map_decisions(
                source_path,
                output_path,
                [{"resource": "p1/S1", "action": "map", "mappings": {"1": ""}}],
            )
            self.assertEqual(result["fonts_mapped"], [])
            self.assertEqual(len(result["fonts_skipped"]), 1)

            import pikepdf

            with pikepdf.open(output_path) as pdf:
                font = pdf.pages[0]["/Resources"]["/Font"]["/S1"]
                self.assertNotIn("/ToUnicode", font)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_artifact_marking_wraps_untagged_runs(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._symbolic_pdf(source_path)
            result = apply_unicode_map_decisions(
                source_path, output_path, [{"resource": "p1/S1", "action": "artifact"}]
            )
            self.assertEqual(result["fonts_artifacted"][0]["runs_marked"], 2)
            self.assertEqual(result["fonts_artifacted"][0]["runs_left_tagged"], 0)

            import pikepdf

            with pikepdf.open(output_path) as pdf:
                data = bytes(pdf.pages[0]["/Contents"].read_bytes())
            self.assertEqual(data.count(b"/Artifact BMC"), 2)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_artifact_marking_preserves_existing_tagged_content(self):
        """Retagging tagged runs would orphan structure-tree entries."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._symbolic_pdf(source_path, tagged=True)
            result = apply_unicode_map_decisions(
                source_path, output_path, [{"resource": "p1/S1", "action": "artifact"}]
            )
            self.assertEqual(result["fonts_artifacted"][0]["runs_marked"], 0)
            self.assertEqual(result["fonts_artifacted"][0]["runs_left_tagged"], 2)

            import pikepdf

            with pikepdf.open(output_path) as pdf:
                data = bytes(pdf.pages[0]["/Contents"].read_bytes())
            self.assertNotIn(b"/Artifact BMC", data)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_unknown_resource_is_rejected(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._symbolic_pdf(source_path)
            with self.assertRaises(PDFAccessibilityError):
                apply_unicode_map_decisions(
                    source_path,
                    output_path,
                    [{"resource": "p9/Nope", "action": "map", "mappings": {"1": "x"}}],
                )
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_unresolved_reason_names_the_real_obstacle(self):
        """The old wording named the code path and sent people font hunting."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._symbolic_pdf(source_path)
            result = embed_fonts_and_rebuild_unicode(
                source_path, output_path, embed_exact_fonts=False
            )
            unresolved = result["unicode_unresolved"]
            self.assertEqual(len(unresolved), 1)
            reason = unresolved[0]["reason"]
            self.assertIn("symbol font", reason)
            self.assertIn("private use area", reason)
            self.assertNotIn("WinAnsi", reason)
            self.assertTrue(unresolved[0]["reviewable"])
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_form_appearance_font_is_not_reported_as_unused(self):
        """An empty field's appearance font looks unused but must not be removed."""
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        try:
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            helv = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Font"),
                        "/Subtype": pikepdf.Name("/Type1"),
                        "/BaseFont": pikepdf.Name("/Helvetica"),
                        "/Name": pikepdf.Name("/Helv"),
                    }
                )
            )
            appearance = pdf.make_stream(
                b"/Tx BMC q BT /Helv 9 Tf 1 3 Td () Tj ET Q EMC"
            )
            appearance["/Resources"] = pikepdf.Dictionary(
                {"/Font": pikepdf.Dictionary({"/Helv": helv})}
            )
            widget = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Annot"),
                        "/Subtype": pikepdf.Name("/Widget"),
                        "/FT": pikepdf.Name("/Tx"),
                        "/T": "Your name",
                        "/DA": "/Helv 9 Tf 0 g",
                        "/Rect": [10, 10, 200, 30],
                        "/AP": pikepdf.Dictionary({"/N": appearance}),
                    }
                )
            )
            page.obj["/Annots"] = pikepdf.Array([widget])
            pdf.Root["/AcroForm"] = pikepdf.Dictionary(
                {
                    "/Fields": pikepdf.Array([widget]),
                    "/DA": "/Helv 0 Tf 0 g",
                    "/DR": pikepdf.Dictionary(
                        {"/Font": pikepdf.Dictionary({"/Helv": helv})}
                    ),
                }
            )
            pdf.save(source_path)
            pdf.close()

            review = collect_symbolic_font_review(source_path)
            font = next(f for f in review["fonts"] if f["font"] == "Helvetica")
            # The empty field draws no glyphs, so this looks unused...
            self.assertEqual(font["glyphCount"], 0)
            self.assertGreater(font["runs"], 0)
            # ...but it is the appearance font the viewer needs once filled.
            self.assertGreaterEqual(font["formFieldCount"], 1)
        finally:
            os.remove(source_path)


class TestSymbolFontProposals(unittest.TestCase):
    def test_curated_proposals_cover_checkbox_glyphs(self):
        self.assertEqual(propose_character("Wingdings", 0xFC).character, "✓")
        self.assertEqual(propose_character("Wingdings", 0xFD).character, "☒")
        self.assertEqual(propose_character("Wingdings", 0xFE).character, "☑")
        self.assertEqual(propose_character("/ABCDEF+Webdings", 0x63).character, "☐")

    def test_uncurated_codes_get_no_proposal(self):
        self.assertIsNone(propose_character("Wingdings", 0x41))
        self.assertIsNone(propose_character("Arial", 0x41))

    def test_symbol_font_detection_ignores_subset_and_style(self):
        self.assertTrue(is_symbolic_family("/CELEJJ+Webdings"))
        self.assertTrue(is_symbolic_family("Wingdings,Bold"))
        self.assertFalse(is_symbolic_family("ArialNarrow"))


class TestStandardFourteenEmbedding(unittest.TestCase):
    """A standard 14 font names a face and stores no metrics of its own."""

    def _standard_14_pdf(self, path):
        import pikepdf

        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(612, 792))
        helv = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/Type1"),
                    "/BaseFont": pikepdf.Name("/Helvetica"),
                    "/Name": pikepdf.Name("/Helv"),
                }
            )
        )
        page.obj["/Resources"] = pikepdf.Dictionary(
            {"/Font": pikepdf.Dictionary({"/Helv": helv})}
        )
        page.obj["/Contents"] = pdf.make_stream(b"BT /Helv 12 Tf 72 700 Td (Hi) Tj ET")
        pdf.save(path)
        pdf.close()

    def _inventory(self, path, canonical="helvetica", embeddable=True):
        return [
            {
                "path": path,
                "names": ["Helvetica"],
                "canonical_names": {canonical},
                "embeddable": embeddable,
                "fsType": 0,
            }
        ]

    def test_dashboard_managed_font_is_found_without_fontconfig(self):
        candidate = _metric_compatible_sans_path()
        if candidate is None:
            self.skipTest("No test TrueType font is installed.")
        with tempfile.TemporaryDirectory() as directory:
            managed = Path(directory) / "Helvetica.ttf"
            os.symlink(candidate, managed)
            with (
                patch(
                    "docassemble.ALDashboard.pdf_accessibility.DASHBOARD_FONT_DIRECTORY",
                    Path(directory),
                ),
                patch(
                    "docassemble.ALDashboard.pdf_accessibility.shutil.which",
                    return_value=None,
                ),
            ):
                inventory = _system_embeddable_fonts()
        self.assertIn(str(managed), {record["path"] for record in inventory})

    def test_expected_widths_fall_back_to_published_metrics(self):
        """Nothing in the file states the widths, so the table must supply them."""
        import pikepdf

        font = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
        self.assertNotIn("/Widths", font)
        expected, source = _expected_font_widths(font)
        self.assertEqual(source, "standard-14")
        self.assertEqual(expected[ord("A")], 667)
        self.assertEqual(expected[ord("i")], 222)
        self.assertEqual(expected[ord(" ")], 278)

    def test_published_metrics_match_a_real_helvetica(self):
        """Guards the vendored table against drift."""
        widths = standard_14_widths("helvetica")
        self.assertIsNotNone(widths)
        for glyph, expected in (("A", 667), ("m", 833), ("W", 944), ("i", 222)):
            self.assertEqual(widths[ord(glyph)], expected)
        courier = standard_14_widths("courier")
        # Courier is monospaced, so every character shares one width.
        self.assertEqual({courier[ord(c)] for c in "AiW "}, {600})

    def test_zapfdingbats_widths_are_keyed_by_its_built_in_encoding(self):
        """Its glyphs have no AGL entries, and the clones cmap these codes."""
        widths = standard_14_widths("zapfdingbats")
        self.assertIsNotNone(widths)
        # 32 is space and 33 is a1, exactly as the AFM records them.
        self.assertEqual(widths[32], 278)
        self.assertEqual(widths[33], 974)
        self.assertEqual(widths[34], 961)
        clone = "/usr/share/fonts/opentype/urw-base35/D050000L.otf"
        if not os.path.exists(clone):
            self.skipTest("No Zapf Dingbats clone is installed.")
        from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

        font = TTFont(clone, lazy=True)
        try:
            cmap = font.getBestCmap()
            metrics = font["hmtx"].metrics
            units = float(font["head"].unitsPerEm)
            for code, expected in widths.items():
                glyph = cmap.get(code)
                if not glyph:
                    continue
                self.assertAlmostEqual(
                    metrics[glyph][0] * 1000.0 / units, expected, delta=2
                )
        finally:
            font.close()

    def test_metric_compatible_font_completes_the_dictionary(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._standard_14_pdf(source_path)
            candidate = _metric_compatible_sans_path()
            if candidate is None:
                self.skipTest("No Helvetica-metric font is installed.")
            with patch(
                "docassemble.ALDashboard.pdf_accessibility._system_embeddable_fonts",
                return_value=self._inventory(candidate),
            ):
                result = embed_fonts_and_rebuild_unicode(
                    source_path, output_path, add_unicode_maps=False
                )
            self.assertEqual(len(result["fonts_embedded"]), 1)
            self.assertTrue(result["fonts_embedded"][0]["completed_standard_14"])

            import pikepdf

            with pikepdf.open(output_path) as pdf:
                font = pdf.pages[0]["/Resources"]["/Font"]["/Helv"]
                # The dictionary must now stand on its own.
                self.assertEqual(str(font["/Subtype"]), "/TrueType")
                self.assertEqual(str(font["/Encoding"]), "/WinAnsiEncoding")
                self.assertIn("/FontFile2", font["/FontDescriptor"])
                first = int(font["/FirstChar"])
                widths = font["/Widths"]
                self.assertEqual(int(widths[ord("A") - first]), 667)
                self.assertEqual(int(widths[ord(" ") - first]), 278)
                self.assertIn(b"<27> <0027>", _simple_font_unicode_cmap(font))
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_completing_a_standard_14_font_keeps_its_differences(self):
        """A /Differences entry still describes what the page draws."""
        import pikepdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            candidate = _metric_compatible_sans_path()
            if candidate is None:
                self.skipTest("No Helvetica-metric font is installed.")
            pdf = pikepdf.new()
            page = pdf.add_blank_page(page_size=(612, 792))
            helv = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Font"),
                        "/Subtype": pikepdf.Name("/Type1"),
                        "/BaseFont": pikepdf.Name("/Helvetica"),
                        "/Name": pikepdf.Name("/Helv"),
                        "/Encoding": pikepdf.Dictionary(
                            {
                                "/Differences": pikepdf.Array(
                                    [39, pikepdf.Name("/quoteright")]
                                )
                            }
                        ),
                    }
                )
            )
            page.obj["/Resources"] = pikepdf.Dictionary(
                {"/Font": pikepdf.Dictionary({"/Helv": helv})}
            )
            page.obj["/Contents"] = pdf.make_stream(
                b"BT /Helv 12 Tf 72 700 Td (Hi) Tj ET"
            )
            pdf.save(source_path)
            pdf.close()

            with patch(
                "docassemble.ALDashboard.pdf_accessibility._system_embeddable_fonts",
                return_value=self._inventory(candidate),
            ):
                result = embed_fonts_and_rebuild_unicode(
                    source_path, output_path, add_unicode_maps=False
                )
            self.assertEqual(len(result["fonts_embedded"]), 1)

            with pikepdf.open(output_path) as embedded:
                font = embedded.pages[0]["/Resources"]["/Font"]["/Helv"]
                encoding = font["/Encoding"]
                self.assertEqual(str(encoding["/BaseEncoding"]), "/WinAnsiEncoding")
                self.assertEqual(
                    [str(item) for item in encoding["/Differences"]],
                    ["39", "/quoteright"],
                )
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_metric_compatible_cff_helvetica_is_embedded(self):
        import pikepdf

        candidate = "/usr/share/fonts/opentype/urw-base35/NimbusSans-Regular.otf"
        if not os.path.exists(candidate):
            self.skipTest("No Helvetica-metric CFF font is installed.")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._standard_14_pdf(source_path)
            with patch(
                "docassemble.ALDashboard.pdf_accessibility._system_embeddable_fonts",
                return_value=self._inventory(candidate),
            ):
                result = embed_fonts_and_rebuild_unicode(
                    source_path, output_path, add_unicode_maps=False
                )
            self.assertEqual(len(result["fonts_embedded"]), 1)
            with pikepdf.open(output_path) as pdf:
                font = pdf.pages[0].Resources.Font.Helv
                self.assertEqual(str(font.Subtype), "/Type1")
                self.assertEqual(str(font.FontDescriptor.FontFile3.Subtype), "/Type1C")
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_font_with_wrong_metrics_is_still_refused(self):
        """Sharing a name is not evidence of being the same face."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._standard_14_pdf(source_path)
            wrong = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            if not os.path.exists(wrong):
                self.skipTest("DejaVu Sans is not installed.")
            with patch(
                "docassemble.ALDashboard.pdf_accessibility._system_embeddable_fonts",
                return_value=self._inventory(wrong),
            ):
                result = embed_fonts_and_rebuild_unicode(
                    source_path, output_path, add_unicode_maps=False
                )
            self.assertEqual(result["fonts_embedded"], [])
            self.assertEqual(len(result["unresolved"]), 1)
            self.assertIn("widths do not match", result["unresolved"][0]["reason"])
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_missing_font_reason_differs_from_wrong_metrics_reason(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._standard_14_pdf(source_path)
            with patch(
                "docassemble.ALDashboard.pdf_accessibility._system_embeddable_fonts",
                return_value=[],
            ):
                result = embed_fonts_and_rebuild_unicode(
                    source_path, output_path, add_unicode_maps=False
                )
            self.assertIn(
                "No installed font is named", result["unresolved"][0]["reason"]
            )
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_license_restricted_font_is_named_as_such(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._standard_14_pdf(source_path)
            candidate = _metric_compatible_sans_path()
            if candidate is None:
                self.skipTest("No Helvetica-metric font is installed.")
            with patch(
                "docassemble.ALDashboard.pdf_accessibility._system_embeddable_fonts",
                return_value=self._inventory(candidate, embeddable=False),
            ):
                result = embed_fonts_and_rebuild_unicode(
                    source_path, output_path, add_unicode_maps=False
                )
            self.assertIn("license flags forbid", result["unresolved"][0]["reason"])
        finally:
            os.remove(source_path)
            os.remove(output_path)


class TestFontSubstitution(unittest.TestCase):
    """Substitution swaps the typeface but must never move the text."""

    def test_cff_replacement_changes_a_truetype_dictionary_to_type1(self):
        import pikepdf
        from docassemble.ALDashboard.pdf_accessibility import _substitute_font_program

        with pikepdf.new() as pdf:
            font = pikepdf.Dictionary(
                {
                    "/Subtype": pikepdf.Name("/TrueType"),
                    "/BaseFont": pikepdf.Name("/Original"),
                    "/Encoding": pikepdf.Name("/MacRomanEncoding"),
                    "/Widths": [600],
                    "/FirstChar": 65,
                }
            )
            with (
                patch(
                    "docassemble.ALDashboard.pdf_accessibility._embeddable_program",
                    return_value=(b"program", "/FontFile3", "/Type1C"),
                ),
                patch(
                    "docassemble.ALDashboard.pdf_accessibility._descriptor_for_program",
                    return_value=pikepdf.Dictionary(),
                ),
            ):
                self.assertTrue(
                    _substitute_font_program(
                        pdf,
                        font,
                        "Original",
                        {"path": "installed.otf", "postscript_name": "Replacement"},
                    )
                )
            self.assertEqual(str(font.Subtype), "/Type1")
            self.assertEqual(str(font.Encoding), "/MacRomanEncoding")

    URW_SANS = "/usr/share/fonts/opentype/urw-base35/NimbusSans-Regular.otf"
    URW_SANS_BOLD = "/usr/share/fonts/opentype/urw-base35/NimbusSans-Bold.otf"

    def _helvetica_font(self):
        import pikepdf

        return pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )

    def _standard_14_pdf(self, path):
        import pikepdf

        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(612, 792))
        helv = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/Type1"),
                    "/BaseFont": pikepdf.Name("/Helvetica"),
                    "/Name": pikepdf.Name("/Helv"),
                }
            )
        )
        page.obj["/Resources"] = pikepdf.Dictionary(
            {"/Font": pikepdf.Dictionary({"/Helv": helv})}
        )
        page.obj["/Contents"] = pdf.make_stream(b"BT /Helv 12 Tf 72 700 Td (Hi) Tj ET")
        pdf.save(path)
        pdf.close()

    def test_bold_is_not_treated_as_metric_compatible_with_regular(self):
        """Bold and regular share most widths and differ only on letters."""
        if not os.path.exists(self.URW_SANS_BOLD):
            self.skipTest("URW base35 fonts are not installed.")
        font = self._helvetica_font()
        regular = _font_width_match_score(font, self.URW_SANS)
        bold = _font_width_match_score(font, self.URW_SANS_BOLD)
        self.assertIsNotNone(regular)
        self.assertIsNotNone(bold)
        self.assertLessEqual(regular, 2.0)
        # A median would report zero here, because more than half of the
        # glyphs agree; the letters that differ are the minority.
        self.assertGreater(bold, 2.0)

    def test_candidates_prefer_a_matching_style(self):
        if not os.path.exists(self.URW_SANS):
            self.skipTest("URW base35 fonts are not installed.")
        font = self._helvetica_font()
        candidates = find_metric_compatible_fonts(
            font, _system_embeddable_fonts(), limit=10
        )
        self.assertTrue(candidates)
        # Upright regular faces must come before oblique ones.
        self.assertTrue(candidates[0]["style_matches"])
        self.assertFalse(candidates[0]["italic"])
        styles = [c["style_matches"] for c in candidates]
        self.assertEqual(styles, sorted(styles, reverse=True))

    def test_substitution_embeds_cff_as_fontfile3(self):
        if not os.path.exists(self.URW_SANS):
            self.skipTest("URW base35 fonts are not installed.")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._standard_14_pdf(source_path)
            result = substitute_fonts(
                source_path,
                output_path,
                [{"resource": "p1/Helv", "path": self.URW_SANS}],
            )
            self.assertEqual(len(result["fonts_substituted"]), 1)
            self.assertEqual(
                result["fonts_substituted"][0]["substitute"], "NimbusSans-Regular"
            )

            import pikepdf

            with pikepdf.open(output_path) as pdf:
                fonts = pdf.pages[0]["/Resources"]["/Font"]
                # The resource name must survive so /DA strings keep resolving.
                self.assertIn("/Helv", fonts)
                font = fonts["/Helv"]
                self.assertEqual(str(font["/BaseFont"]), "/NimbusSans-Regular")
                descriptor = font["/FontDescriptor"]
                self.assertIn("/FontFile3", descriptor)
                self.assertEqual(str(descriptor["/FontFile3"]["/Subtype"]), "/Type1C")
                first = int(font["/FirstChar"])
                self.assertEqual(int(font["/Widths"][ord("A") - first]), 667)
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_substitution_embeds_truetype_as_fontfile2(self):
        candidate = _metric_compatible_sans_path()
        if candidate is None:
            self.skipTest("No Helvetica-metric TrueType font is installed.")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._standard_14_pdf(source_path)
            substitute_fonts(
                source_path,
                output_path,
                [{"resource": "p1/Helv", "path": candidate}],
            )

            import pikepdf

            with pikepdf.open(output_path) as pdf:
                font = pdf.pages[0]["/Resources"]["/Font"]["/Helv"]
                descriptor = font["/FontDescriptor"]
                self.assertIn("/FontFile2", descriptor)
                self.assertEqual(str(font["/Subtype"]), "/TrueType")
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_substitute_with_wrong_metrics_is_refused(self):
        """Even an explicit request must not be allowed to reflow the text."""
        wrong = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if not os.path.exists(wrong):
            self.skipTest("DejaVu Sans is not installed.")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._standard_14_pdf(source_path)
            with self.assertRaises(PDFAccessibilityError) as caught:
                substitute_fonts(
                    source_path,
                    output_path,
                    [{"resource": "p1/Helv", "path": wrong}],
                )
            self.assertIn("widths do not match", str(caught.exception))
        finally:
            os.remove(source_path)
            os.remove(output_path)

    def test_substitute_from_outside_the_inventory_is_refused(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source_path = source.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
            output_path = output.name
        try:
            self._standard_14_pdf(source_path)
            with self.assertRaises(PDFAccessibilityError):
                substitute_fonts(
                    source_path,
                    output_path,
                    [{"resource": "p1/Helv", "path": "/etc/passwd"}],
                )
        finally:
            os.remove(source_path)
            os.remove(output_path)

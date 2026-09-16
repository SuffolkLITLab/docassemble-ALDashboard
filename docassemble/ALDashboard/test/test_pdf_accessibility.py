# do not pre-load
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from docassemble.ALDashboard.symbol_fonts import (
    is_symbolic_family,
    propose_character,
)
from docassemble.ALDashboard.standard_font_metrics import standard_14_widths
from docassemble.ALDashboard.pdf_accessibility import (
    PDFAccessibilityError,
    _expected_font_widths,
    _font_width_match_score,
    _system_embeddable_fonts,
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
    draft_field_tooltips_with_ai,
    embed_fonts_and_rebuild_unicode,
    extract_pdf_field_tooltips,
    inspect_pdf_accessibility,
    _iter_pdf_fonts,
    _heading_candidates_from_xml,
    _winansi_to_unicode_cmap,
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


class TestPDFAccessibilityHelpers(unittest.TestCase):
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
        self.assertGreaterEqual(
            completion.call_args.kwargs["max_output_tokens"], 8192
        )

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
        self.assertEqual([resource for resource, _font in _iter_pdf_fonts(pdf)], ["p1/F1", "p1/F2"])
        pdf.close()

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
                self.assertEqual([str(child.S) for child in part.K], ["/H3", "/P"])
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
                paragraph = next(
                    child for child in page_part.K if str(child.S) == "/P"
                )
                self.assertEqual(int(paragraph.K), 6)
                parent_entries = tagged.Root.StructTreeRoot.ParentTree.Nums[1]
                self.assertEqual(len(parent_entries), 7)
                self.assertIsNone(parent_entries[5])
                self.assertEqual(str(parent_entries[6].S), "/P")
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
                self.assertEqual([str(child.S) for child in part.K], ["/H2", "/P"])
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
            page.obj["/Annots"] = pikepdf.Array([link, note])
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
            self.assertEqual(
                before["figures"][0]["issueIds"], ["figure-structure-alt"]
            )
            self.assertFalse(before["annotations"][0]["tagged"])
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
                ],
            )
            self.assertEqual(result["roles_changed"], 1)
            self.assertEqual(result["scopes_changed"], 1)
            self.assertEqual(result["figure_alts_changed"], 1)
            self.assertEqual(result["annotations_tagged"], 2)
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

            with patch(
                "docassemble.ALDashboard.pdf_accessibility._system_truetype_fonts"
            ) as font_inventory, patch(
                "docassemble.ALDashboard.pdf_accessibility.inspect_pdf_accessibility",
                side_effect=AssertionError("full inspection should not run"),
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
        cmap = _winansi_to_unicode_cmap(font)
        self.assertIsNotNone(cmap)
        self.assertIn(b"<80> <20AC>", cmap)
        self.assertTrue(cmap.endswith(b"end\n"))

    def test_unicode_map_refuses_unknown_encoding(self):
        import pikepdf

        font = pikepdf.Dictionary({"/Encoding": pikepdf.Name("/MacRomanEncoding")})
        self.assertIsNone(_winansi_to_unicode_cmap(font))

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

            apply_pdf_accessibility_settings(
                input_pdf_path=pdf_path,
                output_pdf_path=pdf_path,
                field_tooltips={"copied_field": "Copied source tooltip"},
                auto_fill_missing_tooltips=False,
            )

            with pikepdf.open(pdf_path) as pdf:
                widget = pdf.pages[0]["/Annots"][0]
                self.assertEqual(str(widget["/TU"]), "Copied source tooltip")
                self.assertEqual(
                    str(pdf.Root["/AcroForm"]["/Fields"][0]["/TU"]),
                    "Copied source tooltip",
                )
        finally:
            os.remove(pdf_path)

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
                b"BT /F1 12 Tf 72 680 Td (Second block) Tj ET"
            )
            widget = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/FT": pikepdf.Name("/Tx"),
                        "/T": pikepdf.String("field_one"),
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
            self.assertTrue(result["review_required"])
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


class TestSymbolicFontGlyphReview(unittest.TestCase):
    """Cover the reviewed-Unicode path for symbol fonts like Webdings."""

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
                "docassemble.ALDashboard.pdf_accessibility._system_truetype_fonts",
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
                self.assertIn("/FontFile2", font["/FontDescriptor"])
                first = int(font["/FirstChar"])
                widths = font["/Widths"]
                self.assertEqual(int(widths[ord("A") - first]), 667)
                self.assertEqual(int(widths[ord(" ") - first]), 278)
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
                "docassemble.ALDashboard.pdf_accessibility._system_truetype_fonts",
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
                "docassemble.ALDashboard.pdf_accessibility._system_truetype_fonts",
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
                "docassemble.ALDashboard.pdf_accessibility._system_truetype_fonts",
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

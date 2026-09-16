# do not pre-load
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from docassemble.ALDashboard.pdf_accessibility import (
    apply_pdf_accessibility_settings,
    apply_manual_structure_repairs,
    build_accessibility_report,
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

    def test_draft_structure_tags_each_page_and_sets_mark_info(self):
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
                self.assertTrue(bool(tagged.Root["/MarkInfo"]["/Marked"]))
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

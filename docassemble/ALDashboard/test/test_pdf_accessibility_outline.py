"""Regression cases from the deterministic accessibility pipeline audit."""
import xml.etree.ElementTree as ET

import pikepdf
import pytest

from docassemble.ALDashboard.pdf_accessibility import (
    _duplicate_field_distinguishers,
    _distinguishing_context,
    _nearby_row_label,
    _heading_candidates_from_xml,
    _heading_exclusion_boxes,
    _heading_outline_findings,
    _heading_overlaps_exclusion,
    default_pdf_field_tooltip,
)


def test_pascal_case_and_acronym_tooltips():
    assert default_pdf_field_tooltip("HadFelonyYes") == "Had Felony Yes"
    assert default_pdf_field_tooltip("SSNNumber_otherValue") == "SSN Number other Value"


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("raw_key", ["text", "drawn"])
def test_merged_caption_row_cannot_win_a_geometric_tie(reverse, raw_key):
    fields = [dict(kind="field", index=i+10, name=f"other_case_{i+1}_court",
                   text="Court [1]", page=0, x=138, y=326.16-i*13)
              for i in range(2)]
    merged = dict(kind="text", index=0, page=0, x=74.88, y=329.494,
                  text="Letter of Child Court Docket No.", spoken="Letter of Child Court Docket No.")
    merged[raw_key] = "   Letter of Child            Court         Docket No.   "
    child = dict(kind="text", index=1, page=0, x=74.88, y=318.694, text="CHILD   ")
    labels = [merged, child]
    if reverse:
        labels.reverse()
    assert _nearby_row_label(fields[0], labels) == "CHILD"
    suggestions = _duplicate_field_distinguishers(fields, labels + fields)
    assert len(suggestions) == 2
    assert all(s["distinguisherSource"] == "position" for s in suggestions)
    assert all("Letter of Child" not in s["suggested"] for s in suggestions)
    assert _nearby_row_label(fields[0], [merged]) == ""


@pytest.mark.parametrize("marker", ["Section ", "Section 8", "Section C", "Chapter II", "Page 2"])
def test_page_marker_defers_to_record_context_or_a_real_label(marker):
    fields = [dict(kind="field", index=i+10, name=f"child{i+1}_address_street",
                   text="Street Address", page=1, x=157.44, y=700-i*117.04)
              for i in range(2)]
    label = dict(kind="text", index=0, role="H4", page=1, x=49.087, y=582.023, text=marker)
    assert _distinguishing_context(fields[1], [label]) == ("Child 2", "name_convention")
    suggestions = _duplicate_field_distinguishers(fields, [label] + fields)
    assert suggestions[1]["suggested"] == "Child 2 — Street Address"
    assert suggestions[1]["distinguisherSource"] == "name_convention"
    real_label = dict(label, index=1, role="P", x=30, text="Mailing address")
    assert _nearby_row_label(fields[1], [label, real_label]) == "Mailing address"


@pytest.mark.parametrize("text", ["  Savings   ", "Section 8 benefits", "Page count", "529 Plan"])
def test_row_label_keeps_padding_and_meaningful_numbered_labels(text):
    field = dict(page=0, x=150, y=600)
    label = dict(kind="text", page=0, x=30, y=600, text=text)
    assert _nearby_row_label(field, [label]) == text.strip()


def test_row_label_ties_are_independent_of_traversal_order():
    field = dict(page=0, x=150, y=600)
    labels = [dict(kind="text", page=0, x=30, y=600, text=text)
              for text in ("Amount due", "Savings", "Balance")]
    assert _nearby_row_label(field, labels) == "Balance"
    assert _nearby_row_label(field, list(reversed(labels))) == "Balance"
    assert _nearby_row_label(field, [dict(labels[0], x=None)]) == ""


def test_wrapped_title_and_separate_cells():
    root = ET.fromstring("""<pdf2xml>
      <fontspec id="0" size="12" family="Arial"/>
      <fontspec id="1" size="18" family="Arial Bold"/>
      <page width="612" height="792">
        <text top="20" left="150" width="300" height="18" font="1">Petition for Appointment</text>
        <text top="41" left="190" width="220" height="18" font="1">of a Guardian</text>
        <text top="100" left="20" width="300" height="12" font="0">Ordinary body text provides the dominant font size for this document and its many answers.</text>
        <text top="150" left="20" width="70" height="12" font="0">Docket No.</text>
        <text top="150" left="100" width="200" height="18" font="1">Massachusetts Trial Court</text>
        <text top="200" left="20" width="200" height="18" font="1">1. Minor information:</text>
        <text top="221" left="20" width="200" height="18" font="1">2. Guardian information:</text>
      </page></pdf2xml>""")
    candidates = _heading_candidates_from_xml(root)
    texts = [item["text"] for item in candidates]
    assert "Petition for Appointment of a Guardian" in texts
    assert "Docket No. Massachusetts Trial Court" not in texts
    assert "1. Minor information:" in texts
    assert "2. Guardian information:" in texts
    title = next(item for item in candidates if item["text"].startswith("Petition"))
    assert title["box"]["height"] == 39 / 792


def test_placeholder_and_unrelated_duplicate_controls():
    fields = [
        dict(index=0, name="ChildName", text="undefined", page=0, x=20, y=100),
        dict(index=1, name="ChildName2", text="undefined", page=0, x=20, y=80),
    ]
    suggestions = _duplicate_field_distinguishers(fields, fields)
    assert suggestions[0]["suggested"] == "Child Name (line 1 of 2)"
    for item in fields:
        item["text"] = "Other"
    assert _duplicate_field_distinguishers(fields, fields) == []
    for item in fields:
        item["text"] = "Name"
    fields[1]["name"] = "unrelated_control"
    fields[1]["y"] = 10
    suggestions = _duplicate_field_distinguishers(fields, fields)
    assert len(suggestions) == 2
    assert len({item["suggested"] for item in suggestions}) == 2


def test_outline_checks_ignore_multiple_runs_in_same_heading():
    sequence = [
        dict(index=0, role="H2", structureId="a", page=0, x=20, y=100),
        dict(index=1, role="H2", structureId="a", page=0, x=20, y=90),
        dict(index=2, role="H2", structureId="b", page=0, x=20, y=80),
    ]
    findings = _heading_outline_findings(sequence)
    assert [item["announcedIndex"] for item in findings] == [0]
    sequence[1]["structureId"] = "c"
    assert any("possible-wrap" in item["id"] for item in _heading_outline_findings(sequence))


def test_excludes_notice_box_and_button_label(tmp_path):
    path = tmp_path / "geometry.pdf"
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Contents = pdf.make_stream(b"20 600 400 80 re S")
    widget = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Btn, T="choice", Rect=[20, 500, 32, 512],
    ))
    page.Annots = pikepdf.Array([widget])
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([widget]))
    pdf.save(path)
    boxes = _heading_exclusion_boxes(str(path))[0]
    notice = dict(box=dict(x=30/612, y=120/792, width=300/612, height=30/792))
    option = dict(box=dict(x=38/612, y=280/792, width=70/612, height=12/792))
    section = dict(box=dict(x=20/612, y=400/792, width=200/612, height=18/792))
    assert any(_heading_overlaps_exclusion(notice, box) for box in boxes)
    assert any(_heading_overlaps_exclusion(option, box) for box in boxes)
    assert not any(_heading_overlaps_exclusion(section, box) for box in boxes)


def test_duplicate_group_keeps_array_records_and_rejects_middle_outlier():
    fields = [
        dict(index=0, name="guardian1_name_full", text="Print Name", page=0, x=20, y=600),
        dict(index=1, name="servicer_name_full", text="Print Name", page=0, x=400, y=400),
        dict(index=2, name="guardian2_name_full", text="Print Name", page=2, x=200, y=200),
    ]
    suggestions = _duplicate_field_distinguishers(fields, fields)
    assert [s["announcedIndex"] for s in suggestions] == [0, 2, 1]
    # The naming convention's own role+index ("Guardian 1"/"Guardian 2") is
    # real evidence of purpose; it wins over a bare position number.
    assert {s["suggested"] for s in suggestions if s["announcedIndex"] != 1} == {
        "Guardian 1 — Print Name", "Guardian 2 — Print Name",
    }
    assert all(
        s["distinguisherSource"] == "name_convention"
        for s in suggestions
        if s["announcedIndex"] != 1
    )
    assert suggestions[-1]["suggested"] == "Print Name (line 2 of 3)"


def test_duplicate_group_keeps_local_members_on_either_side_of_outlier():
    fields = [
        dict(index=0, name="a", text="Notes", page=0, x=20, y=600),
        dict(index=1, name="outlier", text="Notes", page=0, x=400, y=590),
        dict(index=2, name="b", text="Notes", page=0, x=20, y=580),
    ]
    assert {s["announcedIndex"] for s in _duplicate_field_distinguishers(fields, fields)} == {0, 1, 2}


def test_component_row_outsider_changes_only_that_components_noun():
    fields = [
        dict(kind="field", index=0, name="guardian1_name", text="Name", page=0, x=20, y=600),
        dict(kind="field", index=1, name="unrelated", text="Name", page=0, x=500, y=600),
        dict(kind="field", index=2, name="guardian2_name", text="Name", page=1, x=20, y=600),
    ]
    suggestions = _duplicate_field_distinguishers(fields, fields)
    related = [item for item in suggestions if item["announcedIndex"] in {0, 2}]
    assert {item["noun"] for item in related} == {"row"}


def test_generic_option_phrase_is_not_numbered_across_questions():
    fields = [
        dict(index=i, name=name, text="not receiving this service", page=0, x=200, y=600-i*36)
        for i, name in enumerate(["patient_education_none", "patient_vocational_none"])
    ]
    assert _duplicate_field_distinguishers(fields, fields) == []


def test_generic_label_does_not_veto_array_evidence():
    fields = [dict(index=i, name=f"owner{i+1}_no", text="no", page=i,
                   x=20, y=600-i*300) for i in range(2)]
    assert len(_duplicate_field_distinguishers(fields, fields)) == 2


def test_distant_signature_block_attributes_do_not_need_numeric_names():
    for label in ("Date", "Signature"):
        fields = [dict(index=i, name=f"{person}_{label.lower()}", text=label,
                       page=0, x=20+i*300, y=600-i*300)
                  for i, person in enumerate(("applicant", "witness"))]
        assert len(_duplicate_field_distinguishers(fields, fields)) == 2


def test_single_letter_placeholder_does_not_group_unrelated_fields():
    fields = [dict(index=i, name=name, text="a", page=0, x=20, y=600-i*20)
              for i, name in enumerate(("county", "court", "plaintiff", "docket", "defendant"))]
    assert _duplicate_field_distinguishers(fields, fields) == []


def test_inline_fragments_and_blanks_are_not_column_starts():
    from docassemble.ALDashboard.pdf_accessibility import _layout_review_findings
    sequence = [
        _layout_run(0, "The applicant", 20, 700),
        _layout_run(1, "________", 80, 700),
        _layout_run(2, "requests", 130, 700),
        _layout_run(3, "the following", 20, 688),
        _layout_run(4, "________", 80, 688),
        _layout_run(5, "relief.", 130, 688),
    ]
    assert not any("column-wrap" in f["id"] for f in _layout_review_findings(sequence))


def test_section_numerals_join_across_fonts_gaps_and_baseline_rounding():
    root = ET.fromstring("""<pdf2xml>
      <fontspec id="0" size="14" family="Arial"/>
      <fontspec id="1" size="14" family="Arial Bold"/>
      <page width="918" height="1188">
        <text top="100" left="20" width="600" height="19" font="0">This ordinary body text supplies the dominant font size used throughout the form.</text>
        <text top="267" left="61" width="12" height="19" font="0">1.</text>
        <text top="266" left="86" width="189" height="19" font="1">Information about the Minor:</text>
        <text top="406" left="61" width="12" height="19" font="0">2.</text>
        <text top="406" left="86" width="113" height="19" font="1">The Petitioner is:</text>
        <text top="500" left="61" width="12" height="19" font="0">3.</text>
        <text top="500" left="200" width="113" height="19" font="1">Separate column</text>
      </page></pdf2xml>""")
    texts = [h["text"] for h in _heading_candidates_from_xml(root)]
    assert "1. Information about the Minor:" in texts
    assert "2. The Petitioner is:" in texts
    assert "3. Separate column" not in texts


def test_bordered_title_survives_but_body_notice_is_excluded(tmp_path):
    import shutil
    import pytest
    from docassemble.ALDashboard.pdf_accessibility import suggest_heading_candidates

    if not shutil.which("pdftohtml"):
        pytest.skip("Poppler required")
    path = tmp_path / "boxed-title.pdf"
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    font = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica,
    ))
    bold = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/Helvetica-Bold"),
    ))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font, F2=bold))
    page.Contents = pdf.make_stream(
        b"40 600 400 130 re S 40 350 400 70 re S "
        b"BT /F2 18 Tf 60 700 Td (COURT ACTIVITY RECORD) Tj "
        b"0 -24 Td (INFORMATION AND WARRANT) Tj "
        b"0 -24 Td (MANAGEMENT SYSTEM RELEASE) Tj "
        b"0 -24 Td (REQUEST FORM) Tj ET "
        b"BT /F1 12 Tf 40 500 Td "
        b"(Ordinary body text supplies the dominant size for the many answers in this form.) Tj ET "
        b"BT /F2 12 Tf 60 390 Td (Keep this information confidential.) Tj ET"
    )
    pdf.save(path)
    headings = suggest_heading_candidates(str(path))
    text = " ".join(h["text"] for h in headings)
    assert "COURT ACTIVITY RECORD" in text
    assert "REQUEST FORM" in text
    assert "Keep this information confidential." not in text


def test_auto_fill_preserves_parent_and_widget_tooltips(tmp_path):
    from docassemble.ALDashboard.pdf_accessibility import apply_pdf_accessibility_settings

    source = tmp_path / "tooltips.pdf"
    output = tmp_path / "updated.pdf"
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page()
    parent = pdf.make_indirect(pikepdf.Dictionary(
        FT=pikepdf.Name.Tx, T="FullName", TU="Full legal name",
    ))
    first = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Widget,
        Parent=parent, Rect=[20, 600, 200, 620],
    ))
    second = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Widget,
        Parent=parent, TU="Co-petitioner's full legal name", Rect=[20, 500, 200, 520],
    ))
    missing = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Tx, T="PhoneNumber", Rect=[20, 400, 200, 420],
    ))
    parent.Kids = pikepdf.Array([first, second])
    page.Annots = pikepdf.Array([first, second, missing])
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([parent, missing]))
    pdf.save(source)
    apply_pdf_accessibility_settings(input_pdf_path=str(source), output_pdf_path=str(output))
    with pikepdf.open(output) as updated:
        widgets = updated.pages[0].Annots
        assert str(widgets[0].Parent.TU) == "Full legal name"
        assert "/TU" not in widgets[0]
        assert str(widgets[1].TU) == "Co-petitioner's full legal name"
        assert str(widgets[2].TU) == "Phone Number"
    apply_pdf_accessibility_settings(
        input_pdf_path=str(output), output_pdf_path=str(output),
        field_tooltips={"FullName": "Reviewed name"},
    )
    with pikepdf.open(output) as updated:
        assert all(str(a.TU) == "Reviewed name" for a in list(updated.pages[0].Annots)[:2])


def test_artifact_inside_text_object_does_not_hide_following_title(tmp_path):
    from unittest.mock import patch
    from docassemble.ALDashboard.pdf_accessibility import create_draft_structure_tree, _readback_sequence

    source, output = tmp_path / "mixed.pdf", tmp_path / "tagged.pdf"
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(
        F1=pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                             BaseFont=pikepdf.Name.Helvetica),
    ))
    page.Contents = pdf.make_stream(
        b"BT /F1 12 Tf /Artifact BMC 1 0 0 1 20 740 Tm (Decorative label) Tj EMC "
        b"1 0 0 1 20 700 Tm (Document title) Tj ET"
    )
    pdf.save(source)
    with patch("docassemble.ALDashboard.pdf_accessibility.suggest_heading_candidates",
               return_value=[dict(candidateId="title", pageIndex=0, text="Document title", suggestedTag="H1")]):
        create_draft_structure_tree(str(source), str(output))
    with pikepdf.open(output) as tagged:
        sequence = _readback_sequence(tagged)
        assert [(s["role"], s["text"]) for s in sequence] == [("H1", "Document title")]


def test_text_geometry_applies_scaled_translations_rotation_and_graphics_state():
    import pytest
    from docassemble.ALDashboard.pdf_accessibility import _text_run_geometry
    pdf = pikepdf.Pdf.new()
    stream = pdf.make_stream(
        b"q 2 0 0 2 5 7 cm BT 10 0 0 10 100 700 Tm (A) Tj "
        b"0 -1.15 Td (B) Tj ET Q "
        b"BT 0 1 -1 0 20 30 Tm 0 -12 Td (Vertical) Tj ET"
    )
    runs = list(_text_run_geometry(list(pikepdf.parse_content_stream(stream))).values())
    assert runs[0] == dict(x=205, y=1407, rotation=0)
    assert runs[1]["y"] == pytest.approx(1384)
    assert runs[2] == dict(x=32, y=30, rotation=90)


def _layout_run(index, text, x, y, role="P", rotation=0, page=0):
    return dict(index=index, text=text, x=x, y=y, role=role, rotation=rotation,
                page=page, kind="text", font="Test", structureId=str(index))


def test_sidebar_heading_group_ignores_intervening_other_column():
    from docassemble.ALDashboard.pdf_accessibility import _layout_review_findings
    sequence = [
        _layout_run(0, "General", 30, 700, "H2"),
        _layout_run(1, "Complete the enclosed form.", 180, 700),
        _layout_run(2, "Information", 30, 686, "H2"),
    ]
    findings = _layout_review_findings(sequence)
    grouped = next(f for f in findings if f["review"]["task"] == "merge_heading_or_demote_paragraph")
    assert [i["index"] for i in grouped["review"]["items"]] == [0, 2]
    assert grouped["confidence"] == "heuristic"


def test_sentence_punctuation_does_not_hide_bold_paragraph_fragmentation():
    from docassemble.ALDashboard.pdf_accessibility import _layout_review_findings
    sequence = [
        _layout_run(0, "Read these instructions.", 30, 700, "H2"),
        _layout_run(1, "Complete every question.", 30, 686, "H2"),
        _layout_run(2, "Sign the form!", 30, 672, "H2"),
    ]
    findings = _layout_review_findings(sequence)
    assert any(len(f["review"]["items"]) == 3 for f in findings)


def test_vertical_letters_and_rotated_text_have_review_evidence():
    from docassemble.ALDashboard.pdf_accessibility import _layout_review_findings
    sequence = []
    for index, letter in enumerate("PRIVATE"):
        sequence.extend([
            _layout_run(index*2, letter, 20, 700-index*10),
            _layout_run(index*2+1, "Main paragraph text", 100, 700-index*10),
        ])
    sequence.append(_layout_run(14, "Rotated sidebar", 580, 700, rotation=90))
    findings = _layout_review_findings(sequence)
    vertical = next(f for f in findings if f["review"]["task"] == "group_vertical_letters_and_review_order")
    assert "PRIVATE" in vertical["detail"]
    assert all(i["x"] == 20 for i in vertical["review"]["items"])
    assert any(f["review"]["items"][0]["rotation"] == 90 for f in findings)


def test_multiline_column_header_interleaving_is_flagged_but_data_rows_are_not():
    from docassemble.ALDashboard.pdf_accessibility import _layout_review_findings
    sequence = [
        _layout_run(0, "Savings/", 60, 700),
        _layout_run(1, "Certificates", 180, 700),
        _layout_run(2, "Checking", 60, 688),
        _layout_run(3, "of Deposit", 180, 688),
    ]
    assert any(f["review"]["task"] == "review_column_header_order"
               for f in _layout_review_findings(sequence))
    for index, run in enumerate(sequence):
        run["text"] = str(index)
    assert _layout_review_findings(sequence) == []


def test_bare_yes_no_are_not_automatic_headings():
    root = ET.fromstring("""<pdf2xml>
      <fontspec id="0" size="12" family="Arial"/>
      <fontspec id="1" size="12" family="Arial Bold"/>
      <page width="612" height="792">
        <text top="100" left="20" width="500" height="12" font="0">The plain-weight question asks whether any other person is currently a guardian.</text>
        <text top="120" left="20" width="20" height="12" font="1">Yes</text>
        <text top="120" left="100" width="20" height="12" font="1">No</text>
      </page></pdf2xml>""")
    assert _heading_candidates_from_xml(root) == []


def test_only_first_identity_of_running_title_survives_on_its_first_page():
    root = ET.fromstring("""<pdf2xml>
      <fontspec id="0" size="12" family="Arial"/>
      <fontspec id="1" size="18" family="Arial Bold"/>
      <page width="612" height="792">
        <text top="20" left="100" width="400" height="18" font="1">Annual Guardianship Status Report</text>
        <text top="70" left="100" width="400" height="18" font="1">Annual Guardianship Status Report</text>
        <text top="200" left="20" width="560" height="12" font="0">Ordinary body text supplies the dominant size used throughout this form.</text>
      </page>
      <page width="612" height="792">
        <text top="20" left="100" width="400" height="18" font="1">Annual Guardianship Status Report</text>
        <text top="200" left="20" width="560" height="12" font="0">More ordinary body text supplies the dominant size on the next page.</text>
      </page>
    </pdf2xml>""")
    headings = _heading_candidates_from_xml(root)
    assert [item["text"] for item in headings].count(
        "Annual Guardianship Status Report"
    ) == 1


def test_image_coverage_tracks_draws_artifacts_and_nested_forms():
    from docassemble.ALDashboard.pdf_accessibility import _unmarked_image_findings, _artifact_untagged_content
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page()
    image = pdf.make_stream(bytes([0] * 32 * 32))
    image.Type = pikepdf.Name.XObject
    image.Subtype = pikepdf.Name.Image
    image.Width, image.Height = 32, 32
    image.ColorSpace = pikepdf.Name.DeviceGray
    image.BitsPerComponent = 8
    form = pdf.make_stream(b"/Seal Do")
    form.Type, form.Subtype = pikepdf.Name.XObject, pikepdf.Name.Form
    form.BBox = pikepdf.Array([0, 0, 100, 100])
    form.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Seal=image))
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm=form, Unused=image))
    page.Contents = pdf.make_stream(b"/Artifact BMC /Fm Do EMC /Fm Do")
    findings = _unmarked_image_findings(pdf)
    assert len(findings) == 1
    assert findings[0]["assetId"] == "p1:Fm/Seal"
    assert "1 time(s)" in findings[0]["detail"]
    assert findings[0]["review"]["requiresImagePreview"]
    # The seal is not automatically hidden, and alt text alone is no coverage.
    image.Alt = "Seal"
    _artifact_untagged_content(pdf)
    assert len(_unmarked_image_findings(pdf)) == 1
    page.Contents = pdf.make_stream(b"/Figure <</MCID 0>> BDC /Fm Do EMC")
    assert _unmarked_image_findings(pdf) == []


def test_hairline_image_invocation_is_artifacted_without_changing_pixels():
    from docassemble.ALDashboard.pdf_accessibility import _artifact_untagged_content, _unmarked_image_findings
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page()
    rule = pdf.make_stream(bytes([0]*14))
    rule.Type, rule.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    rule.Width, rule.Height = 14, 1
    rule.ColorSpace, rule.BitsPerComponent = pikepdf.Name.DeviceGray, 8
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Rule=rule))
    page.Contents = pdf.make_stream(b"/Rule Do")
    assert _artifact_untagged_content(pdf) == 1
    assert rule.read_bytes() == bytes([0]*14)
    assert _unmarked_image_findings(pdf) == []
    assert _artifact_untagged_content(pdf) == 0


def test_review_sampling_keeps_later_pages_and_different_problem_types():
    from docassemble.ALDashboard.pdf_accessibility import _select_readback_review_findings
    findings = [dict(id=str(i), page=0, category="reading-order") for i in range(50)]
    findings.append(dict(id="late-sidebar", page=8, category="heading-outline"))
    assert _select_readback_review_findings(findings, 2)[1]["id"] == "late-sidebar"


def test_ai_review_receives_bounded_layout_evidence_and_uncertainty():
    import json
    from unittest.mock import patch
    from docassemble.ALDashboard.pdf_accessibility import review_pdf_accessibility_with_ai
    with patch("docassemble.ALToolbox.llms.chat_completion", return_value={"findings": []}) as completion:
        review_pdf_accessibility_with_ai({
            "readbackFindings": [{
                "id": "sidebar", "page": 8, "confidence": "heuristic",
                "review": {"task": "merge_heading_or_demote_paragraph",
                           "items": [_layout_run(1, "Instructions.", 30, 600, "H2")]},
            }],
        })
    messages = completion.call_args.kwargs["messages"]
    evidence = json.loads(messages[1]["content"])["readbackFindings"][0]
    assert evidence["review"]["items"][0]["x"] == 30
    assert evidence["confidence"] == "heuristic"
    assert "possible semantic/layout defects" in messages[0]["content"]

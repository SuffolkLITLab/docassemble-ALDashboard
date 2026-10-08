# do not pre-load
import pikepdf
import pytest

from docassemble.ALDashboard.pdf_accessibility import (
    _extract_field_records, _field_label_inputs, _readback_sequence,
    _structure_editor_data, apply_pdf_accessibility_settings, repair_readback_text,
)


@pytest.mark.parametrize("nested", [False, True])
def test_actualtext_replaces_owning_element_once(tmp_path, nested):
    source, output = tmp_path / "source.pdf", tmp_path / "out.pdf"
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page()
        page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica,
        )))
        page.Contents = pdf.make_stream(b"/P <</MCID 0>> BDC BT /F1 12 Tf 50 700 Td (First) Tj ET EMC /P <</MCID 1>> BDC BT /F1 12 Tf 50 680 Td (Second) Tj ET EMC")
        root = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot))
        owner = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructElem, S=pikepdf.Name.P, P=root, Pg=page.obj, ActualText=pikepdf.String("Whole paragraph")))
        if nested:
            owner.K = pikepdf.Array([pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name.StructElem, S=pikepdf.Name.Span, P=owner, K=i,
                ActualText=pikepdf.String("Child override"),
            )) for i in range(2)])
        else:
            owner.K = pikepdf.Array([0, 1])
        root.K = owner
        pdf.Root.StructTreeRoot = root
        before = _readback_sequence(pdf)
        assert len(before) == 1
        assert before[0]["text"] == "Whole paragraph"
        assert before[0]["drawn"] == "First Second"
        assert before[0]["replaced"]
        pdf.save(source)
    # A subsequent correction updates the owner, not an arbitrary child MCID.
    result = repair_readback_text(str(source), str(output), decisions=[{
        "contentId": before[0]["contentId"], "actualText": "Corrected paragraph", "apply": True,
    }])
    assert result["actual_text_added"] == 1
    with pikepdf.open(output) as pdf:
        assert str(pdf.Root.StructTreeRoot.K.ActualText) == "Corrected paragraph"
        assert len(pdf.Root.StructTreeRoot.K.K) == 2
        assert [item["text"] for item in _readback_sequence(pdf)] == ["Corrected paragraph"]
        # Empty ActualText is still a replacement, not permission to read children.
        pdf.Root.StructTreeRoot.K.ActualText = pikepdf.String("")
        assert [item["text"] for item in _readback_sequence(pdf)] == [""]


def test_hierarchical_names_match_inspection_tooltips_and_order(tmp_path):
    source, output = tmp_path / "hierarchy.pdf", tmp_path / "out.pdf"
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page()
        roots, widgets = [], []
        for prefix in ["person", "company"]:
            parent = pdf.make_indirect(pikepdf.Dictionary(T=pikepdf.String(prefix)))
            field = pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Widget,
                T=pikepdf.String("name__7"), Parent=parent, FT=pikepdf.Name.Tx,
                Rect=pikepdf.Array([20, 30, 100, 50]), P=page.obj,
            ))
            parent.Kids = pikepdf.Array([field])
            roots.append(parent)
            widgets.append(field)
        page.Annots = pikepdf.Array(widgets)
        pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array(roots))
        fields, order = _extract_field_records(pdf)
        assert [f["name"] for f in fields] == ["person.name__7", "company.name__7"]
        assert order == ["person.name__7", "company.name__7"]
        assert [f["name"] for f in _field_label_inputs(pdf)] == order
        assert [f["name"] for f in _structure_editor_data(pdf)["widgets"]] == order
        pdf.save(source)
    apply_pdf_accessibility_settings(input_pdf_path=str(source), output_pdf_path=str(output),
        field_tooltips={"person.name__7": "Person name", "company.name__7": "Company name"},
        field_order=["company.name__7", "person.name__7"])
    with pikepdf.open(output) as pdf:
        records, order = _extract_field_records(pdf)
        assert order == ["company.name__7", "person.name__7"]
        assert {f["name"]: f["tooltip"] for f in records} == {
            "person.name__7": "Person name", "company.name__7": "Company name",
        }
        assert str(pdf.Root.AcroForm.Fields[0].T) == "company"
        assert str(pdf.Root.AcroForm.Fields[0].Kids[0].T) == "name__7"

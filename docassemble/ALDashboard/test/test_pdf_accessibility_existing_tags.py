# do not pre-load
"""Direct edits preserve imported structure and its content associations."""
import pikepdf
import pytest

from docassemble.ALDashboard.pdf_accessibility import (
    PDFAccessibilityError,
    _structure_editor_data,
    apply_manual_structure_repairs,
)


def tagged_pdf(path):
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page()
        root = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot))
        paragraph = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.StructElem, S=pikepdf.Name.P, P=root,
            Pg=page.obj, K=0, ID=pikepdf.String("original-id"),
        ))
        root.K = pikepdf.Array([paragraph])
        root.ParentTree = pdf.make_indirect(pikepdf.Dictionary(
            Nums=pikepdf.Array([0, pikepdf.Array([paragraph])]),
        ))
        page.obj.StructParents = 0
        page.obj.Contents = pdf.make_stream(b"/P <</MCID 0>> BDC EMC")
        pdf.Root.StructTreeRoot = root
        pdf.save(path)


def test_edit_imported_tag_preserves_references(tmp_path):
    source, output = tmp_path / "in.pdf", tmp_path / "out.pdf"
    tagged_pdf(source)
    apply_manual_structure_repairs(str(source), str(output), [{
        "action": "edit_tag", "path": "0", "role": "H2",
        "title": "Section", "actualText": "Corrected text", "altText": "Description",
    }])
    with pikepdf.open(output) as pdf:
        root = pdf.Root.StructTreeRoot
        tag = root.K[0]
        assert tag.S == "/H2"
        assert tag.K == 0
        assert str(tag.ID) == "original-id"
        assert tag.P.objgen == root.objgen
        assert tag.Pg.objgen == pdf.pages[0].obj.objgen
        assert root.ParentTree.Nums[1][0].objgen == tag.objgen
        assert pdf.pages[0].Contents.read_bytes() == b"/P <</MCID 0>> BDC EMC"
        node = _structure_editor_data(pdf)["nodes"][0]
        assert node["actualText"] == "Corrected text"
        assert node["title"] == "Section"
        assert node["altText"] == "Description"
    apply_manual_structure_repairs(str(output), str(output), [{
        "action": "edit_tag", "path": "0", "role": "H2", "actualText": "",
    }])
    with pikepdf.open(output) as pdf:
        assert "/ActualText" not in pdf.Root.StructTreeRoot.K[0]
        assert str(pdf.Root.StructTreeRoot.K[0].Alt) == "Description"


def test_reject_structural_role_conversion_and_invalid_path(tmp_path):
    source, output = tmp_path / "in.pdf", tmp_path / "out.pdf"
    tagged_pdf(source)
    for operation in [
        {"path": "0", "role": "Table"},
        {"path": "99", "role": "P"},
    ]:
        with pytest.raises(PDFAccessibilityError):
            apply_manual_structure_repairs(str(source), str(output), [
                {"action": "edit_tag", **operation},
            ])


def test_tag_references_use_inherited_pages_and_keep_stream_identity(tmp_path):
    source = tmp_path / "refs.pdf"
    tagged_pdf(source)
    with pikepdf.open(source) as pdf:
        root = pdf.Root.StructTreeRoot
        parent = root.K[0]
        parent.S = pikepdf.Name.Sect
        stream = pdf.make_stream(b"")
        child = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.StructElem, S=pikepdf.Name.P, P=parent,
            K=pikepdf.Array([3, pikepdf.Dictionary(
                Type=pikepdf.Name.MCR, MCID=4, Stm=stream,
            )]),
        ))
        annot = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link,
            Rect=pikepdf.Array([20, 30, 100, 50]),
        ))
        link = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.StructElem, S=pikepdf.Name.Link, P=parent,
            K=pikepdf.Dictionary(Type=pikepdf.Name.OBJR, Obj=annot),
        ))
        parent.K = pikepdf.Array([child, link])
        nodes = _structure_editor_data(pdf)["nodes"]
        assert nodes[0]["contentRefs"] == []
        assert nodes[1]["pageIndex"] == 0
        assert nodes[1]["contentRefs"] == [
            {"pageIndex": 0, "mcid": 3, "stream": "page"},
            {"pageIndex": 0, "mcid": 4, "stream": str(stream.objgen)},
        ]
        assert nodes[2]["contentRefs"] == [{"pageIndex": 0, "rect": [20., 30., 100., 50.]}]


def merge_fixture(path):
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page()
        root = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot))
        document = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.StructElem, S=pikepdf.Name.Document, P=root, Pg=page.obj,
        ))
        tags = [pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.StructElem, S=pikepdf.Name.P, P=document, K=i,
            ActualText=pikepdf.String(text), ID=pikepdf.String(f"tag-{i}"),
        )) for i, text in enumerate(["First", "Second", "Third"])]
        tags[1].Lang = pikepdf.String("en-US")
        document.K = pikepdf.Array(tags)
        root.K = document
        root.ParentTree = pdf.make_indirect(pikepdf.Dictionary(Nums=pikepdf.Array([0, pikepdf.Array(tags)])))
        root.IDTree = pdf.make_indirect(pikepdf.Dictionary(Names=pikepdf.Array([
            pikepdf.String("tag-0"), tags[0], pikepdf.String("tag-1"), tags[1], pikepdf.String("tag-2"), tags[2],
        ])))
        page.obj.StructParents = 0
        page.obj.Contents = pdf.make_stream(b"/P <</MCID 0>> BDC EMC /P <</MCID 1>> BDC EMC /P <</MCID 2>> BDC EMC")
        pdf.Root.StructTreeRoot = root
        pdf.save(path)


def test_merge_preserves_order_properties_and_both_reference_trees(tmp_path):
    source, output = tmp_path / "merge.pdf", tmp_path / "out.pdf"
    merge_fixture(source)
    result = apply_manual_structure_repairs(str(source), str(output), [{
        "action": "merge_tags", "paths": ["0/1", "0/0"],
    }])
    assert result["tags_merged"] == 2
    with pikepdf.open(source) as before, pikepdf.open(output) as pdf:
        document = pdf.Root.StructTreeRoot.K
        assert len(document.K) == 2
        merged = document.K[0]
        assert merged.S == "/P"
        assert merged.DAWorkshopMerged
        assert str(document.K[1].ActualText) == "Third"
        for index, text in enumerate(["First", "Second"]):
            span = merged.K[index]
            assert span.S == "/Span"
            assert span.P.objgen == merged.objgen
            assert span.K == index
            assert str(span.ActualText) == text
            assert str(span.ID) == f"tag-{index}"
            assert pdf.Root.StructTreeRoot.ParentTree.Nums[1][index].objgen == span.objgen
            assert pdf.Root.StructTreeRoot.IDTree.Names[index * 2 + 1].objgen == span.objgen
        assert str(merged.K[1].Lang) == "en-US"
        assert merged.Pg.objgen == pdf.pages[0].obj.objgen
        assert pdf.pages[0].Contents.read_bytes() == before.pages[0].Contents.read_bytes()
        nodes = _structure_editor_data(pdf)["nodes"]
        assert next(n for n in nodes if n["path"] == "0/0")["text"] == "First Second"
        assert [n["pageIndex"] for n in nodes if n["path"].startswith("0/0/")] == [0, 0]
    # A merged tag can be merged again without losing its preserved spans.
    apply_manual_structure_repairs(str(output), str(output), [{"action": "merge_tags", "paths": ["0/0", "0/1"]}])
    with pikepdf.open(output) as pdf:
        assert len(pdf.Root.StructTreeRoot.K.K) == 1
        assert _structure_editor_data(pdf)["nodes"][1]["text"] == "First Second Third"


@pytest.mark.parametrize("paths", [["0/0"], ["0/0", "0/0"], ["0/0", "0/2"], ["0", "0/0"], ["0/8", "0/9"], ["bad", "path"]])
def test_merge_rejects_invalid_selection_without_saving(tmp_path, paths):
    source = tmp_path / "merge.pdf"
    merge_fixture(source)
    original = source.read_bytes()
    with pytest.raises(PDFAccessibilityError):
        apply_manual_structure_repairs(str(source), str(source), [{"action": "merge_tags", "paths": paths}])
    assert source.read_bytes() == original


@pytest.mark.parametrize("role", ["Form", "Table", "LI", "Figure"])
def test_merge_protects_specialized_tags(tmp_path, role):
    source = tmp_path / "merge.pdf"
    merge_fixture(source)
    with pikepdf.open(source, allow_overwriting_input=True) as pdf:
        pdf.Root.StructTreeRoot.K.K[1].S = pikepdf.Name("/" + role)
        pdf.save(source)
    with pytest.raises(PDFAccessibilityError, match="Only text tags"):
        apply_manual_structure_repairs(str(source), str(source), [{"action": "merge_tags", "paths": ["0/0", "0/1"]}])


def test_merge_cannot_jump_over_direct_content(tmp_path):
    source = tmp_path / "merge.pdf"
    merge_fixture(source)
    with pikepdf.open(source, allow_overwriting_input=True) as pdf:
        parent = pdf.Root.StructTreeRoot.K
        parent.K = pikepdf.Array([parent.K[0], 42, parent.K[1], parent.K[2]])
        pdf.save(source)
    with pytest.raises(PDFAccessibilityError, match="Unselected content"):
        apply_manual_structure_repairs(str(source), str(source), [{"action": "merge_tags", "paths": ["0/0", "0/1"]}])

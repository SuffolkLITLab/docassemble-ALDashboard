"""Content-based regressions for encoded headings and vector-outlined text."""
from unittest.mock import patch

import pikepdf
import pytest

from docassemble.ALDashboard.pdf_accessibility import (
    _decoded_instruction_texts,
    _heading_coverage_findings,
    _page_text_census,
    analyze_screen_reader_readback,
    create_draft_structure_tree,
    ocr_image_only_pages,
)


def encoded_pdf(path, *, missing_mapping=False, cross_cell=False):
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    title = "NAME AND ADDRESS"
    codes = {ch: index + 100 for index, ch in enumerate(sorted(set(title)))}
    pairs = [
        f"<{code:04X}> <{ord(ch):04X}>"
        for ch, code in codes.items() if not (missing_mapping and ch == "M")
    ]
    cmap = pdf.make_stream((
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
        "/CMapType 2 def /CMapName /Test def "
        "1 begincodespacerange <0000> <FFFF> endcodespacerange "
        f"{len(pairs)} beginbfchar " + " ".join(pairs) +
        " endbfchar endcmap CMapName currentdict /CMap defineresource pop end end"
    ).encode())
    descendant = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.CIDFontType2,
        BaseFont=pikepdf.Name.Test,
        CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
    ))
    font = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type0, BaseFont=pikepdf.Name.Test,
        Encoding=pikepdf.Name("/Identity-H"), DescendantFonts=pikepdf.Array([descendant]),
        ToUnicode=cmap,
    ))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    commands = ["BT /F1 12 Tf"]
    for index, char in enumerate(title):
        x = 20 + index*8 + (300 if cross_cell and index > 6 else 0)
        commands.append(f"1 0 0 1 {x} 700 Tm <{codes[char]:04X}> Tj")
    commands.append("ET")
    page.Contents = pdf.make_stream(" ".join(commands).encode())
    pdf.save(path)
    return dict(candidateId="title", pageIndex=0, text="NAME AND A DDRESS", suggestedTag="H1",
                box=dict(x=20/612, y=78/792, width=160/612, height=20/792))


def test_cid_font_and_more_than_four_glyph_runs_match_without_rewriting_text(tmp_path):
    source, output = tmp_path/"encoded.pdf", tmp_path/"tagged.pdf"
    candidate = encoded_pdf(source)
    with patch("docassemble.ALDashboard.pdf_accessibility.suggest_heading_candidates", return_value=[candidate]):
        result = create_draft_structure_tree(str(source), str(output))
    assert result["headings_drafted"] == 1
    assert result["unmatched_heading_candidates"] == []
    report = analyze_screen_reader_readback(str(output), heading_candidates=[candidate])
    assert [(i["role"], i["text"]) for i in report["announcements"]] == [("H1", "NAME AND ADDRESS")]
    values = []
    for path in (source, output):
        with pikepdf.open(path) as pdf:
            ins = list(pikepdf.parse_content_stream(pdf.pages[0]))
            values.append(list(_decoded_instruction_texts(pdf.pages[0], ins).values()))
    assert values[0] == values[1]


@pytest.mark.parametrize("options", [dict(missing_mapping=True), dict(cross_cell=True)])
def test_uncertain_or_cross_cell_heading_matches_remain_unmatched(tmp_path, options):
    source, output = tmp_path/"encoded.pdf", tmp_path/"tagged.pdf"
    candidate = encoded_pdf(source, **options)
    with patch("docassemble.ALDashboard.pdf_accessibility.suggest_heading_candidates", return_value=[candidate]):
        result = create_draft_structure_tree(str(source), str(output))
    assert result["headings_drafted"] == 0
    assert result["unmatched_heading_candidates"][0]["candidateId"] == "title"
    report = analyze_screen_reader_readback(str(output), heading_candidates=[candidate])
    assert any(f["id"] == "readback-heading-coverage-0" for f in report["findings"])


def vector_pdf(path, *, draw=True):
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    form = pdf.make_stream(b"0 0 m 10 0 l 10 10 l h f " * 30)
    form.Type, form.Subtype = pikepdf.Name.XObject, pikepdf.Name.Form
    form.BBox = pikepdf.Array([0, 0, 612, 792])
    form.Resources = pikepdf.Dictionary()
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Outlines=form))
    page.Contents = pdf.make_stream(b"/Outlines Do" if draw else b"")
    pdf.save(path)


def test_vector_only_page_is_flagged_before_and_after_artifact_drafting(tmp_path):
    source, output = tmp_path/"outlines.pdf", tmp_path/"tagged.pdf"
    vector_pdf(source)
    create_draft_structure_tree(str(source), str(output))
    for path in (source, output):
        report = analyze_screen_reader_readback(str(path), heading_candidates=[])
        finding = next(f for f in report["findings"] if f["id"] == "readback-vector-only-0")
        assert finding["severity"] == "fail"
        assert finding["review"]["requiresPagePreview"]
    with pikepdf.open(source) as pdf:
        census = _page_text_census(pdf.pages[0])
        assert census["total"] == census["images"] == 0
        assert census["vector_paints"] == 30


def test_text_showing_operators_are_not_counted_as_vector_paints(tmp_path):
    source = tmp_path / "text.pdf"
    encoded_pdf(source)
    with pikepdf.open(source) as pdf:
        census = _page_text_census(pdf.pages[0])
    assert census["total"] > 0
    assert census["vector_paints"] == 0


def test_ocr_vector_page_adds_invisible_text_and_keeps_drawn_content(tmp_path):
    source, output, tagged = tmp_path/"outlines.pdf", tmp_path/"ocr.pdf", tmp_path/"tagged.pdf"
    vector_pdf(source)
    words = [dict(text="Petition", left=20, top=20, width=80, height=14,
                  pixelWidth=612, pixelHeight=792, confidence=96)]
    with patch("docassemble.ALDashboard.pdf_accessibility._ocr_page_words", return_value=words) as ocr:
        result = ocr_image_only_pages(str(source), str(output))
    assert ocr.call_count == 1
    assert result["pages_read"] == 1
    assert result["pages"][0]["sourceContent"] == "vector"
    with pikepdf.open(source) as before, pikepdf.open(output) as after:
        assert before.pages[0].Contents.read_bytes() == after.pages[0].Contents[1].read_bytes()
        assert b"3 Tr" in after.pages[0].Contents[-1].read_bytes()
    create_draft_structure_tree(str(output), str(tagged))
    report = analyze_screen_reader_readback(str(tagged), heading_candidates=[])
    assert any(i["text"] == "Petition" for i in report["announcements"])
    assert not any("vector-only" in f["id"] for f in report["findings"])


def test_blank_page_with_unused_vector_resource_is_not_ocr_candidate(tmp_path):
    source, output = tmp_path/"blank.pdf", tmp_path/"result.pdf"
    vector_pdf(source, draw=False)
    with patch("docassemble.ALDashboard.pdf_accessibility._ocr_page_words") as ocr:
        result = ocr_image_only_pages(str(source), str(output))
    ocr.assert_not_called()
    assert result["pages_read"] == 0
    assert analyze_screen_reader_readback(str(output), heading_candidates=[])["findings"] == []


@pytest.mark.parametrize("array_contents", [False, True])
def test_ocr_layer_does_not_inherit_original_transform_or_clip(tmp_path, array_contents):
    import shutil
    import subprocess

    if not shutil.which("pdftotext"):
        pytest.skip("Poppler required")
    source, output = tmp_path / "transformed.pdf", tmp_path / "ocr.pdf"
    vector_pdf(source)
    with pikepdf.open(source, allow_overwriting_input=True) as pdf:
        original = pdf.pages[0].Contents.read_bytes() + b" 2 0 0 2 0 0 cm 0 0 10 10 re W n"
        stream = pdf.make_stream(original)
        pdf.pages[0].Contents = pikepdf.Array([stream]) if array_contents else stream
        pdf.save(source)
    words = [dict(text="Petition", left=20, top=20, width=80, height=14,
                  pixelWidth=612, pixelHeight=792, confidence=96)]
    with patch("docassemble.ALDashboard.pdf_accessibility._ocr_page_words", return_value=words):
        result = ocr_image_only_pages(str(source), str(output))
    assert result["pages_read"] == 1
    assert "Petition" in subprocess.check_output(["pdftotext", str(output), "-"], text=True)
    with pikepdf.open(output) as pdf:
        assert pdf.pages[0].Contents[1].read_bytes() == original


def test_failed_ocr_cannot_make_vector_page_report_clean(tmp_path):
    source, output = tmp_path/"outlines.pdf", tmp_path/"result.pdf"
    vector_pdf(source)
    with patch("docassemble.ALDashboard.pdf_accessibility._ocr_page_words", return_value=[]):
        result = ocr_image_only_pages(str(source), str(output))
    assert result["pages_read"] == 0
    assert any(f["id"] == "readback-vector-only-0"
               for f in analyze_screen_reader_readback(str(output), heading_candidates=[])["findings"])

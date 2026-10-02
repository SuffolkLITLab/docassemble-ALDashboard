"""Semantic control placement survives AI column ordering and export metadata."""
from unittest.mock import patch

import pikepdf

from docassemble.ALDashboard.pdf_accessibility import (
    analyze_screen_reader_readback, apply_pdf_accessibility_settings,
    create_draft_structure_tree,
)


def test_column_order_keeps_controls_with_labels_after_field_metadata(tmp_path):
    source, output = str(tmp_path / 'source.pdf'), str(tmp_path / 'tagged.pdf')
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica,
    )))
    commands, fields = [], []
    for name, x, y in [('A1', 30, 700), ('B1', 330, 700), ('A2', 30, 670), ('B2', 330, 670)]:
        commands.append(f'BT /F1 12 Tf 1 0 0 1 {x} {y} Tm ({name}) Tj ET')
        fields.append(pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Widget, FT=pikepdf.Name.Tx,
            T=name, TU='Control ' + name, Rect=pikepdf.Array([x+60, y-10, x+140, y+3]),
            P=page.obj,
        )))
    page.Contents = pdf.make_stream('\n'.join(commands).encode())
    page.Annots = pikepdf.Array(fields)
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array(fields))
    pdf.save(source)
    decisions = [dict(pageIndex=0, text=name, occurrence=0, role='P', roleReviewed=False,
                      order=index, orderReviewed=True) for index, name in enumerate(['A1', 'A2', 'B1', 'B2'])]
    with patch('docassemble.ALDashboard.pdf_accessibility.suggest_heading_candidates', return_value=[]):
        create_draft_structure_tree(source, output, content_decisions=decisions)
    def transcript():
        return [item['text'] for item in analyze_screen_reader_readback(output)['announcements']]
    expected = ['A1', 'Control A1', 'A2', 'Control A2', 'B1', 'Control B1', 'B2', 'Control B2']
    assert transcript() == expected
    result = apply_pdf_accessibility_settings(input_pdf_path=output, output_pdf_path=output,
                                              field_order=['B2', 'B1', 'A2', 'A1'], set_structure_tab_order=True)
    assert transcript() == expected
    # Each control is held by its label, so the caller is told none could move.
    assert result['structure_order_updates'] == 0
    assert result['structure_order_anchored'] == 4


def _text_page(tmp_path, lines, widgets=()):
    source = str(tmp_path / 'source.pdf')
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica,
    )))
    page.Contents = pdf.make_stream('\n'.join(
        f'BT /F1 12 Tf 1 0 0 1 {x} {y} Tm ({text}) Tj ET' for text, x, y in lines
    ).encode())
    fields = [pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Widget, FT=pikepdf.Name.Tx,
        T=name, TU='Control ' + name, Rect=pikepdf.Array(rect), P=page.obj,
    )) for name, rect in widgets]
    if fields:
        page.Annots = pikepdf.Array(fields)
        pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array(fields))
    pdf.save(source)
    return source


def test_moving_one_block_keeps_unreviewed_text_above_it_first(tmp_path):
    source = _text_page(tmp_path, [('Top', 30, 700), ('Middle', 30, 670), ('Last', 30, 640)])
    output = str(tmp_path / 'tagged.pdf')
    # The reviewer swapped the lower two blocks; the title was never touched.
    decisions = [dict(pageIndex=0, text=text, occurrence=0, role='P', roleReviewed=False,
                      order=order, orderReviewed=True) for text, order in [('Middle', 2), ('Last', 1)]]
    with patch('docassemble.ALDashboard.pdf_accessibility.suggest_heading_candidates', return_value=[]):
        create_draft_structure_tree(source, output, content_decisions=decisions)
    announced = [item['text'] for item in analyze_screen_reader_readback(output)['announcements']]
    assert announced == ['Top', 'Last', 'Middle']


def test_field_order_sorts_adjacent_controls_that_share_one_label(tmp_path):
    source = _text_page(tmp_path, [('Pick all that apply', 30, 700)], widgets=[
        ('first', [200, 690, 212, 703]), ('second', [220, 690, 232, 703]), ('third', [240, 690, 252, 703]),
    ])
    output = str(tmp_path / 'tagged.pdf')
    with patch('docassemble.ALDashboard.pdf_accessibility.suggest_heading_candidates', return_value=[]):
        create_draft_structure_tree(source, output)
    result = apply_pdf_accessibility_settings(input_pdf_path=output, output_pdf_path=output,
                                              field_order=['third', 'first', 'second'],
                                              set_structure_tab_order=True)
    announced = [item['text'] for item in analyze_screen_reader_readback(output)['announcements']]
    assert announced == ['Pick all that apply', 'Control third', 'Control first', 'Control second']
    assert result['structure_order_updates'] > 0
    assert result['structure_order_anchored'] == 0


def test_declaration_repair_sets_tag_flag_without_pdfua_certification(tmp_path):
    from docassemble.ALDashboard.pdf_accessibility import (
        _pdfua_part, _set_pdfua_identifier, build_accessibility_report,
    )
    source = str(tmp_path / 'stale.pdf')
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    pdf.Root.StructTreeRoot = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.StructTreeRoot,
        K=pikepdf.Array([pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructElem, S=pikepdf.Name.Document))]),
    ))
    pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=False)
    _set_pdfua_identifier(pdf, True)
    pdf.save(source)
    apply_pdf_accessibility_settings(input_pdf_path=source, output_pdf_path=source, repair_declaration=True)
    with pikepdf.open(source) as fixed:
        assert bool(fixed.Root.MarkInfo.Marked)
        assert not _pdfua_part(fixed)
        assert next(i for i in build_accessibility_report(fixed)['issues'] if i['id'] == 'mark-info')['status'] == 'pass'
    # A plain export must preserve the tag flag without adding conformance claims.
    apply_pdf_accessibility_settings(input_pdf_path=source, output_pdf_path=source)
    with pikepdf.open(source) as exported:
        assert bool(exported.Root.MarkInfo.Marked)
        assert not _pdfua_part(exported)


def test_field_anchor_prefers_label_in_its_own_column_over_closer_line_elsewhere():
    from docassemble.ALDashboard.pdf_accessibility import _field_anchor_index
    # Left-column label at y=700; a right-column line sits at 695, nearer the
    # field's top (690) but 300pt away horizontally.
    spots = [(700.0, 30.0), (695.0, 330.0)]
    assert _field_anchor_index(spots, top=690.0, left=30.0) == 0
    # The same geometry seen from a right-column field picks the right column.
    assert _field_anchor_index(spots, top=690.0, left=330.0) == 1
    assert _field_anchor_index(spots, top=800.0, left=30.0) == -1

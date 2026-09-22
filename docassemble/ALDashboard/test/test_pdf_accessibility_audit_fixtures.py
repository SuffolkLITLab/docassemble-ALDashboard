"""Optional integration checks against the all_interviews audit corpus.

Set ALDASHBOARD_AUDIT_FIXTURES to its root when it is not ~/all_interviews.
Small synthetic counterparts run without this external corpus.
"""
import os
from pathlib import Path

import pikepdf
import pytest

from docassemble.ALDashboard.pdf_accessibility import (
    _layout_review_findings,
    _readback_sequence,
    analyze_screen_reader_readback,
    create_draft_structure_tree,
    repair_duplicate_field_names,
)


def fixture_path(relative):
    root = Path(os.environ.get("ALDASHBOARD_AUDIT_FIXTURES", "~/all_interviews")).expanduser()
    path = root / relative
    if not path.is_file():
        pytest.skip(f"External audit fixture unavailable: {path}")
    return str(path)


def test_eoir_headings_are_recovered_and_unmatched_candidates_reported(tmp_path):
    source = fixture_path("repos/docassemble-ImmigrationCourtEnteringRepresentation/"
                          "docassemble/ImmigrationCourtEnteringRepresentation/data/templates/"
                          "form_eoir28_omb11250006.pdf")
    output = str(tmp_path / "tagged.pdf")
    result = create_draft_structure_tree(source, output, overwrite=True)
    assert result["headings_drafted"] >= 40
    assert result["unmatched_heading_candidates"]
    assert "could not be matched" in result["warning"]
    report = analyze_screen_reader_readback(output)
    assert any(item["role"] == "H1" and item["text"] for item in report["announcements"])


def test_outlined_petition_pages_cannot_pass_as_clean():
    source = fixture_path("repos/docassemble-PetitionToChangeNameOfAdult/docassemble/"
                          "PetitionToChangeNameOfAdult/data/templates/petition_to_change_name_of_adult.pdf")
    report = analyze_screen_reader_readback(source)
    failures = [f for f in report["findings"] if "vector-only" in f["id"]]
    assert {f["page"] for f in failures} == {0, 1}
    assert all(f["severity"] == "fail" for f in failures)


def test_distant_signature_and_date_pairs_are_repaired(tmp_path):
    source = fixture_path("eval_results/remediated_pdfs/"
                          "docassemble-PetitionToDeemSatisfied__Petition-To-Deem-Satisfied-v1.3.pdf")
    result = repair_duplicate_field_names(source, str(tmp_path / "repaired.pdf"))
    assert {item["fieldName"] for item in result["applied"]} >= {
        "petitioner_signature", "attorney_signature",
        "petitioner_signature_date", "attorney_signature_date",
    }


def test_housing_placeholder_does_not_group_caption_fields(tmp_path):
    source = fixture_path("eval_results/remediated_pdfs/"
                          "docassemble-MAHousingTRO__Housing_Temporary_Restraining_Order.pdf")
    result = repair_duplicate_field_names(source, str(tmp_path / "repaired.pdf"))
    assert not ({item["fieldName"] for item in result["applied"]} & {
        "court_county", "court_name", "plaintiff", "docket_number", "defendant",
    })


@pytest.mark.parametrize("name,expect_columns", [
    ("docassemble-MAHousingTRO__Housing_Temporary_Restraining_Order.pdf", False),
    ("docassemble-MAPetitionToSealEviction__petition_to_seal_eviction.pdf", False),
    ("docassemble-ssareportchangesletter__SSA-6233-dedicated_account_record.pdf", True),
])
def test_column_review_retains_tables_without_inline_fragment_noise(name, expect_columns):
    with pikepdf.open(fixture_path("eval_results/remediated_pdfs/" + name)) as pdf:
        findings = _layout_review_findings(_readback_sequence(pdf))
    assert any("column-wrap" in f["id"] for f in findings) == expect_columns

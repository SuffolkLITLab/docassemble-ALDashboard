"""AI response contract regressions; no live model or credentials required."""
from unittest.mock import patch

import pytest

from docassemble.ALDashboard.pdf_accessibility import (
    draft_heading_levels_with_ai,
    review_pdf_accessibility_with_ai,
)

TITLE = "GUARDIAN'S CARE PLAN REPORT"
LETTERHEAD = "Massachusetts Trial Court"


def review(row, **context):
    inputs = {
        "metadata": {"title": "draft.pdf"},
        "textSample": TITLE,
        "headings": [
            {"text": LETTERHEAD, "tag": "H1", "status": "approved"},
            {"text": TITLE, "tag": "H1", "status": "approved"},
        ],
    }
    inputs.update(context)
    with patch("docassemble.ALToolbox.llms.chat_completion", return_value={"findings": [row]}):
        return review_pdf_accessibility_with_ai(inputs)[0]


@pytest.mark.parametrize("field_name", ["Title Payee For", "Account Subtitle", "Document Language"])
@pytest.mark.parametrize("change", [None, {"kind": "metadata", "target": "title", "value": LETTERHEAD}])
def test_field_reference_never_attaches_document_metadata_change(field_name, change):
    finding = review({
        "category": "reading-order",
        "title": "The account grid is announced out of order",
        "explanation": f'The "{field_name}" field interrupts the next column. Reorder its tag.',
        "change": change,
    })
    assert "change" not in finding


@pytest.mark.parametrize("quoted", [True, False])
def test_recommended_title_wins_over_first_h1(quoted):
    named = f'"{TITLE}"' if quoted else TITLE
    finding = review({
        "category": "metadata", "title": "Document title uses a filename",
        "explanation": f"The correct document title is {named}. Use it instead of the letterhead.",
    })
    assert finding["change"] == {"kind": "metadata", "target": "title", "value": TITLE}


@pytest.mark.parametrize("quoted", [True, False])
def test_conflicting_explicit_payload_is_removed(quoted):
    named = f'"{TITLE}"' if quoted else TITLE
    finding = review({
        "category": "metadata", "title": "Document title should match the form",
        "explanation": f"Use {named} as the document title.",
        "change": {"kind": "metadata", "target": "title", "value": LETTERHEAD},
    })
    assert "change" not in finding
    assert "conflicts" in finding["changeRejectedReason"]


def test_multiple_recommended_titles_stay_advisory():
    finding = review({
        "category": "document-title", "title": "Choose the document title",
        "explanation": f'Use "{TITLE}" or prefer "{LETTERHEAD}".',
    })
    assert "change" not in finding


def test_recommendation_to_keep_current_title_does_not_fall_back_to_letterhead():
    finding = review({
        "category": "document-title", "title": "Check the document title",
        "explanation": f'Use "{TITLE}" as the document title.',
    }, metadata={"title": TITLE})
    assert "change" not in finding


def test_arbitrary_quoted_field_name_is_not_used_as_a_recommendation():
    finding = review({
        "category": "metadata", "title": "Document title is missing",
        "explanation": 'The field "Title Payee For" is not the document name. Use the real H1.',
    })
    assert finding["change"]["value"] == LETTERHEAD


def test_matching_explicit_title_change_is_retained():
    finding = review({
        "category": "metadata", "title": "Document title is a filename",
        "explanation": f'Use "{TITLE}" as the document title.',
        "change": {"kind": "metadata", "target": "title", "value": TITLE},
    })
    assert finding["change"]["value"] == TITLE


def heading_decisions(rows):
    candidates = [
        {"candidateId": "title", "text": TITLE, "suggestedTag": "H1"},
        {"candidateId": "label", "text": "Yes", "suggestedTag": "H3"},
        {"candidateId": "title", "text": "Repeated input ID", "suggestedTag": "H1"},
    ]
    with patch("docassemble.ALToolbox.llms.chat_completion", return_value={"decisions": rows}):
        return draft_heading_levels_with_ai(candidates)


def test_omitted_candidate_gets_explicit_nonapproval_in_input_order():
    decisions = heading_decisions([
        {"candidateId": "unknown", "isHeading": True, "suggestedTag": "H1"},
        {"candidateId": "title", "isHeading": True, "suggestedTag": "H1"},
    ])
    assert [d["candidateId"] for d in decisions] == ["title", "label"]
    assert decisions[0]["isHeading"] is True
    assert decisions[1]["isHeading"] is False
    assert decisions[1]["suggestedTag"] == "H3"
    assert decisions[1]["decisionSource"] == "fallback"
    assert "omitted" in decisions[1]["reason"]


@pytest.mark.parametrize("malformed", [
    {"isHeading": "false", "suggestedTag": "H1"},
    {"isHeading": 1, "suggestedTag": "H1"},
    {"isHeading": True, "suggestedTag": "H7"},
    {"suggestedTag": "H1"},
])
def test_malformed_heading_decision_cannot_approve(malformed):
    decisions = heading_decisions([dict(malformed, candidateId="title")])
    assert decisions[0]["isHeading"] is False
    assert "invalid" in decisions[0]["reason"]


def test_conflicting_duplicates_cannot_approve():
    decisions = heading_decisions([
        {"candidateId": "title", "isHeading": True, "suggestedTag": "H1"},
        {"candidateId": "title", "isHeading": False, "suggestedTag": "H1"},
        {"candidateId": "label", "isHeading": False, "suggestedTag": "H2"},
    ])
    assert all(d["isHeading"] is False for d in decisions)
    assert decisions[0]["decisionSource"] == "fallback"
    assert "decisionSource" not in decisions[1]


@pytest.mark.parametrize("rows", [None, {}, [], "not an array"])
def test_missing_decision_array_returns_complete_explicit_fallback(rows):
    assert all(d["isHeading"] is False for d in heading_decisions(rows))


def test_single_quoted_title_with_internal_apostrophe_matches_known_heading():
    finding = review({
        "category": "metadata", "title": "Document title is a filename",
        "explanation": f"Use '{TITLE}' as the document title.",
    })
    assert finding["change"]["value"] == TITLE


def test_metadata_category_alone_does_not_make_a_field_name_document_metadata():
    finding = review({
        "category": "metadata", "title": "Move Title Payee For after the account name",
        "explanation": "The field's position breaks the reading order.",
    })
    assert "change" not in finding


def test_positive_heading_without_level_is_not_approved():
    assert heading_decisions([{"candidateId": "title", "isHeading": True}])[0]["isHeading"] is False


@pytest.mark.parametrize("explanation", [
    f'Replace the current title with "{TITLE}".',
    f'"{TITLE}" is the actual document title.',
    f'Set the stored document title to "{TITLE}".',
])
def test_recommendation_grammar_does_not_revert_to_first_heading(explanation):
    finding = review({
        "category": "metadata", "title": "Document title should match the form",
        "explanation": explanation,
    })
    assert finding["change"]["value"] == TITLE


def test_duplicate_field_suggestions_and_their_evidence_reach_the_model():
    """The AI pass can only improve on a weak draft if it can see one."""
    inputs = {
        "metadata": {"title": "draft.pdf"},
        "textSample": TITLE,
        "headings": [],
        "fields": [{"fieldId": "application_date", "name": "application_date", "tooltip": "application date"}],
        "readbackFindings": [
            {
                "id": "readback-duplicate-names-date",
                "category": "field-names",
                "title": "Several controls announce the same name",
                "detail": "3 controls all announce nothing distinguishing.",
                "severity": "fail",
                "suggestions": [
                    {
                        "fieldName": "application_date", "current": "",
                        "suggested": "application date (line 1 of 3)",
                        "distinguisherSource": "position",
                    },
                ],
            },
        ],
    }
    with patch(
        "docassemble.ALToolbox.llms.chat_completion", return_value={"findings": []}
    ) as completion:
        review_pdf_accessibility_with_ai(inputs)
    sent = completion.call_args.kwargs["messages"][1]["content"]
    assert '"evidenceSource": "position"' in sent
    assert '"drafted": "application date (line 1 of 3)"' in sent
    assert '"fieldId": "application_date"' in sent


def test_ai_can_replace_a_weak_positional_draft_with_a_field_tooltip_change():
    finding = review(
        {
            "category": "field-names", "title": "Several controls announce the same name",
            "explanation": "application_date is really the filing date; give it a real label.",
            "change": {"kind": "field_tooltip", "target": "application_date", "value": "Filing date"},
        },
        fields=[{"fieldId": "application_date", "name": "application_date", "tooltip": "application date"}],
        readbackFindings=[
            {
                "id": "readback-duplicate-names-date", "category": "field-names",
                "title": "Several controls announce the same name", "severity": "fail",
                "suggestions": [
                    {"fieldName": "application_date", "current": "", "distinguisherSource": "position",
                     "suggested": "application date (line 1 of 3)"},
                ],
            },
        ],
    )
    assert finding["change"] == {
        "kind": "field_tooltip", "target": "application_date", "value": "Filing date",
    }

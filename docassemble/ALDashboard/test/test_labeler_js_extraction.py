# do not pre-load
"""Tests to verify JS extraction from labeler HTML templates to static files.

Ensures no regression from moving inline JavaScript out of the DOCX and PDF
labeler HTML templates into dedicated static JS files.
"""

import importlib.resources
import re
import unittest


def _read_package_file(*path_parts: str) -> str:
    """Read a file from the docassemble.ALDashboard package tree."""
    ref = importlib.resources.files("docassemble.ALDashboard")
    for part in path_parts:
        ref = ref / part
    with importlib.resources.as_file(ref) as path:
        return path.read_text(encoding="utf-8")


class TestDocxLabelerJsExtraction(unittest.TestCase):
    """Verify the DOCX labeler JS was correctly extracted to a static file."""

    def setUp(self):
        self.html = _read_package_file("data", "templates", "docx_labeler.html")
        self.js = _read_package_file("data", "static", "docx_labeler.js")

    # -- HTML template checks ------------------------------------------------

    def test_html_references_static_js(self):
        self.assertIn(
            'src="/packagestatic/docassemble.ALDashboard/docx_labeler.js"',
            self.html,
        )

    def test_html_has_no_inline_application_js(self):
        """Only CDN scripts, bootstrap JSON data, and static JS src tags."""
        script_tags = re.findall(r"<script([^>]*)>(.*?)</script>", self.html, re.DOTALL)
        for attrs, body in script_tags:
            body_stripped = body.strip()
            if not body_stripped:
                # Empty body = external src, fine
                continue
            if 'type="application/json"' in attrs:
                # Bootstrap JSON data block, not executable JS
                continue
            self.fail(
                f"Found inline <script> with executable JS:\n"
                f"  attrs: {attrs}\n"
                f"  body (first 120 chars): {body_stripped[:120]}"
            )

    def test_html_preserves_bootstrap_json_placeholder(self):
        self.assertIn("__LABELER_BOOTSTRAP_JSON__", self.html)
        self.assertIn('id="labeler-bootstrap"', self.html)

    def test_html_still_loads_mammoth_cdn(self):
        self.assertIn("mammoth", self.html)
        self.assertIn("cdnjs.cloudflare.com", self.html)

    def test_html_preserves_body_structure(self):
        self.assertIn('<div id="app"', self.html)
        self.assertIn("</body>", self.html)
        self.assertIn("</html>", self.html)

    # -- Static JS file checks -----------------------------------------------

    def test_js_file_is_nonempty(self):
        self.assertGreater(len(self.js.strip()), 1000)

    def test_js_contains_iife_wrapper(self):
        self.assertRegex(self.js, r"\(function\s*\(\)")
        self.assertTrue(self.js.strip().endswith("})();"))

    def test_js_contains_variable_tree(self):
        self.assertIn("AL_VARIABLE_TREE", self.js)
        self.assertIn("PERSON_ATTRIBUTES", self.js)
        self.assertIn("variableTreeExtras", self.js)
        self.assertIn("mergeVariableTree", self.js)
        self.assertIn("Selected interview variables", self.js)

    def test_js_contains_core_functions(self):
        """Key functions that the DOCX labeler UI depends on."""
        expected = [
            "fetchAuthStatus",
            "fetchModelCatalog",
            "processFile",
            "renderGenerationMethodFields",
        ]
        for fn_name in expected:
            self.assertIn(fn_name, self.js, f"Missing function: {fn_name}")

    def test_js_contains_catchall_filter_ui(self):
        self.assertIn("catchall_complete(", self.js)
        self.assertIn("catchall-extra-filters", self.js)
        self.assertIn("catchall-filter-pick", self.js)
        self.assertIn("catchall-filter-add", self.js)
        self.assertIn("COMMON_FILTER_SNIPPETS", self.js)

    def test_js_contains_api_calls(self):
        """Verify API endpoint references are present in the JS."""
        self.assertIn("/docx-labeler/api/", self.js)

    def test_js_reads_bootstrap_json(self):
        """JS must read bootstrap config injected by the server."""
        self.assertIn("parseBootstrapJson", self.js)
        self.assertIn("labeler-bootstrap", self.js)
        self.assertIn("LABELER_BOOTSTRAP", self.js)

    def test_js_references_mammoth(self):
        """The JS should reference the mammoth library loaded from the CDN."""
        self.assertIn("mammoth", self.js)


class TestPdfLabelerJsExtraction(unittest.TestCase):
    """Verify the PDF labeler JS was correctly extracted to a static file."""

    def setUp(self):
        self.html = _read_package_file("data", "templates", "pdf_labeler.html")
        self.js = _read_package_file("data", "static", "pdf_labeler.js")
        self.css = _read_package_file("data", "static", "pdf_labeler.css")
        self.api = _read_package_file("api_labelers.py")
        self.api_accessibility = _read_package_file("pdf_accessibility.py")

    # -- HTML template checks ------------------------------------------------

    def test_html_references_static_js(self):
        self.assertIn(
            'src="/packagestatic/docassemble.ALDashboard/pdf_labeler.js"',
            self.html,
        )

    def test_html_loads_js_as_module(self):
        self.assertIn('type="module"', self.html)

    def test_html_has_no_inline_application_js(self):
        """Only CDN scripts, bootstrap JSON data, and static JS src tags."""
        script_tags = re.findall(r"<script([^>]*)>(.*?)</script>", self.html, re.DOTALL)
        for attrs, body in script_tags:
            body_stripped = body.strip()
            if not body_stripped:
                # Empty body = external src, fine
                continue
            if 'type="application/json"' in attrs:
                # Bootstrap JSON data block, not executable JS
                continue
            self.fail(
                f"Found inline <script> with executable JS:\n"
                f"  attrs: {attrs}\n"
                f"  body (first 120 chars): {body_stripped[:120]}"
            )

    def test_html_preserves_bootstrap_json_placeholder(self):
        self.assertIn("__LABELER_BOOTSTRAP_JSON__", self.html)
        self.assertIn('id="labeler-bootstrap"', self.html)

    def test_html_still_loads_pdf_lib_cdn(self):
        self.assertIn("pdf-lib", self.html)
        self.assertIn("cdnjs.cloudflare.com", self.html)

    def test_html_still_loads_jszip_cdn(self):
        self.assertIn("jszip", self.html)

    def test_html_preserves_body_structure(self):
        self.assertIn("pdf-labeler-body", self.html)
        self.assertIn("</body>", self.html)
        self.assertIn("</html>", self.html)

    # -- Static JS file checks -----------------------------------------------

    def test_js_file_is_nonempty(self):
        self.assertGreater(len(self.js.strip()), 5000)

    def test_js_is_es_module(self):
        """The JS file should use ES module import syntax."""
        self.assertIn("import ", self.js)

    def test_js_imports_pdfjs(self):
        self.assertIn("pdfjsLib", self.js)
        self.assertIn("pdf.min.mjs", self.js)

    def test_js_reads_bootstrap_json(self):
        """JS must read bootstrap config injected by the server."""
        self.assertIn("parseBootstrapJson", self.js)
        self.assertIn("labeler-bootstrap", self.js)
        self.assertIn("LABELER_BOOTSTRAP", self.js)

    def test_js_contains_field_types(self):
        self.assertIn("FIELD_TYPES", self.js)
        for ft in ["text", "multiline", "checkbox", "signature", "radio"]:
            self.assertRegex(self.js, rf"[\"']{re.escape(ft)}[\"']")

    def test_js_contains_core_functions(self):
        expected = [
            "fetchAuthStatus",
            "fetchModelCatalog",
            "renderFieldsOnPages",
            "updateFieldCount",
            "updateZoomControls",
        ]
        for fn_name in expected:
            self.assertIn(fn_name, self.js, f"Missing function: {fn_name}")

    def test_js_contains_api_calls(self):
        self.assertIn("/pdf-labeler/api/", self.js)

    def test_js_references_PDFLib_global(self):
        """pdf-lib is loaded as a global in the HTML head."""
        self.assertIn("PDFLib", self.js)

    def test_js_references_JSZip_global(self):
        self.assertIn("JSZip", self.js)

    def test_pdf_deduplication_contract_is_wired_into_ui(self):
        self.assertIn("reservedOriginalNames", self.js)
        self.assertIn("showFieldRenameSummary", self.js)
        self.assertIn("field-rename-summary-modal", self.html)

    def test_field_count_refreshes_duplicate_warning(self):
        match = re.search(
            r"function updateFieldCount\(\) \{(?P<body>.*?)\n    \}",
            self.js,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        self.assertIn("updateDuplicateFieldWarning();", match.group("body"))

    def test_pdf_attachment_block_utility_is_wired_into_ui(self):
        self.assertIn("/pdf-labeler/api/attachment-block", self.js)
        self.assertIn("util-attachment-generate", self.html)

    def test_accessibility_font_remediation_has_choices_and_persistent_result(self):
        self.assertIn('id="a11y-font-embedding-summary"', self.html)
        self.assertIn('id="a11y-font-unicode-summary"', self.html)
        self.assertIn('id="a11y-embed-fonts"', self.html)
        self.assertIn('id="a11y-add-unicode-maps"', self.html)
        self.assertIn('id="a11y-font-result"', self.html)
        self.assertIn("renderFontStatus", self.js)
        self.assertIn("renderFontRemediationResult", self.js)
        self.assertIn("copyFontAdministratorRequest", self.js)
        self.assertIn("embed_exact_fonts: true", self.js)
        self.assertIn("add_unicode_maps: true", self.js)

    def test_new_accessibility_controls_tolerate_stale_template_markup(self):
        """A package refresh must not crash a page holding the prior template."""
        self.assertIn("function optionalWorkshopElement", self.js)
        optional_ids = [
            "a11y-order-list",
            "a11y-issue-list",
            "a11y-refresh-report",
            "a11y-draft-structure",
            "a11y-font-embedding-summary",
            "a11y-font-unicode-summary",
            "a11y-add-unicode-maps",
            "a11y-heading-list",
            "a11y-structure-preview",
            "a11y-save-heading-review",
            "a11y-heading-review-status",
            "a11y-figure-focus",
            "a11y-structure-focus",
            "a11y-table-list",
            "a11y-annotation-list",
            "a11y-auto-fix",
            "a11y-auto-fix-status",
        ]
        for element_id in optional_ids:
            self.assertRegex(
                self.js,
                rf'optionalWorkshopElement\(\s*"{re.escape(element_id)}"',
                f"New control {element_id} must be safe when older HTML is cached",
            )

    def test_heading_review_has_explicit_save_lifecycle(self):
        self.assertIn('id="a11y-save-heading-review"', self.html)
        self.assertIn('id="a11y-heading-review-status"', self.html)
        self.assertIn("headingReviewSavedSignature", self.js)
        self.assertIn("Unsaved changes", self.js)
        self.assertIn("Save the structure review before creating tags", self.js)

    def test_semantic_findings_focus_editable_targets(self):
        self.assertIn('id="a11y-figure-focus"', self.html)
        self.assertIn('id="a11y-structure-focus"', self.html)
        self.assertIn("editorTargetCount", self.js)
        self.assertIn("structureTargetAttributes", self.js)
        self.assertIn("data-issue-ids", self.js)
        self.assertIn('? "Blocked"', self.js)

    def test_accessibility_workshop_exposes_draft_status_and_structure_edits(self):
        self.assertIn("Draft passed preflight", self.js)
        self.assertIn('id="a11y-figure-tag-list"', self.html)
        self.assertIn('id="a11y-table-list"', self.html)
        self.assertIn("renderStructureEditor", self.js)
        self.assertIn("handleStructureEditorAction", self.js)
        self.assertIn("set_figure_alt", self.js)
        self.assertIn("set_scope", self.js)
        self.assertIn("tag_annotation", self.js)
        self.assertIn("tooltipSourceLabel", self.js)
        self.assertIn("Existing PDF /TU", self.js)

    def test_accessibility_reading_order_has_a_dedicated_editor(self):
        self.assertIn('id="a11y-order-list"', self.html)
        self.assertIn("renderAccessibilityOrderList", self.js)
        self.assertIn('const factor = direction === "rtl" ? -1 : 1;', self.js)
        self.assertIn('a11yOrderList.addEventListener("click"', self.js)
        self.assertIn('a11yOrderList.addEventListener("drop"', self.js)

    def test_accessibility_headings_have_visual_review_and_optional_ai(self):
        self.assertIn('id="a11y-structure-preview"', self.html)
        self.assertIn('id="a11y-headings-approve-all"', self.html)
        self.assertIn('id="a11y-headings-reject-all"', self.html)
        self.assertIn('id="a11y-ai-headings"', self.html)
        self.assertIn("renderStructurePreview", self.js)
        self.assertIn("structurePreviewGeneration", self.js)
        self.assertIn("setHeadingDecision", self.js)
        self.assertIn("/pdf-labeler/api/accessibility-ai-headings", self.js)
        self.assertIn("heading_decisions: decisions", self.js)

    def test_accessibility_review_guards_dynamic_structure_controls(self):
        self.assertIn("if (!input)", self.js)
        self.assertIn("The table cell control is no longer available", self.js)
        self.assertIn("The table header control is no longer available", self.js)
        self.assertIn("const statusRank", self.js)

    def test_accessibility_tooltips_keep_neighbor_label_text(self):
        self.assertIn("allowNeighborFieldText", self.js)
        self.assertIn("{ allowNeighborFieldText: true }", self.js)

    def test_accessibility_remediation_avoids_duplicate_inspection(self):
        self.assertIn(
            "from .pdf_accessibility import PDFAccessibilityError", self.api
        )
        remediation = self.api[
            self.api.index("def pdf_labeler_accessibility_remediate") : self.api.index(
                'f"{LABELER_BASE_PATH}/pdf-labeler/api/accessibility-ai-tooltips"'
            )
        ]
        self.assertNotIn("inspect_pdf_accessibility", remediation)
        self.assertNotIn('"inspection": inspection', remediation)

    def test_accessibility_auto_fix_is_draft_only_and_explicitly_uses_ai(self):
        self.assertIn('id="a11y-auto-fix"', self.html)
        self.assertIn("Auto-fix draft (uses AI)", self.html)
        self.assertIn("function runAccessibilityAutoFix", self.js)
        self.assertIn("await applyAiTooltipDraft()", self.js)
        self.assertIn("await applyAiHeadingDraft()", self.js)
        self.assertIn("mark_as_tagged: false", self.js)
        self.assertIn("Auto-fix never enables it", self.html)
        self.assertIn("Only after checking all fixes", self.js)
        self.assertIn("function draftMissingDocumentLanguage", self.js)
        self.assertIn("document.documentElement.lang", self.js)
        self.assertIn("function remainingAccessibilityIssues", self.js)
        self.assertIn("checks still need attention", self.js)
        self.assertIn("fonts still need manual resolution", self.js)
        self.assertIn("Unicode mappings still need glyph review", self.js)
        self.assertIn("clientSettings.quiet", self.js)
        self.assertIn("Draft passed preflight", self.js)

    def test_panel_prose_lives_behind_dismissible_bootstrap_popovers(self):
        """Explanation stays available without spending vertical space on it."""
        # Bootstrap comes from docassemble's own static files, not a CDN.
        self.assertIn(
            '<script src="/static/bootstrap/js/bootstrap.bundle.min.js">', self.html
        )
        self.assertNotIn("cdnjs.cloudflare.com/ajax/libs/bootstrap", self.html)
        self.assertIn("function initAccessibilityHelpPopovers", self.js)
        self.assertIn("bootstrap.Popover.getOrCreateInstance", self.js)
        self.assertIn(".a11y-help {", self.css)
        # If that bundle ever fails to load, the text is still reachable.
        self.assertIn("element.dataset.bsContent", self.js)
        # Focus trigger is what makes a popover dismiss on the next click.
        self.assertIn('data-bs-trigger="focus"', self.html)
        self.assertGreaterEqual(self.html.count('class="a11y-help"'), 10)
        # Every help button is reachable by name and names its own popover.
        for chunk in self.html.split('class="a11y-help"')[1:]:
            attrs = chunk[: chunk.index(">")]
            self.assertIn("data-bs-title=", attrs)
            self.assertIn("data-bs-content=", attrs)
            self.assertIn("aria-label=", attrs)
        # The prose itself is retained, just not printed on the panel.
        self.assertNotIn(
            '<div class="alert alert-warning small">A tag tree must describe',
            self.html,
        )
        self.assertIn("A tag tree must describe the real document", self.html)
        self.assertIn("Auto-fix never enables it", self.html)

    def test_the_tag_tree_preview_collapses_repeated_siblings(self):
        """A form has dozens of identical /Form nodes; listing each says nothing."""
        self.assertIn("function renderAccessibilityTagStructure", self.js)
        self.assertIn("nodes, max depth ", self.js)
        self.assertIn('run + "  \\u00d7" + String(count)', self.js)
        self.assertIn('id="a11y-tag-structure-details"', self.html)

    def test_the_findings_rail_collapses_and_can_be_brought_back(self):
        self.assertIn('id="a11y-toggle-report"', self.html)
        self.assertIn('id="a11y-report-body"', self.html)
        self.assertIn("function setAccessibilityReportCollapsed", self.js)
        self.assertIn("is-report-collapsed", self.js)
        self.assertIn(".a11y-workshop-layout.is-report-collapsed", self.css)
        # Collapsed it still shows how many findings are waiting.
        self.assertIn('id="a11y-report-count"', self.html)
        self.assertIn("function renderAccessibilityReportCount", self.js)

    def test_manual_tagging_can_clear_the_untagged_content_finding(self):
        """Auto-fix always artifacted leftover content; the button did not."""
        manual = self.js[
            self.js.index('runAccessibilityRemediation("draft_structure"') :
        ][:600]
        self.assertIn("mark_untagged_as_artifacts: true", manual)
        self.assertIn(
            "content_decisions: accessibilityContentDecisionPayload()", manual
        )

    def test_the_h1_is_a_rules_based_title_source_with_no_ai_involved(self):
        """A document's own heading needs no model to be usable as its title."""
        self.assertIn('id="a11y-title-from-heading"', self.html)
        self.assertIn("no AI involved", self.html)
        self.assertIn("function documentHeadingTitle", self.js)
        self.assertIn("function applyDocumentTitleFromHeading", self.js)
        self.assertIn("function looksLikeFilenameTitle", self.js)
        self.assertIn("Use the first heading:", self.js)
        self.assertIn("Title already matches the first heading", self.js)
        # Auto-fix takes the deterministic route before the AI ever sees it.
        self.assertIn(
            "summary.draftedTitle = draftFilenameLikeDocumentTitle()", self.js
        )
        self.assertIn("document title drafted from the heading", self.js)
        # An approved H1 outranks a heuristic one, and a rejected heading is skipped.
        self.assertIn(
            'decision.status === "approved" && decision.tag === "H1"', self.js
        )
        self.assertIn('decision.status !== "rejected"', self.js)

    def test_ai_review_context_keeps_the_headings_it_has_already_seen(self):
        """Tagging rewrites content streams, so a re-inspection can lose them."""
        self.assertIn("knownHeadings", self.js)
        self.assertIn("state.accessibility.knownHeadings =", self.js)
        self.assertIn("documentLanguage:", self.js)
        self.assertIn("def _title_from_text_sample", self.api_accessibility)

    def test_ai_findings_pane_has_room_to_read_and_act_on_a_finding(self):
        """Open one finding and still see the next summary lines."""
        self.assertIn("#a11y-ai-review-findings {", self.css)
        self.assertIn("resize: vertical", self.css)
        self.assertIn("min-height: 8rem", self.css)
        # The working area keeps a floor of its own, so dragging the pane
        # taller can never leave nothing to work in.
        self.assertIn("min-height: 12rem", self.css)
        self.assertIn(".a11y-workshop-shell > .a11y-ai-review", self.css)
        # The standing explanation gives way to the findings themselves.
        self.assertIn("a11y-ai-review-intro", self.html)
        self.assertIn("a11y-ai-review-intro", self.js)

    def test_auto_fix_summary_is_collapsible_dismissible_and_height_capped(self):
        """It sits above the workspace, so it must not eat a laptop screen."""
        self.assertIn("data-auto-fix-dismiss", self.js)
        self.assertIn("data-auto-fix-toggle", self.js)
        self.assertIn("function hideAccessibilityAutoFixStatus", self.js)
        self.assertIn("Show details", self.js)
        self.assertIn("Hide details", self.js)
        self.assertIn(".a11y-auto-fix-status", self.css)
        self.assertIn("max-height: min(12rem, 24vh)", self.css)
        # The working area is the only part of the shell allowed to absorb
        # leftover height, so banners cannot squeeze the panels away.
        self.assertIn(".a11y-workshop-shell > *:not(.a11y-workshop-layout)", self.css)
        self.assertIn("@media (max-height: 820px)", self.css)

    def test_accessibility_workshop_steps_are_tabs_not_one_long_column(self):
        self.assertIn('id="a11y-panel-nav"', self.html)
        self.assertIn('role="tablist"', self.html)
        self.assertIn('data-panel-tab="metadata"', self.html)
        self.assertIn('data-panel-tab="fonts"', self.html)
        self.assertIn("function setActiveAccessibilityPanel", self.js)
        self.assertIn("function renderAccessibilityPanelBadges", self.js)
        self.assertIn('aria-selected="true"', self.css)
        # Every panel is a tab panel, and every tab names an existing panel.
        for panel in (
            "metadata",
            "field_tooltips",
            "reading_order",
            "draft_structure",
            "figures",
            "structure",
            "fonts",
        ):
            self.assertIn(f'data-panel-tab="{panel}"', self.html)
            self.assertIn(f'id="a11y-panel-{panel}" class="a11y-panel"', self.html)

    def test_ai_review_findings_keep_their_place_and_show_an_affordance(self):
        self.assertIn("expandedIndexes", self.js)
        self.assertIn("a11yAiReviewFindings.scrollTop = previousScrollTop", self.js)
        self.assertIn("a11y-ai-review-chevron", self.js)
        self.assertIn(".a11y-ai-review-chevron", self.css)
        self.assertIn("function rerenderPreservingPlace", self.js)

    def test_export_only_reasserts_an_existing_pdfua_declaration(self):
        """A tagged PDF without a PDF/UA identifier must not be downgraded."""
        self.assertNotIn("marked: !!state.accessibility.marked", self.js)
        self.assertIn("if (state.accessibility.marked) {", self.js)
        self.assertIn("payload.marked = true;", self.js)

    def test_export_only_artifacts_untagged_content_for_drafted_structure(self):
        self.assertIn(
            "mark_untagged_as_artifacts: !!state.accessibility.structureDrafted",
            self.js,
        )
        self.assertIn("state.accessibility.structureDrafted = true;", self.js)
        self.assertNotIn("mark_untagged_as_artifacts=True", self.api)

    def test_pdf_output_names_only_replace_a_trailing_extension(self):
        self.assertIn("def _suffixed_pdf_name", self.api)
        self.assertNotIn('.replace(".pdf"', self.api)

    def test_accessibility_workshop_exposes_completion_and_export_actions(self):
        self.assertIn('id="a11y-certify-accessible"', self.html)
        self.assertIn('id="a11y-export"', self.html)
        self.assertIn("setAccessibilityDeclaration", self.js)
        self.assertIn("a11yStatusLegend.innerHTML", self.js)
        self.assertIn("closeAccessibilityWorkshop", self.js)
        self.assertIn("showPdfWorkspace();", self.js)

    def test_accessibility_workshop_has_reviewable_ai_final_check(self):
        self.assertIn('id="a11y-ai-review"', self.html)
        self.assertIn('id="a11y-ai-review-findings"', self.html)
        self.assertIn("function runAiAccessibilityReview", self.js)
        self.assertIn("applyDrafts: true", self.js)
        self.assertIn("data-ai-review-action", self.js)
        self.assertIn("Apply the AI suggestion", self.js)
        self.assertIn("unresolvedAiAccessibilityFindings", self.js)
        self.assertIn('issue.status !== "pass"', self.js)
        self.assertIn('<details class="a11y-ai-review-finding', self.js)
        # Each action says what it leaves behind, and no two of them read the
        # same way. "Keep" only ever appears where there is a suggestion to
        # decline; a finding with no automatic fix is acknowledged instead.
        self.assertIn("Keep the current value", self.js)
        self.assertIn("Undo: put the old value back", self.js)
        self.assertIn("Mark as reviewed", self.js)
        self.assertNotIn("Keep as-is", self.js)
        self.assertIn("function aiReviewCurrentValue", self.js)
        self.assertIn("AI suggests", self.js)
        self.assertIn(".a11y-ai-review-values", self.css)
        self.assertIn("ignoreAiAccessibilityFinding", self.js)
        self.assertIn("overflow-y: auto", self.css)
        self.assertIn('id="a11y-ai-review-toggle"', self.html)
        self.assertIn("setAiReviewExpanded", self.js)
        self.assertIn("Open related controls", self.js)
        self.assertIn("/pdf-labeler/api/accessibility-ai-review", self.js)
        self.assertIn("def pdf_labeler_accessibility_ai_review", self.api)

    def test_account_menu_uses_server_menu_items_and_is_rightmost(self):
        self.assertIn("data.data.menu_items", self.js)
        self.assertIn("state.auth.menuItems", self.js)
        self.assertGreater(
            self.html.index('id="auth-controls"'),
            self.html.index('id="save-playground-btn"'),
        )

    def test_new_text_fields_only_auto_size_name_and_address_like_names(self):
        self.assertIn("function looksLikeSingleLineAutoSizeField", self.js)
        self.assertRegex(
            self.js,
            r"autoSize:\s*type === [\"']text[\"']\s*&&\s*"
            r"looksLikeSingleLineAutoSizeField\(",
        )

    def test_auto_size_preview_starts_from_field_height(self):
        self.assertIn("pxSize = maxPxSize;", self.js)
        self.assertNotIn("pxSize = Math.min(pxSize, maxPxSize);", self.js)

    def test_pdf_field_default_is_ten_point_helvetica(self):
        self.assertRegex(
            self.js,
            r"HARD_DEFAULTS\s*=\s*\{\s*font:\s*[\"']Helvetica[\"'],"
            r"\s*fontSize:\s*10",
        )


class TestLabelerTemplateRendering(unittest.TestCase):
    """Verify that the template-reading helpers still work after refactoring."""

    def test_docx_template_is_valid_html(self):
        html = _read_package_file("data", "templates", "docx_labeler.html")
        self.assertTrue(html.strip().startswith("<!DOCTYPE html>"))
        self.assertIn("<head>", html)
        self.assertIn("</head>", html)
        self.assertIn("<body", html)
        self.assertIn("</body>", html)

    def test_pdf_template_is_valid_html(self):
        html = _read_package_file("data", "templates", "pdf_labeler.html")
        self.assertTrue(html.strip().startswith("<!DOCTYPE html>"))
        self.assertIn("<head>", html)
        self.assertIn("</head>", html)
        self.assertIn("<body", html)
        self.assertIn("</body>", html)

    def test_docx_bootstrap_json_injection(self):
        """Simulate the server-side bootstrap JSON injection for docx."""
        html = _read_package_file("data", "templates", "docx_labeler.html")
        rendered = html.replace(
            "__LABELER_BOOTSTRAP_JSON__",
            '{"apiBasePath":"/al","initialPlaygroundSource":{"project":"demo-project","filename":"test.docx"}}',
        )
        self.assertNotIn("__LABELER_BOOTSTRAP_JSON__", rendered)
        self.assertIn(
            '{"apiBasePath":"/al","initialPlaygroundSource":{"project":"demo-project","filename":"test.docx"}}',
            rendered,
        )

    def test_pdf_bootstrap_json_injection(self):
        """Simulate the server-side bootstrap JSON injection."""
        html = _read_package_file("data", "templates", "pdf_labeler.html")
        rendered = html.replace(
            "__LABELER_BOOTSTRAP_JSON__",
            '{"apiBasePath":"/al","branding":{}}',
        )
        self.assertNotIn("__LABELER_BOOTSTRAP_JSON__", rendered)
        self.assertIn('{"apiBasePath":"/al","branding":{}}', rendered)

    def test_docx_html_does_not_duplicate_js_content(self):
        """The HTML should not contain content from the static JS file."""
        html = _read_package_file("data", "templates", "docx_labeler.html")
        # These are distinctive markers from the JS that should NOT be in HTML
        self.assertNotIn("AL_VARIABLE_TREE", html)
        self.assertNotIn("PERSON_ATTRIBUTES", html)
        self.assertNotIn("fetchAuthStatus", html)

    def test_pdf_html_does_not_duplicate_js_content(self):
        """The HTML should not contain content from the static JS file."""
        html = _read_package_file("data", "templates", "pdf_labeler.html")
        self.assertNotIn("FIELD_TYPES", html)
        self.assertNotIn("renderFieldsOnPages", html)
        self.assertNotIn("fetchAuthStatus", html)


class TestStaticJsFilesExist(unittest.TestCase):
    """Verify that the static JS files are properly packaged."""

    def test_docx_labeler_js_exists(self):
        ref = (
            importlib.resources.files("docassemble.ALDashboard")
            / "data"
            / "static"
            / "docx_labeler.js"
        )
        with importlib.resources.as_file(ref) as path:
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 0)

    def test_pdf_labeler_js_exists(self):
        ref = (
            importlib.resources.files("docassemble.ALDashboard")
            / "data"
            / "static"
            / "pdf_labeler.js"
        )
        with importlib.resources.as_file(ref) as path:
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 0)

    def test_docx_labeler_css_still_exists(self):
        ref = (
            importlib.resources.files("docassemble.ALDashboard")
            / "data"
            / "static"
            / "docx_labeler.css"
        )
        with importlib.resources.as_file(ref) as path:
            self.assertTrue(path.exists())

    def test_pdf_labeler_css_still_exists(self):
        ref = (
            importlib.resources.files("docassemble.ALDashboard")
            / "data"
            / "static"
            / "pdf_labeler.css"
        )
        with importlib.resources.as_file(ref) as path:
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()

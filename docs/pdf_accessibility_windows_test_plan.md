# PDF accessibility workshop: Windows assistive-technology test plan

## Purpose

Validate the exported PDF as a person using assistive technology experiences it. Passing veraPDF is necessary but is not sufficient: this plan checks meaning, navigation, reading order, and visual fidelity.

## Test environment

- Windows 11 with current updates.
- NVDA current stable release. Repeat the smoke test with JAWS when available.
- Free Adobe Acrobat Reader as the primary PDF reader; Microsoft Edge as a secondary compatibility check.
- veraPDF 1.28.1 with the PDF/UA-1 profile, matching ALActions PR 95.
- Browser developer-network logging when testing the workshop's AI controls.

Record application, version, screen reader, voice/language, PDF SHA-256, and workshop export settings in every result.

## Fixture matrix

Use at least these documents from the Massachusetts corpus:

1. `affidavit_of_indigency_supplement.pdf`: many form controls and an initially missing structure tree.
2. `civil_docketing_statement.pdf`: unembedded fonts and missing Unicode maps.
3. `complaint_209A_labeled_1.pdf`: figure, tables, and a pre-existing structure tree.
4. One PDF containing links or non-widget annotations.
5. A synthetic one-page form with two visual rows of three fields each, including duplicate appearances of one logical field.
6. Synthetic Arabic (right-to-left) and Japanese (vertical-column) ordering examples.

Keep the source PDF, exported PDF, workshop report JSON, veraPDF XML, screenshots, and speech/focus log together.

## Automated preflight

For every source/export pair:

1. Run `qpdf --check` and fail on any hard error.
2. Run veraPDF PDF/UA-1 and retain every rule/count, including warnings and suppressed strict-mode findings.
3. Compare page count, media/crop boxes, field count, complete field names, field types, option/export values, and annotation count.
4. Render every page to PNG at 150 DPI. Pixel-compare source and export for metadata, tag, tooltip, tab-order, and ToUnicode-only operations; those operations should have no visible differences.
5. Extract text with `pdftotext -layout`. Flag missing, added, reordered, or replacement characters.
6. Confirm each font reported as newly embedded has the exact expected PostScript name, compatible widths, and license permission. A suggested substitute must remain unresolved until a human explicitly replaces it.

## Screen-reader procedure

Start NVDA before opening the PDF. Disable any reader feature that invents labels from visual context so the test measures the PDF itself.

### Document entry

1. Open the export in Acrobat Reader.
2. Capture the first 30 seconds of speech.
3. Confirm the document title—not only the filename—is announced and the correct language/voice is selected.
4. Confirm there is no warning that the document is empty, untagged, or unreadable.

### Reading and headings

1. Read continuously from the start through each page.
2. Compare the utterance sequence with the visible document, line by line.
3. Open NVDA Elements List (`NVDA+F7`) and inspect headings and links.
4. Confirm heading levels form a sensible hierarchy; font size alone is not acceptance evidence.
5. Confirm headers, footers, page decorations, and repeated rules are either announced intentionally or skipped as artifacts.

### Forms and keyboard order

1. Enter focus/forms mode and press Tab through every interactive control.
2. Log control index, page, complete PDF field name, announced name, role, state/value, and bounding box.
3. Confirm the sequence matches the workshop's reviewed order for left-to-right, right-to-left, or vertical-column mode.
4. Confirm every control has a concise, contextual name. Reject generic names such as “field,” raw variable syntax, or labels that omit a row/party distinction.
5. Exercise text fields, multiline fields, checkboxes, radios, dropdowns, list boxes, and signatures. Confirm state and option changes are announced.
6. Shift+Tab through the full document and confirm the reverse order is exact.

### Figures, tables, links, and annotations

1. Navigate by graphic. Confirm meaningful figures announce reviewed alternative text and decorative images are silent.
2. Navigate tables by row and column. Confirm row/column counts, header associations, scopes, and cell order.
3. Navigate links and annotations. Confirm each announces its purpose and activation target.
4. Treat AI-drafted alternative text as a failure until a human verifies it against the rendered page.

### Unicode

1. Read text containing punctuation, currency, section symbols, accented names, and any non-Latin scripts.
2. Copy those spans to Notepad and compare Unicode code points with the expected text.
3. Search for the same text in Acrobat Reader. Reading, copying, and searching must all agree.

## Workshop behavior checks

1. Opening or refreshing Accessibility Workshop must make no LLM/network-model request.
2. Each heuristic button must name its rule, describe its assumption, and leave the result editable.
3. The AI button must be visually identified as optional, require a separate activation, and return drafts without auto-exporting.
4. Cancel an AI request and confirm all manual edits remain.
5. Confirm MarkInfo can be turned on or off, with a warning before turning it on when semantic tags have not been reviewed.
6. Confirm an existing tag tree is never replaced without explicit confirmation.
7. Confirm unmatched fonts are reported with suggestions, are not silently substituted, and direct the volunteer to ask an administrator to install the exact font using the Dashboard font manager.

## Acceptance criteria

- No qpdf hard errors and no unintended visual differences.
- No loss or rewriting of opaque field names.
- Every focusable control has an accurate accessible name and reviewed order.
- Continuous reading order matches human reading order on every page.
- Headings, tables, figures, links, and artifacts convey the intended semantics.
- Search/copy/speech preserve expected Unicode.
- Remaining veraPDF failures are shown in the workshop report and explicitly documented; the UI never labels a draft as compliant.
- The same core task is usable with keyboard only at 200% browser zoom.

## Result format for an automated Windows runner

Return one JSON record per PDF with `source_sha256`, `export_sha256`, tool versions, veraPDF rules, qpdf result, visual-diff pages, text-diff summary, ordered focus events, ordered speech events, semantic-navigation results, Unicode probes, unresolved findings, and artifact paths. Include a final `pass`, `fail`, or `needs_human_review`; never infer `pass` solely from veraPDF.

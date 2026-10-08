# ALDashboard Agent Notes

## PDF Labeler

The browser editor is served at `/al/pdf-labeler`. Its main implementation is:

- `docassemble/ALDashboard/api_labelers.py`: Flask endpoints.
- `docassemble/ALDashboard/pdf_export_utils.py`: conversion and field-name rules.
- `docassemble/ALDashboard/data/templates/pdf_labeler.html`: page and modal markup.
- `docassemble/ALDashboard/data/static/pdf_labeler.js`: editor state and interactions.
- `docassemble/ALDashboard/data/static/pdf_labeler.css`: labeler-specific styles.

Keep the HTML template free of duplicated JavaScript. The labeler loads the
packaged ES module from `data/static/pdf_labeler.js`.

### Field-name contract

PDF field names are opaque strings. Do not normalize, collapse underscores,
renumber suffixes, or otherwise rewrite a unique field name during save or
export.

Deduplication means only this: no two fields in one output PDF may have exactly
the same complete name. Keep the first occurrence and rename only later exact
duplicates by appending an unused `__N` suffix.

Names that already end in `__1`, `__2`, or any other digits after a double
underscore are valid independent names. The digits do not need to be
sequential. Reserve every original field name before generating a suffix so a
generated name never displaces a later unique original name. Example:

`["name", "name", "name__1"]` becomes
`["name", "name__2", "name__1"]`.

Whenever save or export changes names to enforce uniqueness, show an
occurrence-level summary to the user with each old and new name.

The executable contract and focused tests belong in
`pdf_export_utils.py` and `test/test_pdf_export_utils.py`. Keep the browser
implementation in `buildExportNameMap()` behaviorally identical.

### Attachment blocks

The Utilities modal generates a complete docassemble PDF `attachment:` block
from the active PDF. It includes `name`, `filename`, `pdf template file`, and a
list-form `fields` section. Match Weaver's quoted field-label style exactly:

`- "users1_name__1": ${ users[0] }`

Preserve the raw PDF field name on the left and use AssemblyLine display
expressions on the right. Repeated-appearance suffixes are ignored only when
deriving the expression. Do not remove that suffix from the actual PDF field
name.

### Verification

Run the focused Python tests for export and attachment mapping, then the static
labeler extraction tests. When changing browser behavior, also run a JavaScript
syntax check.

## PDF accessibility workshop

A full-screen review mode of the PDF labeler, opened by the "Accessibility
workshop" button. Implementation:

- `docassemble/ALDashboard/pdf_accessibility.py`: detection and repairs.
- `docassemble/ALDashboard/pdf_field_labels.py`: accessible-name review for
  form controls (instruction-style names, placeholders, answer boxes grouped
  with their question). Inspection returns it as `field_labels`; the browser
  uses those suggestions and has no labeling heuristics of its own.
- `docassemble/ALDashboard/api_labelers.py`: `/pdf-labeler/api/accessibility-*`
  endpoints. They are stateless: each repair receives the working PDF and
  returns the repaired one.
- `docassemble/ALDashboard/data/static/pdf_accessibility_workshop.js`: the
  workshop module, imported by `pdf_labeler.js`.
- `docassemble/ALDashboard/data/static/pdf_accessibility_workshop.css`: styles,
  all scoped under `.aw`.
- The workshop's markup shell is `#a11y-workshop` in `pdf_labeler.html`,
  outside `#app` so the editor can be made inert while it is open.

### Contract

- Three steps: find problems (with the repairs that need no judgment applied
  automatically), review eight reader-facing tasks, then test and export.
- Machine checks, human review and external testing stay separate evidence.
  There is no combined score. A task with nothing to review is "not
  applicable", never "reviewed".
- Drafts (from nearby text, the layout or AI) never count until a person
  confirms them. AI calls happen only on an explicit click.
- Never add the PDF/UA declaration automatically; only the explicit
  declaration control in Test & export may set it.
- Reading order, headings and image decisions are applied by rebuilding the
  draft tag tree from all decisions (`create_draft_structure_tree` with
  `overwrite`), then re-applying what lives on tag-tree elements (tab order,
  `/ActualText` corrections). Keep `rebuildSteps()` and
  `afterStructureSteps()` in step when adding a decision.
- An earlier tag tree the PDF arrived with is kept until the person chooses to
  replace it under Headings & tags.
- Existing tags can be edited directly under Headings & tags via `edit_tag`.
  These edits preserve the imported hierarchy and content references and must
  not call the draft-tree rebuild. Text roles may change to other text roles;
  specialized table, list and form roles stay intact. The preview links tags to
  their actual marked-content/annotation references; never locate them by text
  matching. Keep one selected-tag editor, with advanced properties collapsed.
- Merging belongs under Reading order, not a duplicate control in Headings &
  tags. Ctrl/Cmd-click and list checkboxes select adjacent items. Native merges
  preserve original elements as inline Spans in one text group, retaining
  ParentTree/IDTree references and per-run properties; never rebuild the tree
  for a native merge. Draft merges still apply on confirming the page.
- Internal field names never change. Only announced names (`/TU`) do.
- A name is the information, not an instruction: "County", not "Type name of
  county"; the screen reader already announces the role. Each box in a row of
  answers is named by its question and its answer.

### Verification

Run `test/test_pdf_accessibility*.py` (including `_api`, `_image_decisions`
and `_redraft`) and `test/test_pdf_field_labels.py`, `npm run check`, `npm run test:a11y-workshop`, and
`npm run test:pdf-labeler-focus` (it stubs the workshop import).

## Court form shapes

The Interview Intake Document Generator can draft a court filing instead of an
intake summary. Implementation:

- `docassemble/ALDashboard/court_form_profiles.py`: loading, `extends:`
  merging, Word style application, `.docx` fragment resolution and splicing.
- `docassemble/ALDashboard/court_form_generator.py`: the four shapes
  (`court_form`, `motion`, `affidavit`, `letter`) and the block renderer.
- `docassemble/ALDashboard/data/sources/court_form_profiles/*.yml`: one file
  per court.
- `docassemble/ALDashboard/data/templates/court_forms/<profile>/<section>.docx`:
  optional Word-authored override for a single section.

`shape="intake"` is the default of `generate_variable_report()` and must stay
that way: the ALWeaver's `variable_report.py` calls it without a shape.

### Where a court's layout belongs

No caption, footer, font or party label goes in Python. A new court is a new
YAML file and nothing else; if drafting a court needs a code change, the block
vocabulary is missing something and that is what should grow.

### The two kinds of placeholder

Profile text mixes two kinds of `{{ ... }}` and they are not interchangeable:

- Draft-time keys (`document_title`, `docket_label`, `form_code`,
  `form_revision`, `court_rule_citation`, `labels.*`) are substituted while
  drafting and must not survive into the output.
- Everything else — `{{ trial_court }}`, `{{ docket_number }}`,
  `{{ users[0].signature }}` — passes through untouched as the profile's
  contract with the interview.

Fields discovered in the interview are separate again and are always wrapped in
`showifdef()`. Do not "unify" these three; the tests assert each one.

### Verification

Run `test/test_court_form_generator.py`. It generates every shape against every
shipped profile, asserts each caption carries that jurisdiction's boilerplate
and docket label, and checks that a `.docx` fragment replaces only its own
section while the profile's Word styles survive.

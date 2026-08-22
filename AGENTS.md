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

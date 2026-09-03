# Court form profiles

One YAML file per court. Each describes the boilerplate that court fixes —
the caption, the running footer, the signature block — and the Word styles the
document should use. The middle of the form is generated from the interview's
own questions, so only the parts the court dictates live here.

Add a court by copying the closest existing file and editing it. Profiles are
found by filename, so `data/sources/court_form_profiles/nh_circuit.yml` becomes
the profile id `nh_circuit` and appears in the generator's dropdown with no code
change. Servers that would rather not fork this package can point the
`court form profiles` key in the docassemble configuration at their own
directory; profiles found there win over the packaged ones of the same name.

## What a profile contains

| Key | What it does |
| --- | --- |
| `extends` | Inherit from another profile. `styles`, `labels`, `body` and `page` merge key by key; a section such as `caption` is replaced outright. |
| `page` | Margins, in inches. |
| `styles` | The Word styles to define. Font, size, bold, alignment, line spacing, space before and after, indents. |
| `labels` | What this court calls things: `docket`, `plaintiff`, `defendant`. |
| `caption`, `header`, `footer`, `letterhead`, `signature`, `certificate_of_service`, `jurat` | Ordered blocks that make up each fixed section. |
| `body` | Which style fills which role, plus the shape-specific sentences (`motion_intro`, `motion_prayer`, `affidavit_intro`, `affidavit_closing`). |

## Blocks

A section is a list of blocks:

```yaml
caption:
  - type: paragraph
    style: CourtCaptionHeading
    text: "COMMONWEALTH OF MASSACHUSETTS"
  - type: table
    widths: [3.4, 3.1]        # inches
    borders: none             # none | box | grid | bottom
    rows:
      - cells: ["{{ users }}", "{{ docket_label }} {{ docket_number }}"]
      - cells: ["v.", "{{ document_title }}"]
        style: CourtCaptionText
  - type: rule                # a horizontal line
  - type: spacer              # an empty paragraph
```

`page_break` is also available. A `paragraph` may set `page_numbers: true` to
append a live "Page X of Y" field, with `page_numbers_align` to place it.

## Placeholders

Two kinds of `{{ ... }}` appear in a profile, and they are treated differently.

**Filled in while drafting**, because the drafter already knows them:
`document_title`, `docket_label`, `court_name`, `jurisdiction`, `form_code`,
`form_revision`, `court_rule_citation`, and `labels.<name>` for anything in the
`labels:` map. These do not survive into the generated template.

**Left alone**, because they are the interview's job to answer: everything
else, including `{{ trial_court }}`, `{{ docket_number }}`, `{{ county }}`,
`{{ users }}` and `{{ users[0].signature }}`. Write these the way you would in
any docassemble DOCX template. They are the profile's contract with the
interview: an interview drafted against this profile should define them.

Fields discovered in the interview are handled separately and are always
wrapped in `showifdef()`, because whether any one of them is defined at assembly
time is exactly what the generator cannot know.

## Changing a font

Edit the `styles:` map. Everything the generator writes is tagged with one of
these styles, so a court that prefers Arial needs one line changed, not a sweep
through the layout:

```yaml
styles:
  CourtBodyText:
    font: Arial
    size: 11
```

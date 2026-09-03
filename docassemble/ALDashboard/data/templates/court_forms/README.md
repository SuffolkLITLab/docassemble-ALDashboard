# Word-authored overrides for court form sections

A court form drafted by the Interview Intake Document Generator gets its fixed
sections — caption, running header and footer, signature block, certificate of
service, jurat — from the jurisdiction profile in
`data/sources/court_form_profiles/`. Those profiles are YAML, which is easy to
diff and easy to edit in the Playground.

Some layouts are easier to draw than to describe. When that is the case, put a
Word document here instead and it will be used verbatim in place of the
profile's version of that section:

```
data/templates/court_forms/
  <profile id>/
    caption.docx
    header.docx
    footer.docx
    letterhead.docx
    signature.docx
    certificate_of_service.docx
    jurat.docx
```

`<profile id>` is the profile's filename without its extension — for example
`ma_trial_court`, `mi_scao`, `dmass_federal`.

## How the override behaves

* **One section at a time.** Dropping in `caption.docx` replaces only the
  caption. Every other section still comes from the profile, so a court that
  just wants its own caption does not have to redraw its footer.
* **Copied as written.** Everything in the fragment's body is copied in,
  including its tables, tabs and images. Its own page setup is not: page size
  and margins stay with the profile, so a fragment cannot accidentally start a
  new section in the middle of the form.
* **Styles come along.** Any style the fragment defines that the generated
  document does not already have is copied over, so a caption you formatted in
  Word keeps its fonts.
* **Jinja passes through.** Write `{{ docket_number }}`, `{{ trial_court }}` or
  `{{ users[0].signature }}` in the fragment exactly as you would in any
  docassemble DOCX template. The generator does not rewrite them.

The generator reports where each section came from — `yaml` for the profile,
`docx` for an override — on the results screen and in the `sections` field of
the API response, which is the fastest way to confirm your fragment was picked
up.

## Fonts

Prefer changing a font in the profile's `styles:` map over formatting runs
directly in a fragment. The profile's styles are written into the generated
document as real Word styles, so one edit repoints the whole document; direct
formatting has to be redone every time the layout changes.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const lib = require("pdf-lib");
const moduleUrl = pathToFileURL(
  path.join(__dirname, "../data/static/pdf_page_assembly.js"),
);

async function fixture() {
  const doc = await lib.PDFDocument.create();
  const pages = Array.from({ length: 3 }, () => doc.addPage([400, 500]));
  const form = doc.getForm();
  const repeated = form.createTextField("name");
  repeated.setText("Preserved value");
  repeated.addToPage(pages[0], { x: 10, y: 10, width: 100, height: 20 });
  repeated.addToPage(pages[1], { x: 10, y: 10, width: 100, height: 20 });
  const independent = form.createTextField("name__1");
  independent.setText("Independent name");
  independent.addToPage(pages[2], { x: 10, y: 10, width: 100, height: 20 });
  return new Uint8Array(await doc.save());
}
const descriptor = (sourceId, sourcePageIndex) => ({
  sourceId,
  sourcePageIndex,
});

test("page splitting preserves shared field identities, values and default fonts", async () => {
  const { assemblePdfPages } = await import(moduleUrl);
  const bytes = await fixture();
  const sources = { active: { bytes } };
  const result = await assemblePdfPages(
    lib,
    [descriptor("active", 1), descriptor("active", 0)],
    sources,
    true,
  );
  const doc = await lib.PDFDocument.load(result.bytes);
  assert.deepEqual(
    doc
      .getForm()
      .getFields()
      .map((f) => f.getName()),
    ["name"],
  );
  const field = doc.getForm().getTextField("name");
  assert.equal(field.getText(), "Preserved value");
  assert.equal(field.acroField.getWidgets().length, 2);
  assert.deepEqual(result.renames, []);
  const split = await assemblePdfPages(
    lib,
    [descriptor("active", 1)],
    sources,
    true,
  );
  const splitDoc = await lib.PDFDocument.load(split.bytes);
  const splitField = splitDoc.getForm().getTextField("name");
  assert.equal(splitField.acroField.getWidgets().length, 1);
  assert.equal(splitDoc.getForm().getFields().length, 1);
  const da = splitField.acroField.getDefaultAppearance();
  const font = /\/(\S+)\s+[\d.]+\s+Tf/.exec(da)[1];
  const resources = splitDoc.catalog
    .lookup(lib.PDFName.of("AcroForm"), lib.PDFDict)
    .lookup(lib.PDFName.of("DR"), lib.PDFDict)
    .lookup(lib.PDFName.of("Font"), lib.PDFDict);
  assert.ok(
    resources.has(lib.PDFName.of(font)),
    "inherited default appearance resolves its font",
  );
});

test("inserted PDFs reserve all complete original names and report exact duplicate renames", async () => {
  const { assemblePdfPages } = await import(moduleUrl);
  const bytes = await fixture();
  const result = await assemblePdfPages(
    lib,
    [descriptor("active", 0), descriptor("insert", 0), descriptor("active", 2)],
    { active: { bytes }, insert: { bytes } },
    true,
  );
  const doc = await lib.PDFDocument.load(result.bytes);
  assert.deepEqual(
    doc
      .getForm()
      .getFields()
      .map((f) => f.getName()),
    ["name", "name__2", "name__1"],
  );
  assert.deepEqual(result.renames, [
    { index: 1, old_name: "name", new_name: "name__2" },
  ]);
  assert.equal(
    doc.getForm().getTextField("name__2").getText(),
    "Preserved value",
  );
  assert.equal(
    doc.getForm().getTextField("name__1").getText(),
    "Independent name",
  );
  const duplicate = await assemblePdfPages(
    lib,
    [descriptor("active", 0), descriptor("active", 0)],
    { active: { bytes } },
    true,
  );
  const duplicateDoc = await lib.PDFDocument.load(duplicate.bytes);
  assert.equal(duplicateDoc.getPageCount(), 2);
  assert.notEqual(duplicateDoc.getPage(0).ref, duplicateDoc.getPage(1).ref);
  assert.deepEqual(
    duplicateDoc
      .getForm()
      .getFields()
      .map((f) => f.getName()),
    ["name", "name__1"],
  );
});

test("ordinary page edits retain unnamed controls and leave rename acknowledgment to export", async () => {
  const { assemblePdfPages } = await import(moduleUrl);
  const doc = await lib.PDFDocument.create();
  const page = doc.addPage();
  const button = doc.getForm().createButton("temporary");
  button.addToPage("Print", page, { x: 10, y: 10, width: 100, height: 30 });
  button.acroField.dict.set(lib.PDFName.of("T"), lib.PDFString.of(""));
  const bytes = new Uint8Array(await doc.save());
  const result = await assemblePdfPages(
    lib,
    [descriptor("active", 0), descriptor("insert", 0)],
    { active: { bytes }, insert: { bytes } },
  );
  const output = await lib.PDFDocument.load(result.bytes);
  assert.deepEqual(
    output
      .getForm()
      .getFields()
      .map((f) => f.getName()),
    ["", ""],
  );
  assert.deepEqual(result.renames, []);
});

test("form-level default appearance and alignment remain inherited by copied fields", async () => {
  const { assemblePdfPages } = await import(moduleUrl);
  const bytes = await fixture();
  const source = await lib.PDFDocument.load(bytes);
  const field = source.getForm().getTextField("name");
  const form = source.catalog.lookup(lib.PDFName.of("AcroForm"), lib.PDFDict);
  form.set(
    lib.PDFName.of("DA"),
    lib.PDFString.of(field.acroField.getDefaultAppearance()),
  );
  form.set(lib.PDFName.of("Q"), lib.PDFNumber.of(2));
  field.acroField.dict.delete(lib.PDFName.of("DA"));
  const result = await assemblePdfPages(
    lib,
    [descriptor("active", 1)],
    { active: { bytes: await source.save({ updateFieldAppearances: false }) } },
    true,
  );
  const output = await lib.PDFDocument.load(result.bytes);
  const copied = output.getForm().getTextField("name");
  assert.match(copied.acroField.getDefaultAppearance(), /\/pm1_Helvetica/);
  assert.equal(copied.getAlignment(), 2);
  assert.equal(
    copied.acroField.getWidgets()[0].dict.get(lib.PDFName.of("P")).toString(),
    output.getPage(0).ref.toString(),
  );
});

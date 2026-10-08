/* Copy pages with their live form fields. pdf-lib's copyPages() copies the
   annotations, but does not register their field trees in the new catalog. */
export async function assemblePdfPages(
  lib,
  pages,
  sources,
  deduplicate = false,
) {
  const {
    PDFDocument,
    PDFName,
    PDFDict,
    PDFArray,
    PDFString,
    PDFHexString,
    PDFObjectCopier,
    PDFStream,
  } = lib;
  const name = PDFName.of;
  const output = await PDFDocument.create();
  const documents = new Map();
  const copied = new Map();
  const fontMaps = new Map();
  const defaultAppearances = new Map();
  const defaultAlignments = new Map();
  const fonts = output.context.obj({});
  const stringValue = (value) =>
    value instanceof PDFString || value instanceof PDFHexString
      ? value.decodeText()
      : "";
  for (const sourceId of new Set(pages.map((page) => page.sourceId))) {
    const source = sources[sourceId];
    if (!source || !source.bytes)
      throw new Error("A source PDF is missing for one or more pages.");
    const document = await PDFDocument.load(source.bytes.slice());
    documents.set(sourceId, document);
    const indexes = [
      ...new Set(
        pages
          .filter((page) => page.sourceId === sourceId)
          .map((page) => page.sourcePageIndex),
      ),
    ];
    const copies = await output.copyPages(document, indexes);
    copied.set(
      sourceId,
      new Map(indexes.map((index, position) => [index, copies[position]])),
    );
    const form = document.catalog.lookupMaybe(name("AcroForm"), PDFDict);
    const sourceFonts = form
      ?.lookupMaybe(name("DR"), PDFDict)
      ?.lookupMaybe(name("Font"), PDFDict);
    const copier = PDFObjectCopier.for(document.context, output.context);
    const mapping = new Map();
    const addFonts = (dictionary) => {
      if (!dictionary) return;
      for (const [key, value] of dictionary.entries()) {
        if (mapping.has(key.toString())) continue;
        const newName = name(`pm${documents.size}_${key.decodeText()}`);
        fonts.set(newName, copier.copy(value));
        mapping.set(key.toString(), newName.toString());
      }
    };
    addFonts(sourceFonts);
    // Some producers store default-appearance fonts only in the widget's
    // appearance stream. Register those as form resources as well.
    for (const index of indexes) {
      const annotations = document
        .getPage(index)
        .node.lookupMaybe(name("Annots"), PDFArray);
      for (const reference of annotations?.asArray() || []) {
        const annotation = document.context.lookup(reference);
        if (
          !(annotation instanceof PDFDict) ||
          annotation.get(name("Subtype")) !== name("Widget")
        )
          continue;
        const appearance = annotation
          .lookupMaybe(name("AP"), PDFDict)
          ?.lookup(name("N"));
        const streams =
          appearance instanceof PDFDict
            ? appearance.values().map((value) => document.context.lookup(value))
            : [appearance];
        for (const stream of streams) {
          if (!(stream instanceof PDFStream)) continue;
          addFonts(
            stream.dict
              .lookupMaybe(name("Resources"), PDFDict)
              ?.lookupMaybe(name("Font"), PDFDict),
          );
        }
      }
    }
    fontMaps.set(sourceId, mapping);
    defaultAppearances.set(
      sourceId,
      form ? stringValue(form.lookup(name("DA"))) : "",
    );
    defaultAlignments.set(sourceId, form?.get(name("Q")));
  }
  const widgets = new Set();
  const roots = new Map();
  const usedPages = new Set();
  for (const descriptor of pages) {
    const key = `${descriptor.sourceId}:${descriptor.sourcePageIndex}`;
    let page = copied.get(descriptor.sourceId).get(descriptor.sourcePageIndex);
    if (usedPages.has(key))
      [page] = await output.copyPages(documents.get(descriptor.sourceId), [
        descriptor.sourcePageIndex,
      ]);
    usedPages.add(key);
    output.addPage(page);
    const annotations = page.node.lookupMaybe(name("Annots"), PDFArray);
    if (!annotations) continue;
    for (let index = 0; index < annotations.size(); index++) {
      const reference = annotations.get(index);
      const widget = output.context.lookup(reference);
      if (
        !(widget instanceof PDFDict) ||
        widget.get(name("Subtype")) !== name("Widget")
      )
        continue;
      widgets.add(widget);
      widget.set(name("P"), page.ref);
      let field = widget,
        fieldRef = reference,
        appearance = "";
      const ancestors = new Set();
      while (field && !ancestors.has(field)) {
        ancestors.add(field);
        const ownAppearance = stringValue(field.lookup(name("DA")));
        appearance ||= ownAppearance;
        if (ownAppearance)
          field.set(
            name("DA"),
            PDFString.of(
              ownAppearance.replace(
                /\/[^\s]+/g,
                (token) =>
                  fontMaps.get(descriptor.sourceId).get(token) || token,
              ),
            ),
          );
        const parentRef = field.get(name("Parent"));
        const parent = output.context.lookup(parentRef);
        if (!(parent instanceof PDFDict)) break;
        field = parent;
        fieldRef = parentRef;
      }
      roots.set(field, fieldRef);
      appearance ||= defaultAppearances.get(descriptor.sourceId);
      if (appearance) {
        const remapped = PDFString.of(
          appearance.replace(
            /\/[^\s]+/g,
            (token) => fontMaps.get(descriptor.sourceId).get(token) || token,
          ),
        );
        widget.set(name("DA"), remapped);
        if (!field.has(name("DA"))) field.set(name("DA"), remapped);
      }
      const alignment = defaultAlignments.get(descriptor.sourceId);
      if (alignment && !field.has(name("Q"))) field.set(name("Q"), alignment);
    }
  }
  function prune(field, ancestors = new Set()) {
    if (ancestors.has(field)) return false;
    const children = field.lookupMaybe(name("Kids"), PDFArray);
    if (!children) return widgets.has(field);
    const path = new Set([...ancestors, field]);
    const retained = children.asArray().filter((reference) => {
      const child = output.context.lookup(reference);
      return child instanceof PDFDict && prune(child, path);
    });
    field.set(name("Kids"), output.context.obj(retained));
    return retained.length > 0;
  }
  const fields = [...roots.entries()].filter(([field]) => prune(field));
  const renames = [];
  if (fields.length) {
    const form = output.context.obj({
      Fields: fields.map(([, reference]) => reference),
      DR: { Font: fonts },
    });
    output.catalog.set(name("AcroForm"), output.context.register(form));
    if (deduplicate) {
      const terminal = [];
      function collect(field, prefix = "") {
        const partial = stringValue(field.lookup(name("T")));
        const complete = partial
          ? prefix
            ? `${prefix}.${partial}`
            : partial
          : prefix;
        const children =
          field
            .lookupMaybe(name("Kids"), PDFArray)
            ?.asArray()
            .map((ref) => output.context.lookup(ref))
            .filter(
              (child) => child instanceof PDFDict && child.has(name("T")),
            ) || [];
        if (children.length)
          children.forEach((child) => collect(child, complete));
        else if (partial) terminal.push({ field, partial, complete, prefix });
      }
      fields.forEach(([field]) => collect(field));
      const reserved = new Set(terminal.map((item) => item.complete));
      const used = new Set();
      terminal.forEach((item, index) => {
        if (used.has(item.complete)) {
          let suffix = 1;
          while (
            reserved.has(`${item.complete}__${suffix}`) ||
            used.has(`${item.complete}__${suffix}`)
          )
            suffix++;
          item.field.set(name("T"), PDFString.of(`${item.partial}__${suffix}`));
          renames.push({
            index,
            old_name: item.complete,
            new_name: `${item.complete}__${suffix}`,
          });
          used.add(`${item.complete}__${suffix}`);
        } else used.add(item.complete);
      });
    }
  }
  return { bytes: new Uint8Array(await output.save()), renames };
}

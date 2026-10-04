"""Explicit author choices for table grouping, decorative controls and pages."""

from typing import Any, Dict, List, Mapping
import math


def _children(node: Any) -> List[Any]:
    import pikepdf

    kids = node.get("/K")
    return (
        list(kids)
        if isinstance(kids, pikepdf.Array)
        else ([] if kids is None else [kids])
    )


def table_content_items(pdf: Any) -> List[Dict[str, Any]]:
    from .pdf_accessibility import _readback_sequence

    groups: Dict[str, Any] = {}
    for entry in _readback_sequence(pdf, keep_elements=True):
        if not entry.get("contentId") or entry.get("element") is None:
            continue
        group = groups.setdefault(
            str(entry["element"].objgen), {"element": entry["element"], "entries": []}
        )
        group["entries"].append(entry)

    def table_owner(element: Any) -> str:
        seen = set()
        while element is not None and element.objgen not in seen:
            seen.add(element.objgen)
            if str(element.get("/S", "")) == "/Table":
                return str(element.objgen)
            element = element.get("/P")
        return ""

    return [
        dict(
            tableOwner=table_owner(group["element"]),
            contentIds=[e["contentId"] for e in group["entries"]],
            pageIndex=group["entries"][0]["page"],
            text=" ".join(
                e.get("text")
                or e.get("drawn")
                or e.get("name")
                or "(unlabeled control)"
                for e in group["entries"]
            )[:300],
        )
        for group in groups.values()
        if len({e["page"] for e in group["entries"]}) == 1
    ]


def create_table(pdf: Any, operation: Mapping[str, Any]) -> None:
    """Group existing tagged content; never manufacture spoken table text."""
    import pikepdf
    from .pdf_accessibility import (
        PDFAccessibilityError,
        _readback_sequence,
        _document_structure_parent,
    )

    rows = operation.get("rows")
    table_id = str(operation.get("tableId") or "")
    if (
        not table_id
        or len(table_id) > 100
        or not isinstance(rows, list)
        or not 1 <= len(rows) <= 20
    ):
        raise PDFAccessibilityError("Provide a table identity and 1–20 rows.")
    width = len(rows[0]) if isinstance(rows[0], list) else 0
    if not 1 <= width <= 10 or any(
        not isinstance(row, list) or len(row) != width for row in rows
    ):
        raise PDFAccessibilityError("Every table row needs the same 1–10 cells.")
    sequence = _readback_sequence(pdf, keep_elements=True)
    by_id: Dict[str, List[Any]] = {}
    by_element: Dict[str, set[str]] = {}
    for entry in sequence:
        if entry.get("contentId") and entry.get("element") is not None:
            by_id.setdefault(entry["contentId"], []).append(entry)
            by_element.setdefault(str(entry["element"].objgen), set()).add(
                entry["contentId"]
            )
    selected: Dict[str, Any] = {}
    cell_nodes = []
    page_indexes = set()
    for row in rows:
        node_row = []
        for cell in row:
            if not isinstance(cell, dict) or cell.get("role", "TD") not in ("TH", "TD"):
                raise PDFAccessibilityError("Each cell must be a data cell or header.")
            if cell.get("role") == "TH" and cell.get("scope") not in (
                "Row",
                "Column",
                "Both",
            ):
                raise PDFAccessibilityError("Choose the direction of each header.")
            identities = cell.get("contentIds") or []
            if not isinstance(identities, list) or len(identities) > 200:
                raise PDFAccessibilityError("Choose existing content for each cell.")
            nodes: List[Any] = []
            for identity in identities:
                matches = by_id.get(str(identity), [])
                if len(matches) != 1:
                    raise PDFAccessibilityError(
                        "Table content is missing or ambiguous. Review the selection again."
                    )
                entry = matches[0]
                node = entry["element"]
                ancestor = node
                ancestors = set()
                while ancestor is not None and ancestor.objgen not in ancestors:
                    ancestors.add(ancestor.objgen)
                    if (
                        str(ancestor.get("/S", "")) == "/Table"
                        and str(ancestor.get("/DAWorkshopTable", "")) != table_id
                    ):
                        raise PDFAccessibilityError(
                            "This content already belongs to a table. Repair that table instead."
                        )
                    ancestor = ancestor.get("/P")
                key = str(node.objgen)
                if key in selected and selected[key] is not cell:
                    raise PDFAccessibilityError(
                        "The same passage cannot belong to two cells."
                    )
                if not by_element[key].issubset(set(identities)):
                    raise PDFAccessibilityError(
                        "Keep a whole passage together in one cell."
                    )
                selected[key] = cell
                if not any(existing.objgen == node.objgen for existing in nodes):
                    nodes.append(node)
                page_indexes.add(entry["page"])
            node_row.append((cell, nodes))
        cell_nodes.append(node_row)
    if not selected or len(page_indexes) != 1 or None in page_indexes:
        raise PDFAccessibilityError(
            "Select content from one page for this basic table."
        )
    root = pdf.Root.StructTreeRoot
    # An existing table with this identity is replaced atomically. Otherwise,
    # insert at the earliest selected branch, keeping surrounding reading order.
    document = _document_structure_parent(root)
    anchor_parent, anchor_index = document, len(_children(document))
    existing: Any = None

    def locate(node: Any) -> None:
        nonlocal anchor_parent, anchor_index, existing
        kids = _children(node)
        for index, child in enumerate(kids):
            if not hasattr(child, "get") or not child.get("/S"):
                continue
            if str(child.get("/DAWorkshopTable", "")) == table_id:
                existing = child
                anchor_parent, anchor_index = node, index
                return
            locate(child)

    locate(root)
    if existing is None:
        first = next(
            entry["element"]
            for entry in sequence
            if str(entry["element"].objgen) in selected
        )
        parent = first.get("/P")
        # Do not insert a new Table inside a cell or another Table.
        branch = first
        while (
            parent is not None
            and str(parent.get("/S", "")) not in ("/Part", "/Sect", "/Document")
            and parent.objgen != root.objgen
        ):
            branch = parent
            parent = parent.get("/P")
        anchor_parent = parent if parent is not None else document
        anchor_index = next(
            (
                i
                for i, child in enumerate(_children(anchor_parent))
                if getattr(child, "objgen", None) == branch.objgen
            ),
            len(_children(anchor_parent)),
        )
    parents: Dict[str, Any] = {}
    for entry in sequence:
        node = entry.get("element")
        if node is not None and str(node.objgen) in selected:
            parent = node.get("/P")
            if parent is not None:
                parents[str(parent.objgen)] = parent
    for parent in parents.values():
        parent.K = pikepdf.Array(
            [
                child
                for child in _children(parent)
                if str(getattr(child, "objgen", "")) not in selected
            ]
        )
    if existing is not None:
        anchor_parent.K = pikepdf.Array(
            [
                child
                for child in _children(anchor_parent)
                if getattr(child, "objgen", None) != existing.objgen
            ]
        )
    page = pdf.pages[next(iter(page_indexes))].obj
    table = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.StructElem,
            S=pikepdf.Name.Table,
            P=anchor_parent,
            Pg=page,
            DAWorkshopTable=pikepdf.String(table_id),
        )
    )
    new_rows = []
    for node_row in cell_nodes:
        tr = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.StructElem, S=pikepdf.Name.TR, P=table, Pg=page
            )
        )
        cells = []
        for decision, nodes in node_row:
            cell = pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.StructElem,
                    S=pikepdf.Name("/" + decision.get("role", "TD")),
                    P=tr,
                    Pg=page,
                    K=pikepdf.Array(nodes),
                )
            )
            if decision.get("role") == "TH":
                cell.A = pikepdf.Dictionary(
                    O=pikepdf.Name.Table, Scope=pikepdf.Name("/" + decision["scope"])
                )
            for node in nodes:
                node.P = cell
            cells.append(cell)
        tr.K = pikepdf.Array(cells)
        new_rows.append(tr)
    table.K = pikepdf.Array(new_rows)
    siblings = _children(anchor_parent)
    siblings.insert(min(anchor_index, len(siblings)), table)
    anchor_parent.K = pikepdf.Array(siblings)


def disable_decorative_button(
    pdf: Any, operation: Mapping[str, Any], number_entries: Dict[int, Any]
) -> None:
    """Paint the normal appearance as an artifact and remove interactivity."""
    import pikepdf
    from .pdf_accessibility import PDFAccessibilityError

    widgets = [
        a
        for page in pdf.pages
        for a in page.get("/Annots", [])
        if a is not None and a.get("/Subtype") == pikepdf.Name.Widget
    ]
    page_index = int(operation.get("pageIndex", -1))
    index = int(operation.get("index", -1))
    if page_index < 0 or index < 0:
        raise PDFAccessibilityError("Choose a button on the page.")
    try:
        page = pdf.pages[page_index]
        widget = page.Annots[index]
    except (IndexError, KeyError) as exc:
        raise PDFAccessibilityError(
            "The control list changed; refresh and retry."
        ) from exc
    field = widget
    seen = set()
    inherited: Dict[str, Any] = {}
    while field is not None and field.objgen not in seen:
        seen.add(field.objgen)
        for key in ("/FT", "/Ff"):
            inherited.setdefault(key, field.get(key)) if key in field else None
        field = field.get("/Parent")
    if (
        widget.get("/Subtype") != pikepdf.Name.Widget
        or inherited.get("/FT") != pikepdf.Name.Btn
        or not int(inherited.get("/Ff") or 0) & 65536
    ):
        raise PDFAccessibilityError("Only push buttons can be disabled as decoration.")
    if int(widget.get("/F", 0)) & (1 | 2 | 16 | 32) or widget.get("/OC") is not None:
        raise PDFAccessibilityError(
            "This button has conditional visibility or rotation; its appearance cannot safely be flattened here."
        )
    appearance = widget.get("/AP", {}).get("/N")
    if not isinstance(appearance, pikepdf.Stream):
        raise PDFAccessibilityError("This button has no normal appearance to preserve.")
    rect = [float(v) for v in widget.get("/Rect", [])]
    box = [float(v) for v in appearance.get("/BBox", [])]
    matrix = [float(v) for v in appearance.get("/Matrix", [1, 0, 0, 1, 0, 0])]
    if (
        len(rect) != 4
        or len(box) != 4
        or len(matrix) != 6
        or not all(math.isfinite(v) for v in rect + box + matrix)
    ):
        raise PDFAccessibilityError("The button appearance has invalid geometry.")
    a, b, c, d, e, f = matrix
    points = [
        (a * x + c * y + e, b * x + d * y + f)
        for x in (box[0], box[2])
        for y in (box[1], box[3])
    ]
    left = min(x for x, y in points)
    bottom = min(y for x, y in points)
    width = max(x for x, y in points) - left
    height = max(y for x, y in points) - bottom
    if width <= 0 or height <= 0:
        raise PDFAccessibilityError("The button appearance has no visible area.")
    sx = (rect[2] - rect[0]) / width
    sy = (rect[3] - rect[1]) / height
    resources = page.get("/Resources") or pikepdf.Dictionary()
    page.Resources = pikepdf.Dictionary(dict(resources.items()))
    xobjects = pikepdf.Dictionary(dict(page.Resources.get("/XObject", {}).items()))
    page.Resources.XObject = xobjects
    resource = "/DADecoration"
    suffix = 1
    while resource in xobjects:
        resource = f"/DADecoration{suffix}"
        suffix += 1
    # Clone the appearance: another live button may share the same stream.
    decoration = pdf.make_stream(appearance.read_bytes())
    for key, value in appearance.items():
        if key not in ("/Length", "/Filter", "/DecodeParms"):
            decoration[key] = value
    decoration.DAWorkshopDecorative = True
    xobjects[resource] = decoration
    content = f"\n/Artifact BMC\nq {sx:.9f} 0 0 {sy:.9f} {rect[0]-sx*left:.9f} {rect[1]-sy*bottom:.9f} cm {resource} Do Q\nEMC\n".encode()
    contents = page.get("/Contents")
    original = (
        list(contents)
        if isinstance(contents, pikepdf.Array)
        else ([contents] if contents is not None else [])
    )
    page.Contents = pikepdf.Array(
        [pdf.make_stream(b"q\n"), *original, pdf.make_stream(b"\nQ\n" + content)]
    )
    page.Annots = pikepdf.Array([a for a in page.Annots if a.objgen != widget.objgen])

    def prune(node: Any) -> bool:
        kids = _children(node)
        retained = []
        for child in kids:
            if (
                hasattr(child, "get")
                and child.get("/Type") == pikepdf.Name.OBJR
                and getattr(child.get("/Obj"), "objgen", None) == widget.objgen
            ):
                continue
            if hasattr(child, "get") and child.get("/S") and not prune(child):
                continue
            retained.append(child)
        if kids:
            node.K = pikepdf.Array(retained)
        return bool(retained) or not kids or str(node.get("/S", "")) in ("/TD", "/TH")

    prune(pdf.Root.StructTreeRoot)
    if widget.get("/StructParent") is not None:
        number_entries.pop(int(widget.StructParent), None)
    retained_widgets = {w.objgen for w in widgets if w.objgen != widget.objgen}

    def keep_field(node: Any) -> bool:
        kids = node.get("/Kids")
        if kids is None:
            return node.objgen in retained_widgets
        node.Kids = pikepdf.Array([child for child in kids if keep_field(child)])
        return bool(node.Kids)

    form = pdf.Root.get("/AcroForm")
    if form is not None:
        form.Fields = pikepdf.Array(
            [field for field in form.get("/Fields", []) if keep_field(field)]
        )


def preserve_page_tags(
    input_path: str, output_path: str, indexes: List[int], deduplicate: bool = False
) -> Dict[str, Any]:
    """Rearrange original page objects and prune references to omitted pages."""
    import pikepdf
    from .pdf_accessibility import (
        PDFAccessibilityError,
        _number_tree_entries,
        _set_pdfua_identifier,
    )

    with pikepdf.open(input_path) as pdf:
        if (
            not indexes
            or any(type(i) is not int or i < 0 or i >= len(pdf.pages) for i in indexes)
            or len(set(indexes)) != len(indexes)
        ):
            raise PDFAccessibilityError(
                "Keeping tags requires distinct pages from the original PDF."
            )
        pages = [p.obj for p in pdf.pages]
        selected = [pages[i] for i in indexes]
        keep = {p.objgen for p in selected}
        root = pdf.Root.get("/StructTreeRoot")
        if root is None:
            raise PDFAccessibilityError("This PDF has no existing tags to keep.")
        order: Dict[Any, int] = {p.objgen: i for i, p in enumerate(selected)}

        def prune(node: Any, inherited: Any = None) -> bool:
            if not hasattr(node, "get"):
                return inherited is None or inherited.objgen in keep
            pg = node.get("/Pg") or inherited
            if node.get("/S"):
                old = _children(node)
                children = [child for child in old if prune(child, pg)]
                if old and not children:
                    return False
                if not old and pg is not None and pg.objgen not in keep:
                    return False
                if old:
                    node.K = pikepdf.Array(children)
                if node.get("/Pg") is not None and node.Pg.objgen not in keep:
                    del node["/Pg"]
                return True
            if node.get("/Type") in (pikepdf.Name.MCR, pikepdf.Name.OBJR):
                return pg is None or pg.objgen in keep
            return True

        root.K = pikepdf.Array([child for child in _children(root) if prune(child)])

        # Reorder page containers while keeping the cell/heading relationships
        # inside each page. Spanning structures are preserved for author review.
        def first_page(node: Any) -> int:
            if not hasattr(node, "get"):
                return len(indexes)
            own = order.get(getattr(node.get("/Pg"), "objgen", None), len(indexes))
            return min([own] + [first_page(child) for child in _children(node)])

        def reorder(node: Any) -> None:
            if str(node.get("/S", "")) in ("", "/Document", "/Part", "/Sect"):
                children = _children(node)
                children.sort(key=first_page)
                node.K = pikepdf.Array(children)
                for child in children:
                    if hasattr(child, "get") and child.get("/S"):
                        reorder(child)

        reorder(root)
        keys = set()
        widgets = set()

        def resource_keys(resources: Any, seen: set) -> None:
            for form in resources.get("/XObject", {}).values():
                if not hasattr(form, "get") or form.objgen in seen:
                    continue
                seen.add(form.objgen)
                if form.get("/StructParents") is not None:
                    keys.add(int(form.StructParents))
                resource_keys(form.get("/Resources", {}), seen)

        for page in selected:
            # Materialize inherited page properties before flattening /Pages.
            parent: Any = page
            seen = set()
            while parent is not None and parent.objgen not in seen:
                seen.add(parent.objgen)
                for key in ("/MediaBox", "/CropBox", "/Resources", "/Rotate"):
                    if key not in page and key in parent:
                        page[key] = parent[key]
                parent = parent.get("/Parent")
            if page.get("/StructParents") is not None:
                keys.add(int(page.StructParents))
            resource_keys(page.get("/Resources", {}), set())
            for annotation in page.get("/Annots", []):
                if annotation is None:
                    continue
                if annotation.get("/StructParent") is not None:
                    keys.add(int(annotation.StructParent))
                if annotation.get("/Subtype") == pikepdf.Name.Widget:
                    widgets.add(annotation.objgen)
            page.Parent = pdf.Root.Pages
        entries = (
            _number_tree_entries(root.get("/ParentTree"))
            if root.get("/ParentTree")
            else {}
        )
        root.ParentTree = pdf.make_indirect(
            pikepdf.Dictionary(
                Nums=pikepdf.Array(
                    [
                        value
                        for key in sorted(keys)
                        if key in entries
                        for value in (key, entries[key])
                    ]
                )
            )
        )

        def keep_field(field: Any) -> bool:
            if field.get("/Kids") is None:
                return field.objgen in widgets
            field.Kids = pikepdf.Array(
                [child for child in field.Kids if keep_field(child)]
            )
            return bool(field.Kids)

        form = pdf.Root.get("/AcroForm")
        if form is not None:
            form.Fields = pikepdf.Array(
                [field for field in form.get("/Fields", []) if keep_field(field)]
            )
        renames: List[Dict[str, Any]] = []
        if deduplicate and form is not None:
            from .pdf_export_utils import deduplicate_pdf_field_names

            terminal = []

            def collect(field: Any, prefix: str = "") -> None:
                partial = str(field.get("/T", ""))
                complete = (
                    f"{prefix}.{partial}" if prefix and partial else partial or prefix
                )
                children = [child for child in field.get("/Kids", []) if "/T" in child]
                if children:
                    for child in children:
                        collect(child, complete)
                elif partial:
                    terminal.append((field, partial, complete))

            for field in form.get("/Fields", []):
                collect(field)
            names, renames = deduplicate_pdf_field_names(item[2] for item in terminal)
            for (field, partial, old), name in zip(terminal, names):
                if name != old:
                    field.T = pikepdf.String(partial + name[len(old) :])
        pdf.Root.Pages.Kids = pikepdf.Array(selected)
        pdf.Root.Pages.Count = len(selected)
        _set_pdfua_identifier(pdf, False)
        pdf.save(output_path)
    return dict(
        action="preserve_pages",
        pages=len(indexes),
        tags_preserved=True,
        renames=renames,
        review_required=True,
        warning="Existing tags were kept. Check reading order and any structures that span pages after editing.",
    )

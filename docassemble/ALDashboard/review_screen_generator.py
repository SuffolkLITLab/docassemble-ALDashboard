import re
from typing import Any, Dict, List, Tuple

from ruamel.yaml import YAML
from ruamel.yaml.compat import StringIO


def _load_yaml_documents(yaml_texts: List[str]) -> List[Dict[str, Any]]:
    yaml = YAML(typ="safe", pure=True)
    docs: List[Dict[str, Any]] = []
    for text in yaml_texts:
        for doc in yaml.load_all(text):
            if isinstance(doc, dict):
                docs.append(doc)
    return docs


def generate_review_screen_yaml(
    yaml_texts: List[str],
    *,
    build_revisit_blocks: bool = True,
    point_sections_to_review: bool = True,
) -> str:
    docs = _load_yaml_documents(yaml_texts)

    objects_temp: List[Dict[str, str]] = []
    attributes_list: Dict[str, List[Dict[str, Any]]] = {}
    questions_temp: List[Dict[str, Any]] = []
    sections_temp: List[str] = []

    for doc in docs:
        if not any(
            key in doc for key in ["fields", "question", "objects", "sections", "metadata"]
        ):
            continue
        if doc.get("generic object"):
            continue

        question = {"question": str(doc.get("question", "")).strip()}
        fields_temp: List[Dict[str, Any]] = []

        if "fields" in doc and isinstance(doc["fields"], list):
            for field in doc["fields"]:
                if not isinstance(field, dict) or not field:
                    continue
                if "code" in field:
                    try:
                        match = re.match(r"((\w+\[\d+\])|\w+)", str(field["code"]))
                        object_name = match[1] if match else None
                        if object_name is None or object_name == "x" or "[i]" in str(field["code"]):
                            continue
                    except Exception:
                        continue
                    if ".name_fields(" in str(field["code"]):
                        fields_temp.extend(
                            [
                                {"First": f"{object_name}.name.first"},
                                {"Middle": f"{object_name}.name.middle"},
                                {"Last": f"{object_name}.name.last"},
                            ]
                        )
                    elif ".address_fields(" in str(field["code"]):
                        fields_temp.extend(
                            [
                                {"Address": f"{object_name}.address.address"},
                                {"Apartment or Unit": f"{object_name}.address.unit"},
                                {"City": f"{object_name}.address.city"},
                                {"State": f"{object_name}.address.state"},
                                {"Zip": f"{object_name}.address.zip"},
                                {"Country": f"{object_name}.address.country"},
                            ]
                        )
                    elif ".gender_fields(" in str(field["code"]):
                        fields_temp.append({"Gender": f"{object_name}.gender"})
                    elif ".language_fields(" in str(field["code"]):
                        fields_temp.append({"Language": f"{object_name}.language"})
                else:
                    first_val = next(iter(field.values()))
                    if isinstance(first_val, str) and "[i]" in first_val:
                        obj_match = re.match(r"(\w+).*\[i.*", first_val)
                        object_name = obj_match[1] if obj_match else None
                        if not object_name:
                            continue
                        attributes_list.setdefault(object_name, []).append(field)
                    else:
                        fields_temp.append(field)
        elif "yesno" in doc:
            fields_temp.append({doc.get("question", ""): doc.get("yesno"), "datatype": "yesno"})
        elif "noyes" in doc:
            fields_temp.append({doc.get("question", ""): doc.get("noyes"), "datatype": "noyes"})
        elif "signature" in doc:
            fields_temp.append({doc.get("question", ""): doc.get("signature"), "datatype": "signature"})
        elif "field" in doc and ("choices" in doc or "buttons" in doc):
            fields_temp.append({doc.get("question", ""): doc.get("field"), "datatype": "radio"})
        elif "objects" in doc and isinstance(doc["objects"], list):
            objects_temp.extend(doc["objects"])
        elif "sections" in doc and isinstance(doc["sections"], list):
            sections_temp.extend([next(iter(sec.keys()), "") for sec in doc["sections"] if isinstance(sec, dict)])

        question["fields"] = fields_temp
        if fields_temp:
            questions_temp.append(question)

    objects = objects_temp
    questions = questions_temp
    section_events = sections_temp

    review_event_name = "review_form"
    review_fields_temp: List[Dict[str, str]] = []
    revisit_screens: List[Dict[str, str]] = []
    tables: List[Dict[str, Any]] = []
    sections: List[Dict[str, str]] = []

    if point_sections_to_review:
        for sec in section_events:
            sections.append({"event": sec, "code": review_event_name})

    if build_revisit_blocks:
        for obj in objects:
            obj_name = next(iter(obj.keys()), "")
            obj_type = next(iter(obj.values()), "")
            if not obj_name or not isinstance(obj_type, str):
                continue
            skippable_types = ["ALDocument.", "ALDocumentBundle.", "DAStaticFile.", "ALPeopleList."]
            if any(obj_type.startswith(val) for val in skippable_types):
                continue
            review_fields_temp.append(
                {
                    "Edit": f"{obj_name}.revisit",
                    "button": (
                        f"**{obj_name.replace('_', ' ').title()}**\\n\\n"
                        f"% for item in {obj_name}:\\n- ${{ item }}\\n% endfor"
                    ),
                }
            )
            revisit_screens.append(
                {
                    "id": f"revisit {obj_name}",
                    "continue button field": f"{obj_name}.revisit",
                    "question": f"Edit your answers about {obj_name.replace('_', ' ').title()}",
                    "subquestion": f"${{{obj_name}.table}}\\n\\n${{{obj_name}.add_action()}}",
                }
            )
            if obj_name in attributes_list:
                columns = []
                edits = []
                for attribute in attributes_list[obj_name]:
                    attr_key = next(iter(attribute.keys()))
                    attr_value = next(iter(attribute.values()))
                    if not isinstance(attr_value, str):
                        continue
                    attr_name = attr_value.split(".")[-1]
                    label = attr_name if attr_key == "no label" else attr_key
                    columns.append(
                        {
                            label: (
                                f"row_item.{attr_name} if hasattr(row_item, '{attr_name}') else ''"
                            )
                        }
                    )
                    edits.append(attr_name)
                tables.append(
                    {
                        "table": f"{obj_name}.table",
                        "rows": obj_name,
                        "columns": columns,
                        "edit": edits,
                    }
                )

    not_labels = {
        "label",
        "datatype",
        "default",
        "help",
        "min",
        "max",
        "maxlength",
        "minlength",
        "rows",
        "choices",
        "input type",
        "required",
        "hint",
        "code",
        "exclude",
        "none of the above",
        "shuffle",
        "show if",
        "hide if",
        "enable if",
        "disable if",
        "js show if",
        "js hide if",
        "js enable if",
        "js disable if",
        "disable others",
        "note",
        "html",
        "field",
        "field metadata",
        "accept",
        "validate",
        "address autocomplete",
    }

    for question in questions:
        fields = question.get("fields", [])
        if not fields:
            continue

        first_label_pair = next((pair for pair in fields[0].items() if pair[0] not in not_labels), None)
        if first_label_pair is None:
            first_label_pair = (fields[0].get("label", ""), fields[0].get("field", ""))

        review: Dict[str, str] = {"Edit": str(first_label_pair[1])}
        question_text = str(question.get("question", ""))
        if "\n" in question_text:
            review["button"] = f"<strong>\\n{question_text}\\n</strong>\\n\\n"
        else:
            review["button"] = f"**{question_text}**\\n\\n"

        for field in fields:
            label_pair = next((pair for pair in field.items() if pair[0] not in not_labels), None)
            if label_pair is None:
                label_pair = (field.get("label", ""), field.get("field", ""))

            label = label_pair[0]
            value_ref = label_pair[1]
            if not label:
                continue

            show_if = field.get("show if")
            if show_if:
                if isinstance(show_if, str):
                    review["button"] += f"% if showifdef('{show_if}'):\\n"
                elif isinstance(show_if, dict) and show_if.get("variable"):
                    var = show_if.get("variable")
                    val = show_if.get("is")
                    if val not in ["False", "True", "false", "true"]:
                        val = f'"{val}"'
                    review["button"] += f"% if showifdef('{var}') == {val}:\\n"

            if label != "no label":
                review["button"] += f"{label}: "

            datatype = field.get("datatype")
            if datatype in ["yesno", "yesnoradio", "yesnowide"]:
                review["button"] += f"${{ word(yesno({value_ref})) }}\\n"
            elif datatype == "currency":
                review["button"] += f"${{ currency(showifdef('{value_ref}')) }}\\n"
            else:
                review["button"] += f"${{ showifdef('{value_ref}') }}\\n"

            if show_if:
                review["button"] += "% endif\\n\\n"
            else:
                review["button"] += "\\n"

        review["button"] = review["button"].strip() + "\\n"
        review_fields_temp.append(review)

    review_yaml = (
        sections
        + [
            {
                "id": "review screen",
                "event": review_event_name,
                "question": "Review your answers",
                "review": review_fields_temp,
            }
        ]
        + revisit_screens
        + tables
    )

    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump_all(review_yaml, stream)
    return stream.getvalue()

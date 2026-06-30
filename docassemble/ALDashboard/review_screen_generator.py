import os
import re
from typing import Any, Dict, Iterable, List, Tuple

from ruamel.yaml import YAML
from ruamel.yaml.compat import StringIO
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, LiteralScalarString


def list_review_playground_projects() -> List[str]:
    from .interview_linter import list_playground_projects

    return list_playground_projects()


def list_review_playground_yaml_files(
    project: str = "default",
) -> List[Dict[str, str]]:
    from .yaml_formatter import list_formatter_playground_yaml_files

    return list_formatter_playground_yaml_files(project)


def _review_output_filename(filename: str) -> str:
    cleaned = str(filename or "").strip() or "review.yml"
    if (
        os.path.basename(cleaned) != cleaned
        or "/" in cleaned
        or "\\" in cleaned
        or cleaned in {".", ".."}
    ):
        raise ValueError("The output filename must not include a directory.")
    if not cleaned.lower().endswith((".yml", ".yaml")):
        cleaned += ".yml"
    return cleaned


def _get_review_playground_storage(project: str) -> Tuple[Any, str]:
    from .docassemble_compat import SavedFile, directory_for

    from .interview_linter import _resolve_current_user_id

    current_user_id = _resolve_current_user_id()
    if current_user_id is None:
        raise ValueError("Could not determine the current user for playground access.")

    playground_area = SavedFile(current_user_id, fix=True, section="playground")
    project_root = directory_for(playground_area, project)
    if not project_root or not os.path.isdir(project_root):
        raise ValueError("Could not locate the selected playground project.")
    return playground_area, os.path.realpath(project_root)


def save_review_screen_to_playground(
    generated_yaml: str,
    *,
    selected_playground_project: str,
    output_filename: str = "review.yml",
) -> Dict[str, Any]:
    project = str(selected_playground_project or "default")
    result: Dict[str, Any] = {
        "output_filename": str(output_filename or "review.yml"),
        "saved": False,
        "error": None,
    }
    try:
        result["output_filename"] = _review_output_filename(output_filename)
        playground_area, _ = _get_review_playground_storage(project)
        playground_area.write_content(
            str(generated_yaml or ""),
            filename=result["output_filename"],
            project=project,
            save=False,
        )
        playground_area.finalize()
        result["saved"] = True
    except Exception as err:
        result["error"] = str(err)
    return result


def generate_and_save_playground_review_screen(
    selected_filenames: Iterable[str],
    *,
    selected_playground_project: str,
    output_filename: str = "review.yml",
    save_to_playground: bool = True,
    save_playground_project: str = "",
    build_revisit_blocks: bool = True,
    point_sections_to_review: bool = True,
) -> Dict[str, Any]:
    project = str(selected_playground_project or "default")
    filenames = [str(filename) for filename in selected_filenames]
    result: Dict[str, Any] = {
        "selected_count": len(filenames),
        "output_filename": str(output_filename or "review.yml"),
        "generated_yaml": "",
        "saved": False,
        "save_requested": bool(save_to_playground),
        "save_error": None,
        "error": None,
    }

    try:
        if not filenames:
            raise ValueError("Select at least one YAML file.")

        allowed_filenames = {
            str(item.get("token"))
            for item in list_review_playground_yaml_files(project)
            if item.get("token")
        }
        if any(filename not in allowed_filenames for filename in filenames):
            raise ValueError(
                "A selected file is not in the selected playground project."
            )

        playground_area, project_root = _get_review_playground_storage(project)

        yaml_texts: List[str] = []
        for filename in filenames:
            source_path = os.path.realpath(os.path.join(project_root, filename))
            if not source_path.startswith(project_root + os.sep):
                raise ValueError(
                    "Refusing to read files outside the selected playground project."
                )
            with open(source_path, "r", encoding="utf-8") as source_file:
                yaml_texts.append(source_file.read())

        result["generated_yaml"] = generate_review_screen_yaml(
            yaml_texts,
            build_revisit_blocks=build_revisit_blocks,
            point_sections_to_review=point_sections_to_review,
        )
        if save_to_playground:
            save_result = save_review_screen_to_playground(
                result["generated_yaml"],
                selected_playground_project=save_playground_project or project,
                output_filename=output_filename,
            )
            result["output_filename"] = save_result["output_filename"]
            result["saved"] = save_result["saved"]
            result["save_error"] = save_result["error"]
    except Exception as err:
        result["error"] = str(err)

    return result


def _load_yaml_documents(yaml_texts: List[str]) -> List[Dict[str, Any]]:
    yaml = YAML(typ="safe", pure=True)
    docs: List[Dict[str, Any]] = []
    for text in yaml_texts:
        for doc in yaml.load_all(text):
            if isinstance(doc, dict):
                docs.append(doc)
    return docs


def normalize_objects_block(objects_block):
    if isinstance(objects_block, dict):
        return [{key: value} for key, value in objects_block.items()]
    if isinstance(objects_block, list):
        return [obj for obj in objects_block if isinstance(obj, dict)]
    return []


def is_list_object_type(obj_type: str) -> bool:
    class_reference = obj_type.strip().split(".using(", 1)[0]
    class_name = class_reference.rsplit(".", 1)[-1]
    return class_name.lower().endswith("list")


def _show_if_review_directive(show_if: Any) -> str:
    if isinstance(show_if, str):
        variable = show_if.strip()
        if variable:
            return f"% if showifdef({variable!r}):\n"
        return ""

    if not isinstance(show_if, dict):
        return ""

    variable = str(show_if.get("variable") or "").strip()
    if variable:
        expected = show_if.get("is")
        if isinstance(expected, str):
            normalized = expected.strip().lower()
            if normalized == "true":
                expected_literal = "True"
            elif normalized == "false":
                expected_literal = "False"
            elif normalized in {"null", "none"}:
                expected_literal = "None"
            else:
                expected_literal = repr(expected)
        else:
            expected_literal = repr(expected)
        return f"% if showifdef({variable!r}) == {expected_literal}:\n"

    code = show_if.get("code")
    if isinstance(code, str):
        expression = " ".join(
            line.strip() for line in code.splitlines() if line.strip()
        )
        if expression:
            return f"% if {expression}:\n"

    return ""


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
            key in doc
            for key in ["fields", "question", "objects", "sections", "metadata"]
        ):
            continue
        if doc.get("generic object"):
            continue

        question: Dict[str, Any] = {"question": str(doc.get("question", "")).strip()}
        fields_temp: List[Dict[str, Any]] = []

        if "fields" in doc and isinstance(doc["fields"], list):
            for field in doc["fields"]:
                if not isinstance(field, dict) or not field:
                    continue
                if "code" in field:
                    try:
                        match = re.match(r"((\w+\[\d+\])|\w+)", str(field["code"]))
                        object_name = match[1] if match else None
                        if (
                            object_name is None
                            or object_name == "x"
                            or "[i]" in str(field["code"])
                        ):
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
            fields_temp.append(
                {doc.get("question", ""): doc.get("yesno"), "datatype": "yesno"}
            )
        elif "noyes" in doc:
            fields_temp.append(
                {doc.get("question", ""): doc.get("noyes"), "datatype": "noyes"}
            )
        elif "signature" in doc:
            fields_temp.append(
                {doc.get("question", ""): doc.get("signature"), "datatype": "signature"}
            )
        elif "field" in doc and ("choices" in doc or "buttons" in doc):
            fields_temp.append(
                {doc.get("question", ""): doc.get("field"), "datatype": "radio"}
            )
        elif "objects" in doc:
            objects_temp.extend(normalize_objects_block(doc["objects"]))
        elif "sections" in doc and isinstance(doc["sections"], list):
            sections_temp.extend(
                [
                    next(iter(sec.keys()), "")
                    for sec in doc["sections"]
                    if isinstance(sec, dict)
                ]
            )

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
        seen_object_names = set()
        for obj in objects:
            if not isinstance(obj, dict):
                continue

            obj_name = next(iter(obj.keys()), "")
            obj_type = next(iter(obj.values()), "")

            if not obj_name or not isinstance(obj_type, str):
                continue
            if not is_list_object_type(obj_type):
                continue
            if any(character in obj_name for character in ".[]"):
                continue
            if obj_name in seen_object_names:
                continue
            seen_object_names.add(obj_name)
            review_fields_temp.append(
                {
                    "Edit": f"{obj_name}.revisit",
                    "button": LiteralScalarString(
                        f"**{obj_name.replace('_', ' ').title()}**\n\n"
                        f"% for item in {obj_name}:\n- ${{ item }}\n% endfor"
                    ),
                }
            )
            revisit_screens.append(
                {
                    "id": f"revisit {obj_name}",
                    "continue button field": f"{obj_name}.revisit",
                    "question": f"Edit your answers about {obj_name.replace('_', ' ').title()}",
                    "subquestion": LiteralScalarString(
                        f"${{{obj_name}.table}}\n\n${{{obj_name}.add_action()}}"
                    ),
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
                    label = DoubleQuotedScalarString(str(label))
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
        raw_fields = question.get("fields", [])
        if not isinstance(raw_fields, list):
            continue
        fields: List[Dict[str, Any]] = [
            field for field in raw_fields if isinstance(field, dict)
        ]
        if not fields:
            continue

        first_label_pair = next(
            (pair for pair in fields[0].items() if pair[0] not in not_labels), None
        )
        if first_label_pair is None:
            first_label_pair = (fields[0].get("label", ""), fields[0].get("field", ""))

        review: Dict[str, str] = {"Edit": str(first_label_pair[1])}
        question_text = str(question.get("question", ""))
        if "\n" in question_text:
            review["button"] = f"<strong>\n{question_text}\n</strong>\n\n"
        else:
            review["button"] = f"**{question_text}**\n\n"

        for field in fields:
            label_pair = next(
                (pair for pair in field.items() if pair[0] not in not_labels), None
            )
            if label_pair is None:
                label_pair = (field.get("label", ""), field.get("field", ""))

            label = label_pair[0]
            value_ref = label_pair[1]
            if not label:
                continue

            show_if_directive = _show_if_review_directive(field.get("show if"))
            if show_if_directive:
                review["button"] += show_if_directive

            if label != "no label":
                review["button"] += f"{label}: "

            datatype = field.get("datatype")
            if datatype in ["yesno", "yesnoradio", "yesnowide"]:
                review["button"] += f"${{ word(yesno({value_ref})) }}\n"
            elif datatype == "currency":
                review["button"] += f"${{ currency(showifdef('{value_ref}')) }}\n"
            else:
                review["button"] += f"${{ showifdef('{value_ref}') }}\n"

            if show_if_directive:
                review["button"] += "% endif\n\n"
            else:
                review["button"] += "\n"

        review["button"] = LiteralScalarString(review["button"].strip() + "\n")
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

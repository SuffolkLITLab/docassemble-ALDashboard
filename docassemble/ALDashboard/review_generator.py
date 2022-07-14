from typing import Any, Dict
import ruamel.yaml
import yaml
from docassemble.base.util import (
    DAFile,
    single_paragraph,
    noun_singular,
    noun_plural,
    ordinal,
)
from typing import List, Tuple, Dict
import re

# Monkeypatch yaml module so that text with line breaks are represented with a `|` rather than a giant quoted string
def str_presenter(dumper, data):
    if "\n" in data or "\r" in data:  # check for multiline string
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(str, str_presenter)
yaml.representer.SafeRepresenter.add_representer(str, str_presenter)

not_labels = [
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
    "field metadata",
    "accept",
    "validate",
    "address autocomplete",
]


def parse_yaml(yaml_file: DAFile):
    yaml_parsed = []
    for f in yaml_file:
        yaml_parsed.extend(list(yaml.safe_load_all(f.slurp())))

    return yaml_parsed


def dump_yaml(blocks: List[Dict]) -> str:
    return yaml.dump_all(blocks)


def get_question_review_blocks_and_tables(
    questions: List[Dict], number_of_expansions: int = 3, iterator="i"
) -> Tuple[List[Dict], List[Dict]]:
    """
    Returns a tuple of review blocks together with any tables that are needed to
    expand iterator variables. Should be provided a list of normalized questions
    that each have a `fields` block (e.g., not the special yesno: fieldname
    style block)
    """
    blocks = []
    tables = []
    for question in questions:
        if not question.get("fields"):
            continue
        if question.get("ITERATOR QUESTION PLACEHOLDER"):
            # Insert review blocks with the display table in context with the
            # list[0], list[1] special inline review blocks
            blocks.append(
                get_iterator_review_block(
                    question,
                    number_of_expansions=number_of_expansions,
                    iterator=iterator,
                )
            )
            tables.append(
                get_iterator_question_table(
                    question,
                    number_of_expansions=number_of_expansions,
                    iterator=iterator,
                )
            )
        else:
            fields = question["fields"]
            if not fields:
                continue
            # The first field on the screen will be the trigger for reviewing the screen
            first_field_name = field_and_label(fields[0])[0]

            review = {
                "Edit": first_field_name,
                # The first line in the review block will be the question text to provide context
                "button": f"**{ single_paragraph(question['question']) }**\n\n",
            }
            # Then list all fields in this review block, wrapped in `showifdef`
            for field in fields:
                field_name, field_text = field_and_label(field)
                if field_text == "no label":
                    if field.get("datatype") in ["yesno", "yesnoradio", "yesnowide"]:
                        review[
                            "button"
                        ] += f"${{ word(yesno(showifdef('{ field_name }', False))) }}\n\n"
                    elif field.get("datatype") in ["noyes", "noyesradio", "noyeswide"]:
                        review[
                            "button"
                        ] += f"${{ word(noyes(showifdef('{ field_name }', False))) }}\n\n"
                    else:
                        review["button"] += f"${{ showifdef('{ field_name}') }}\n\n"
                else:
                    if field.get("datatype") in ["yesno", "yesnoradio", "yesnowide"]:
                        review[
                            "button"
                        ] += f"{field_text or ''}: ${{ word(yesno(showifdef('{ field_name }', False))) }}\n\n"
                    elif field.get("datatype") in ["noyes", "noyesradio", "noyeswide"]:
                        review[
                            "button"
                        ] += f"{field_text or ''}: ${{ word(noyes(showifdef('{ field_name }', False))) }}\n\n"
                    else:
                        review[
                            "button"
                        ] += f"{field_text or ''}: ${{ showifdef('{ field_name }') }}\n\n"
                review["button"] = review["button"].strip() + "\n"

    return (blocks, tables)


def has_iterator(question: Dict, iterator: str = "i") -> bool:
    if f"[{iterator}]" in question["question"]:
        return True
    if any(
        f"[{iterator}]" in next(iter(field.values()))
        for field in question.get("fields", [{"": ""}])
    ):
        return True
    if any(f"[{iterator}]" in value for value in question.values()):
        return True
    return False


def uses_generic_object(question: Dict) -> bool:
    return bool(question.get("generic object"))


def get_questions(yaml_parsed: List[Any], objects: List[Dict[str, str]]) -> List[Dict]:
    """
    Return a normalized list of questions and fields, with special handling of
    the one-field variant of Docassemble questions which are converted into
    ordinary fields statements.
    """
    questions = []
    for doc in yaml_parsed:
        if not doc.get("question"):
            continue
        # First, expand generic objects
        if uses_generic_object(doc):
            to_process = expand_generic_question(doc, objects)
        else:
            to_process = [doc]
        for item in to_process:
            # Expand iterators and normalize after generic objects are expanded
            if has_iterator(item):
                expanded_questions = expand_iterator_question(item)
                questions.extend(
                    [normalize_question(question) for question in expanded_questions]
                )
                # Add a marker for where the review block for the overflow items should go
                questions.append(normalize_question(item, placeholder=True))
            else:
                questions.append(normalize_question(item))

    return questions


def normalize_question(doc: Dict, placeholder: bool = False) -> Dict:
    if doc.get("question"):
        question = {"question": doc["question"].strip(), "fields": []}
        if placeholder:
            question["ITERATOR QUESTION PLACEHOLDER"] = True
        if "yesno" in doc:
            question["fields"] = [
                {doc.get("question", ""): doc.get("yesno"), "datatype": "yesno"}
            ]
        elif "noyes" in doc:
            question["fields"] = [
                {doc.get("question", ""): doc.get("noyes"), "datatype": "noyes"}
            ]
        elif "signature" in doc:
            question["fields"] = [
                {doc.get("question", ""): doc.get("signature"), "datatype": "signature"}
            ]
        elif "field" in doc:
            if "choices" in doc or "buttons" in doc:
                question["fields"] = [
                    {doc.get("question", ""): doc.get("field"), "datatype": "radio"}
                ]
        else:
            question["fields"] = expand_fields(question.get("fields", []))
    return question


def expand_generic_question(
    question: Dict, objects: List[Dict[str, str]]
) -> List[Dict]:
    """
    Given a single question that uses the `x` generic object modifier,
    return a list of questions where the `x` is replaced with each object
    that matches the generic object modifier.
    """
    generic_object = question.get("generic object", "").strip()
    if not generic_object:
        return [question]
    expanded_questions: list = []
    x_regex = re.compile(r"(^x)|(^x\[\w+\])\.")  # for a field, matching x. or x[i|n].
    x_body_regex = re.compile(
        r"(\$\{\s*\b)(x)|(x\[\w+\])(\.)"
    )  # for simple mako statements in question body
    for obj in objects:
        obj_name = next(iter(obj.keys()), "UNKNOWN")
        if generic_object == next(iter(obj.values()), "").split(".")[0]:
            if question.get("fields"):
                expanded_questions.append(
                    {
                        "question": x_body_regex.sub(
                            r"\1" + obj_name + r"\2", question["question"]
                        ),
                        "fields": [
                            {key: x_regex.sub(obj_name, value) for key, value in field}
                            for field in question["fields"]
                        ],
                    }
                )
            else:
                expanded_questions.append(
                    {
                        key: x_regex.sub(obj_name, value)
                        for key, value in question.items()
                        if key != "question"
                    }.update(
                        {
                            "question": x_body_regex.sub(
                                r"\1" + obj_name + r"\2", question["question"]
                            )
                        }
                    )
                )

    return expanded_questions


def expand_iterator_question(
    question: Dict, number_of_expansions: int = 3, iterator: str = "i"
) -> List[Dict]:
    """
    Return a list of questions where an iterator like `i` is replaced with 0..number_of_expansions.
    This facilitates building a review screen that displays the direct value of the first few
    people in a list, e.g., rather than forcing the user to click a button to launch a table on
    a new screen.
    """
    expanded_questions = []

    for index in range(0, number_of_expansions):
        if question.get("fields"):
            expanded_questions.append(
                {
                    # Replace [i] and ordinal(i) with specific index
                    "question": question["question"]
                    .replace(f"[{iterator}]", f"[{index}]")
                    .replace(f"${{ ordinal({iterator}) }}", ordinal(index)),
                    "fields": [
                        {
                            next(iter(field.keys())): next(
                                iter(field.values())
                            ).replace(f"[{iterator}]", f"[{index}]")
                        }
                        for field in question["fields"]
                    ],
                }
            )
        else:
            for key, value in question.items():
                expanded_questions.append(
                    {
                        key: value.replace(f"[{iterator}]", f"[{index}]")
                        if isinstance(value, str)
                        else value
                    }
                )
    return expanded_questions


def get_iterator_review_block(
    question: Dict, number_of_expansions: int = 3, iterator: str = "i"
) -> Dict:
    first_field = next(iter(question.get("fields", [])), {"": ""})
    # Take the first part of the first field name before . and remove [i] to get bare object name
    object_name = (
        next(iter(first_field.values()), "").split(".")[0].replace(f"[{iterator}]", "")
    )
    nice_obj_name = object_name.replace("_", " ")
    first_attribute_name = next(iter(first_field.values()), "").split(".")[-1]
    review_block = {
        "show if": f"{object_name}[{number_of_expansions}:]",
        "note": f"**{ single_paragraph(question['question']) }** (additional {noun_plural(nice_obj_name) })\n\n${{ { object_name }.{ first_attribute_name }_table }}",
    }
    return review_block


def get_iterator_question_table(
    question: Dict, number_of_expansions: int = 3, iterator: str = "i"
) -> Dict:
    first_field = next(iter(question.get("fields", [])), {"": ""})
    # Take the first part of the first field name before . and remove [i] to get bare object name
    object_name = (
        next(iter(first_field.values()), "").split(".")[0].replace(f"[{iterator}]", "")
    )
    first_attribute_name = next(iter(first_field.values()), "").split(".")[-1]
    attributes = {
        next(iter(field.values()), "").split(".")[-1]
        for field in question.get("fields", [])
    }
    return {
        "table": f"{ object_name }.{ first_attribute_name }_table",
        "rows": f"{object_name}[{number_of_expansions}:]",
        "edit": list(attributes),
        "columns": [
            {
                attribute.replace(
                    "_", " "
                ).capitalize(): f"showifdef(row_item.attr_name('{ attribute }'))"
            }
            for attribute in attributes
        ],
    }


def get_name_and_address_review_blocks(
    objects: List[Dict[str, str]], number_of_expansions: int = 3
) -> List[Dict]:
    """
    Create review blocks for name and address of lists of people as a special
    case, because someone is likely to use the AssemblyLine built-in questions
    for those attributes.
    """
    blocks: list = []
    for obj in objects:
        obj_name = next(iter(obj.keys()), "")
        obj_class = next(iter(obj.values()), "").split(".")[0]
        # We only build name and address blocks for lists of people (predetermined by our known object names)
        if obj_class not in ["PeopleList", "ALPeopleList", "PartyList", "ChildList"]:
            continue
        if obj_name == "users":
            nice_obj_name = "people on your side of the case"
        else:
            nice_obj_name = obj_name.replace("_", " ")

        blocks.append(
            {
                "show if": f"{ obj_name }[{ number_of_expansions }:]",
                "note": f"**Names of { nice_obj_name }**\n\n${{ { obj_name }.name_table }}",
            }
        )

        blocks.append(
            {
                "show if": f"{obj_name}[{number_of_expansions}:]",
                "note": f"**Addresses of { nice_obj_name }**\n\n${{ { obj_name }.address_table }}",
            }
        )

    return blocks


def field_and_label(field: Dict[str, str]) -> Tuple[str, str]:
    """
    Get the field name and label in a predictable order, regardless of whether
    the field label is the first key in the dictionary.
    """
    # Check to see if this field uses the special {field: var, label: text} syntax
    if any(True for item in field.items() if item[0] in ["field", "label"]):
        field_name = next(item[1] for item in field.items() if item[0] == "field")
        field_text = single_paragraph(
            next(item[1] for item in field.items() if item[0] == "label")
        )
    else:
        # Figure out the field label and variable name by looking for the first item
        # that isn't a special keyword like "datatype"
        label_pair = next(
            (pair for pair in field.items() if pair[0] not in not_labels), None
        )
        if label_pair:
            field_name = label_pair[1]
            field_text = label_pair[0]
        else:
            return "", ""

    return field_name, field_text


def get_name_and_address_tables(
    objects: List[Dict[str, str]], number_of_expansions: int = 3
) -> List[Dict]:
    """
    Create review tables for name and address of lists of people as a special
    case, because someone is most likely to use the AssemblyLine built-in questions
    for those attributes.
    """
    blocks: list = []
    for obj in objects:
        obj_name = next(iter(obj.keys()), "")
        obj_class = next(iter(obj.values()), "").split(".")[0]
        # We only build name and address blocks for lists of people (predetermined by our known object names)
        if obj_class not in ["PeopleList", "ALPeopleList", "PartyList", "ChildList"]:
            continue

        blocks.append(
            {
                "table": f"{obj_name}.name_table",
                "rows": f"{obj_name}[{number_of_expansions}:]",
                "columns": {f"Name": "row_item.name.full(middle='full')"},
                "edit": ["name.first"],
            }
        )

        blocks.append(
            {
                "table": f"{obj_name}.address_table",
                "rows": f"{obj_name}[{number_of_expansions}:]",
                "columns": {"Address": "row_item.address.on_one_line()"},
                "edit": ["address.address"],
            }
        )

    return blocks


def expand_fields(fields: List[Dict]) -> List[Dict]:
    """
    Transform a list of fields that include 'code' statements and expand
    expressions that represent special AssemblyLine methods like `name_fields`,
    `address_fields`, `gender_fields` etc. into the actual list of fields
    we know those represent.
    """
    name_match = re.compile(r"((\w+\[\w\])|\w+)")
    expanded_fields = []
    for field in fields:
        if field and "code" in field:
            match = name_match.match(field["code"])
            if match:
                object_name = match[1]
            else:
                continue
            if ".name_fields(" in field["code"]:
                expanded_fields.extend(
                    [
                        {"First": f"{ object_name }.name.first"},
                        {"Middle": f"{ object_name }.name.middle"},
                        {"Last": f"{ object_name }.name.last"},
                        {"Suffix": f"{ object_name }.name.suffix"},
                    ]
                )
            elif ".address_fields(" in field["code"]:
                expanded_fields.extend(
                    [
                        {"Address": f"{ object_name }.address.address"},
                        {"Apartment or Unit": f"{ object_name }.address.unit"},
                        {"City": f"{ object_name }.address.city"},
                        {"State": f"{ object_name }.address.state"},
                        {"Zip": f"{ object_name }.address.zip"},
                        {"Country": f"{ object_name }.address.country"},
                    ]
                )
            elif ".gender_fields(" in field["code"]:
                expanded_fields.extend(
                    [
                        {"Gender": f"{ object_name }.gender"},
                    ]
                )
            elif ".language_fields(" in field["code"]:
                expanded_fields.extend(
                    [
                        {"Language": f"{ object_name }.language"},
                    ]
                )
        elif field:
            expanded_fields.append(field)

    return expanded_fields


def get_objects(yaml_parsed: List[Any]) -> List[Dict[str, str]]:
    objects = []
    for doc in yaml_parsed:
        if doc.get("objects"):
            objects.extend(doc["objects"])
    return objects

from docassemble.base.util import DAEmpty
import ruamel.yaml
import mako.template
import mako.runtime

mako.runtime.UNDEFINED = DAEmpty()
from mako import exceptions
from typing import List, Tuple

__all__ = ["validate_attachment_block"]


def validate_attachment_block(fields_statement: str) -> List[Tuple[str, str]]:
    yaml = ruamel.yaml.YAML(typ="rt")
    parsed_blocks = yaml.load(fields_statement)

    errors = []
    for index, row in enumerate(parsed_blocks["fields"]):
        try:
            mytemplate = mako.template.Template(next(iter(row.values())))
            content = mytemplate.render()
        except:
            errors.append(
                (
                    f"Error on row {index}, id: {row}",
                    exceptions.text_error_template().render(),
                )
            )
    return errors

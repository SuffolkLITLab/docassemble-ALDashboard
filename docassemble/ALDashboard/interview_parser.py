from dataclasses import dataclass, field
from typing import List, Dict
from ruamel.yaml import YAML
from docassemble.base.util import DAFileList

__all__ = ['parse_interview_yaml', 'parse_interview_docs', 'ParseResult']

@dataclass
class ParseResult:
    questions: List[Dict[str, any]] = field(default_factory=list)
    objects: List[Dict[str, any]] = field(default_factory=list)
    sections: List[Dict[str, any]] = field(default_factory=list)
    metadata: Dict[str, any] = field(default_factory=dict)
    attributes: Dict[str, any] = field(default_factory=dict)

def parse_interview_yaml(yaml_files:DAFileList) -> ParseResult:
    """
    Parse a Docassemble interview YAML file, and return a ParseResult object
    containing:

    - questions: List of questions
    - objects: List of objects
    - sections: List of sections
    - metadata: Metadata
    - attributes: Attributes

    Args:
        yaml_files: A DAFileList object containing the YAML files

    Returns:
        ParseResult object
    """
    yaml = YAML(typ='safe', pure=True)
    yaml_parsed = []
    for f in yaml_files:
        yaml_parsed.extend(list(yaml.load_all(f.slurp())))

    return parse_interview_docs(yaml_parsed)


def parse_interview_docs(yaml_parsed: List[Dict[str, any]]) -> ParseResult:
    """
    Given a list of parsed YAML documents, return a ParseResult object
    with usable information from a Docassemble interview or interviews
    """
    result = ParseResult()
    result.objects = []
    result.attributes = {}
    result.questions = []
    result.generic_question_blocks = []
    result.sections = []

    for doc in yaml_parsed:
        if isinstance(doc, dict):
            if 'metadata' in doc:
                result.metadata.update(doc['metadata'])
            if 'sections' in doc:
                result.sections.extend([next(iter(sec.keys()), [""]) for sec in doc["sections"]])
            if 'objects' in doc:
                result.objects.extend(doc["objects"])
            if 'question' in doc:
                # Parse question to create a standardized question object
        # Update result.questions, result.objects, result.sections based on doc
    return result

# do not pre-load
"""Every packaged question block must survive docassemble's own parser.

A block can be valid YAML, and pass dayamlchecker, and still be rejected by
docassemble at runtime -- `show if:` accepts only `variable` plus `is`, or
`code`, and anything else raises `DASourceError` the moment a user opens the
screen. That failure mode is invisible to every other check in CI, so it is
worth spending a test on: this walks the package's question files and builds
each block the way the server would.
"""

import glob
import os
import unittest

from ruamel.yaml import YAML
from ruamel.yaml.compat import StringIO

try:
    from docassemble.base.parse import Interview, InterviewSourceString, Question
except Exception:  # pragma: no cover - depends on the server's packages
    Interview = None  # type: ignore[assignment, misc]


def _parser_runtime_available():
    """The parser needs the request-local state that the webapp initializes."""
    if Interview is None:
        return False
    try:
        from docassemble.base.thread_context import this_thread

        return this_thread._get_current_object() is not None
    except (ImportError, RuntimeError):
        return False


PACKAGE = "docassemble.ALDashboard"
QUESTIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "questions",
)

# Keys that mark a mapping as something docassemble would build a Question from.
QUESTION_KEYS = ("question", "fields", "code", "event")


def _question_files():
    return sorted(glob.glob(os.path.join(QUESTIONS_DIR, "*.yml")))


@unittest.skipUnless(
    _parser_runtime_available(),
    "docassemble parser requires an active webapp request context",
)
class TestPackagedQuestionBlocksParse(unittest.TestCase):
    def test_every_question_block_builds(self):
        yaml = YAML(typ="safe", pure=True)
        files = _question_files()
        self.assertTrue(files, "no packaged question files were found")

        parsed = 0
        for path in files:
            with self.subTest(interview=os.path.basename(path)):
                with open(path, "r", encoding="utf-8") as handle:
                    text = handle.read()
                source = InterviewSourceString(
                    content="", directory=None, path=path, package=PACKAGE
                )
                interview = Interview(source=source)
                documents = list(yaml.load_all(StringIO(text)))
                for index, document in enumerate(documents):
                    if not isinstance(document, dict):
                        continue
                    if not any(key in document for key in QUESTION_KEYS):
                        continue
                    label = document.get("id") or f"document {index}"
                    try:
                        Question(document, interview, source=source, package=PACKAGE)
                    except Exception as err:
                        self.fail(
                            f"{os.path.basename(path)} [{label}] is not a valid "
                            f"docassemble block: {err}"
                        )
                    parsed += 1

        self.assertGreater(parsed, 0, "no question blocks were checked")

    def test_the_court_form_options_screen_is_valid(self):
        """The screen that gained the shape and jurisdiction fields.

        `show if:` with `is not:` parsed as YAML and passed dayamlchecker but
        was rejected by the server; the same-screen condition has to be written
        as `js show if:`.
        """
        path = os.path.join(QUESTIONS_DIR, "variable_report_generator.yml")
        self.assertTrue(os.path.isfile(path))
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()

        yaml = YAML(typ="safe", pure=True)
        source = InterviewSourceString(
            content="", directory=None, path=path, package=PACKAGE
        )
        interview = Interview(source=source)

        options_screen = None
        for document in yaml.load_all(StringIO(text)):
            if isinstance(document, dict) and document.get("id") == (
                "variable report options"
            ):
                options_screen = document
                break

        self.assertIsNotNone(options_screen, "the options screen went missing")
        assert options_screen is not None  # for mypy
        Question(options_screen, interview, source=source, package=PACKAGE)

        field_names = set()
        for field in options_screen.get("fields") or []:
            if isinstance(field, dict):
                field_names.update(str(key) for key in field.values())
        self.assertIn("report_shape", field_names)
        self.assertIn("court_profile", field_names)


if __name__ == "__main__":
    unittest.main()

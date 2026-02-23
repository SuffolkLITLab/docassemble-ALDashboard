# do not pre-load

import unittest
from typing import Optional
from .validate_docx import detect_docx_automation_features, get_jinja_errors
from pathlib import Path
import tempfile
import zipfile
import os


class TestGetJinjaErrors(unittest.TestCase):
    def _build_docx(self, parts):
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as temp_docx:
            docx_path = temp_docx.name
        with zipfile.ZipFile(docx_path, "w") as archive:
            for name, content in parts.items():
                archive.writestr(name, content)
        return docx_path

    def test_working_template(self):
        working_template = Path(__file__).parent / "test/made_up_variables.docx"
        result: Optional[str] = get_jinja_errors(working_template)
        self.assertIsNone(result)

    def test_failing_template(self):
        failing_template = Path(__file__).parent / "test/valid_word_invalid_jinja.docx"
        result: Optional[str] = get_jinja_errors(failing_template)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)

    def test_detects_field_controls_in_body(self):
        docx_path = self._build_docx(
            {
                "word/document.xml": """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:fldChar w:fldCharType="begin"/><w:instrText>MERGEFIELD ClientName</w:instrText></w:p></w:body>
</w:document>""",
            }
        )
        try:
            findings = detect_docx_automation_features(docx_path)
            codes = {item["code"] for item in findings["warning_details"]}
            self.assertIn("classic_fields", codes)
            self.assertIn("field_instructions", codes)
        finally:
            if os.path.exists(docx_path):
                os.remove(docx_path)

    def test_detects_sdt_controls(self):
        docx_path = self._build_docx(
            {
                "word/document.xml": """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:sdt><w:sdtPr><w:text/><w:tag w:val="x"/></w:sdtPr><w:sdtContent/></w:sdt></w:body>
</w:document>""",
            }
        )
        try:
            findings = detect_docx_automation_features(docx_path)
            codes = {item["code"] for item in findings["warning_details"]}
            self.assertIn("structured_document_tags", codes)
            self.assertIn("sdt_plain_text_control", codes)
            self.assertIn("sdt_metadata", codes)
        finally:
            if os.path.exists(docx_path):
                os.remove(docx_path)

    def test_detects_fragmented_runs(self):
        runs = "".join(f"<w:r><w:t>fragment{i:02d}</w:t></w:r>" for i in range(12))
        docx_path = self._build_docx(
            {
                "word/document.xml": f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p>{runs}</w:p></w:body>
</w:document>""",
            }
        )
        try:
            findings = detect_docx_automation_features(docx_path)
            codes = {item["code"] for item in findings["warning_details"]}
            self.assertIn("fragmented_runs", codes)
        finally:
            if os.path.exists(docx_path):
                os.remove(docx_path)

    def test_ignores_footer_page_number_controls(self):
        docx_path = self._build_docx(
            {
                "word/document.xml": """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:t>x</w:t></w:p></w:body></w:document>""",
                "word/footer1.xml": """<?xml version="1.0" encoding="UTF-8"?>
<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:sdt><w:sdtPr><w:docPartObj><w:docPartGallery w:val="Page Numbers (Bottom of Page)"/></w:docPartObj></w:sdtPr>
    <w:sdtContent><w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> PAGE </w:instrText></w:r></w:p></w:sdtContent>
  </w:sdt>
</w:ftr>""",
            }
        )
        try:
            findings = detect_docx_automation_features(docx_path)
            codes = {item["code"] for item in findings["warning_details"]}
            self.assertNotIn("classic_fields", codes)
            self.assertNotIn("structured_document_tags", codes)
        finally:
            if os.path.exists(docx_path):
                os.remove(docx_path)


if __name__ == "__main__":
    unittest.main()

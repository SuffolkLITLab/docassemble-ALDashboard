import unittest

from docassemble.ALDashboard.api_dashboard_utils import (
    DEFAULT_MAX_UPLOAD_BYTES,
    DashboardAPIValidationError,
    _validate_upload_size,
    coerce_async_flag,
    decode_base64_content,
    parse_bool,
)


class TestDashboardAPIUtils(unittest.TestCase):
    def test_parse_bool_accepts_common_values(self):
        self.assertTrue(parse_bool("true"))
        self.assertTrue(parse_bool("YES"))
        self.assertFalse(parse_bool("0"))
        self.assertFalse(parse_bool("off"))

    def test_parse_bool_rejects_invalid(self):
        with self.assertRaises(DashboardAPIValidationError):
            parse_bool("not-a-bool")

    def test_decode_base64_content_validation(self):
        self.assertEqual(decode_base64_content("YQ=="), b"a")
        with self.assertRaises(DashboardAPIValidationError):
            decode_base64_content("")
        with self.assertRaises(DashboardAPIValidationError):
            decode_base64_content("%%%")

    def test_coerce_async_flag(self):
        self.assertTrue(coerce_async_flag({"mode": "async"}))
        self.assertFalse(coerce_async_flag({"mode": "sync"}))
        self.assertTrue(coerce_async_flag({"async": "true"}))
        self.assertFalse(coerce_async_flag({}))
        with self.assertRaises(DashboardAPIValidationError):
            coerce_async_flag({"mode": "later"})

    def test_validate_upload_size(self):
        _validate_upload_size(b"x")
        with self.assertRaises(DashboardAPIValidationError):
            _validate_upload_size(b"")
        with self.assertRaises(DashboardAPIValidationError):
            _validate_upload_size(b"x" * (DEFAULT_MAX_UPLOAD_BYTES + 1))


if __name__ == "__main__":
    unittest.main()

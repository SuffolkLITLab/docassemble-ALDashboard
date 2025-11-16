import ast
import unittest
from typing import Dict


def _extract_functions_from_file(path, names):
    """Return a dict mapping function name -> source text for each name found in the file."""
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    functions = {}
    lines = src.splitlines(True)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            # ast indexes are 1-based and end_lineno is available in Python 3.8+
            start = node.lineno - 1
            end = node.end_lineno
            functions[node.name] = "".join(lines[start:end])
    return functions


class TestUsageHeatmapHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = __file__.replace("/test/test_usage_heatmap.py", "/aldashboard.py")
        funcs = _extract_functions_from_file(
            path, {"make_usage_rows", "compute_heatmap_styles"}
        )
        ns = {}
        # provide minimal names expected by the functions (typing annotations may reference these)
        ns.update(
            {
                "Optional": __import__("typing").Optional,
                "List": __import__("typing").List,
                "Dict": __import__("typing").Dict,
                "Callable": __import__("typing").Callable,
                "math": __import__("math"),
            }
        )
        # execute the function source into ns
        for name, src in funcs.items():
            exec(src, ns)
        cls.ns = ns

    def test_make_usage_rows_basic(self):
        make_usage_rows = self.ns["make_usage_rows"]
        data = {
            1: [{"filename": "pkg:qa.yml", "sessions": 5, "users": 1}],
            5: [{"filename": "pkg:qa.yml", "sessions": 10, "users": 2}],
            10: [{"filename": "pkg:qa.yml", "sessions": 20, "users": 3}],
            30: [{"filename": "pkg:qa.yml", "sessions": 50, "users": 4}],
            60: [{"filename": "pkg:qa.yml", "sessions": 100, "users": 5}],
            120: [{"filename": "pkg:qa.yml", "sessions": 200, "users": 15}],
        }
        rows = make_usage_rows(data, lambda x: "qa", limit=1)
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        # totals
        self.assertEqual(r["s_1"], 5)
        self.assertEqual(r["s_5"], 10)
        self.assertEqual(r["s_10"], 20)
        self.assertEqual(r["s_30"], 50)
        self.assertEqual(r["s_60"], 100)
        self.assertEqual(r["s_120"], 200)
        self.assertEqual(r["users"], 15)
        self.assertEqual(r["total"], 5 + 10 + 20 + 50 + 100 + 200)

    def test_compute_heatmap_styles_basic(self):
        compute = self.ns["compute_heatmap_styles"]
        rows = [
            {
                "filename": "pkg:qa.yml",
                "title": "qa",
                "s_1": 5,
                "s_5": 10,
                "s_10": 20,
                "s_30": 50,
                "s_60": 100,
                "s_120": 200,
                "users": 15,
            }
        ]
        out = compute(rows)
        self.assertIs(out, rows)
        r = out[0]
        # ensure style attributes and text colors were added
        for m in (1, 5, 10, 30, 60, 120):
            self.assertIn(f"style_attr_{m}", r)
            self.assertIn("background-color: rgba(255,0,0", r[f"style_attr_{m}"])
            self.assertIn(f"text_color_{m}", r)
        self.assertIn("style_attr_users", r)
        self.assertIn("text_color_users", r)

    def test_compute_heatmap_styles_zero(self):
        compute = self.ns["compute_heatmap_styles"]
        rows = [{"filename": "none", "title": "none", "s_1": 0, "users": 0}]
        out = compute(rows)
        r = out[0]
        # with no data, style should still exist but use minimal alpha
        self.assertIn("style_attr_1", r)
        self.assertIn("style_attr_users", r)


if __name__ == "__main__":
    unittest.main()

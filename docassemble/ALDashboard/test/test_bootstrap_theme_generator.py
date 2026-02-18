import re
import unittest

from docassemble.ALDashboard.bootstrap_theme_generator import (
    contrast_ratio,
    easy_theme_scss,
    random_seed_colors,
)


class TestBootstrapThemeGenerator(unittest.TestCase):
    def test_easy_theme_scss_generates_core_bootstrap_variables(self):
        result = easy_theme_scss("#0D6EFD", font_choice="system", style_choice="rounded")
        scss = result["scss"]
        self.assertIn('$theme-colors: (', scss)
        self.assertIn('"primary":', scss)
        self.assertIn('"secondary":', scss)
        self.assertIn('"success":', scss)
        self.assertIn('"warning":', scss)
        self.assertIn('@import "bootstrap";', scss)

    def test_easy_theme_colors_meet_min_contrast_against_white(self):
        result = easy_theme_scss("#8E44AD", font_choice="georgia", style_choice="square")
        for color_name in ("primary", "secondary", "success", "info", "warning", "danger"):
            ratio = contrast_ratio(result["theme_colors"][color_name], "#FFFFFF")
            self.assertGreaterEqual(
                ratio,
                4.5,
                f"{color_name} contrast ratio too low: {ratio}",
            )

    def test_random_seed_colors_are_hex(self):
        seeds = random_seed_colors(3)
        self.assertEqual(len(seeds), 3)
        for seed in seeds:
            self.assertRegex(seed, re.compile(r"^#[0-9A-F]{6}$"))


if __name__ == "__main__":
    unittest.main()

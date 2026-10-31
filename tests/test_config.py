"""Configuration boundary tests; all filesystem changes live in a temp directory."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import contour_label as c


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.saved = {name: getattr(c, name) for name in c._CONFIG_NAMES}
        self.addCleanup(lambda: c.__dict__.update(self.saved))

    def test_example_rejected_before_io(self):
        with self.assertRaisesRegex(ValueError, "Configure CONTOUR_GDB"):
            c.preflight_config()

    def test_missing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Config missing"):
                c.load_config(Path(directory) / "missing.py")

    def test_incomplete_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.py"
            path.write_text('CONTOUR_GDB = "data/synthetic.gdb"', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Incomplete config"):
                c.load_config(path)

    def test_complete_config_and_derived_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.py"
            content = c._EXAMPLE_CONFIG.read_text(encoding="utf-8")
            content = content.replace("LABEL_MAP_LENGTH_MM = 5.669", "LABEL_MAP_LENGTH_MM = 6.0")
            path.write_text(content, encoding="utf-8")
            c.load_config(path)
            self.assertEqual(c.LABEL_GROUND_LENGTH_M, 60)
            self.assertEqual(c.MIN_END_CLEARANCE_M, 35)
            self.assertFalse(c.OVERWRITE_OUTPUT_GDB)

    def test_output_input_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            c.CONTOUR_GDB = str(base / "input.gdb")
            c.CONTOUR_LAYER = "synthetic_contours"
            c.SHEET_GDB = str(base / "sheets.gdb")
            c.SHEET_LAYER = "synthetic_sheets"
            c.INDEX_OBJE_CODES = ["SYNTHETIC_INDEX"]
            c.USE_DEM_FALLBACK = False
            for output in [base / "input.gdb", base / "input.gdb" / "child.gdb"]:
                c.OUTPUT_GDB = str(output)
                with self.subTest(output=output):
                    with self.assertRaisesRegex(ValueError, "must not overlap"):
                        c.preflight_config()
            c.CONTOUR_GDB = str(base / "parent.gdb" / "nested.gdb")
            c.OUTPUT_GDB = str(base / "parent.gdb")
            with self.assertRaisesRegex(ValueError, "must not overlap"):
                c.preflight_config()
            c.OUTPUT_GDB = str(base / "output.gdb")
            c.preflight_config()

    def test_cli_does_not_call_main_on_invalid_config(self):
        with patch.object(c, "main") as main:
            with self.assertRaises(ValueError):
                c.cli(["--config", str(c._EXAMPLE_CONFIG)])
            main.assert_not_called()


if __name__ == "__main__":
    unittest.main()

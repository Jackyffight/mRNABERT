import copy
import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path


DESIGN_FLOW_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = DESIGN_FLOW_ROOT / "scripts" / "plot_stage_closed_loop.py"
DATA_PATH = DESIGN_FLOW_ROOT / "docs" / "reports" / "stage-closed-loop-data-20260716.json"
PNG_PATH = DESIGN_FLOW_ROOT / "figures" / "stage-closed-loop-20260716.png"
SVG_PATH = DESIGN_FLOW_ROOT / "figures" / "stage-closed-loop-20260716.svg"


def load_plot_module():
    spec = importlib.util.spec_from_file_location("plot_stage_closed_loop", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class StageClosedLoopPlotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_plot_module()
        cls.data = cls.module.load_json(DATA_PATH)

    def test_audited_evidence_validates(self):
        self.module.validate_evidence(self.data)

    def test_advantage_metrics_are_derived_from_audited_counts(self):
        metrics = self.module.derive_advantage_metrics(self.data)

        self.assertEqual(metrics["lineage"]["numerator"], 384)
        self.assertEqual(metrics["lineage"]["denominator"], 384)
        self.assertEqual(metrics["lineage"]["ratio"], 1.0)
        self.assertEqual(metrics["diversity"]["numerator"], 53)
        self.assertEqual(metrics["diversity"]["denominator"], 53)
        self.assertEqual(metrics["verification"]["numerator"], 20)
        self.assertEqual(metrics["verification"]["denominator"], 20)
        self.assertEqual(metrics["followup_deferral"]["numerator"], 332)
        self.assertEqual(metrics["followup_deferral"]["denominator"], 384)
        self.assertAlmostEqual(metrics["followup_deferral"]["ratio"], 0.8645833333)

    def test_pending_scientific_work_cannot_be_presented_as_closed(self):
        altered = copy.deepcopy(self.data)
        altered["execution"]["stage7"]["execution_state"] = "executed"

        with self.assertRaisesRegex(ValueError, "Stage 7 must remain pending"):
            self.module.validate_evidence(altered)

    def test_committed_figure_is_large_nontrivial_and_labeled(self):
        png = PNG_PATH.read_bytes()
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = struct.unpack(">II", png[16:24])
        self.assertGreaterEqual(width, 2400)
        self.assertGreaterEqual(height, 1600)
        self.assertGreater(len(png), 150_000)

        svg = SVG_PATH.read_text(encoding="utf-8")
        for expected in (
            "Immutable Stage 1-7 closed loop",
            "Broad search, controlled expensive compute",
            "Models are replaceable tools, not authority",
            "Quantified system advantages",
        ):
            self.assertIn(expected, svg)

    def test_render_writes_nonblank_png_and_svg_when_matplotlib_is_available(self):
        if importlib.util.find_spec("matplotlib") is None:
            self.skipTest("matplotlib is not installed in this Python environment")

        import matplotlib.image as mpimg

        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "closed-loop"
            png_path, svg_path = self.module.render_figure(self.data, prefix, dpi=100)
            pixels = mpimg.imread(png_path)

        self.assertTrue(png_path.name.endswith(".png"))
        self.assertTrue(svg_path.name.endswith(".svg"))
        self.assertGreater(float(pixels.std()), 0.02)
        self.assertLess(float(pixels.min()), 0.2)


if __name__ == "__main__":
    unittest.main()

import unittest
from visual_edit import validate_visual_edit, resolve_framing, transition_windows


class VisualEditTests(unittest.TestCase):
    def test_legacy_and_per_ratio_fallback(self):
        self.assertEqual(validate_visual_edit({}), {})
        edit = {"fit": "contain", "crop_x": .7, "framing": {"9:16": {"fit": "cover"}}}
        self.assertEqual(resolve_framing(edit, "9:16"), {"fit": "cover", "crop_x": .7, "crop_y": .5})
        self.assertEqual(resolve_framing(edit, "16:9")["fit"], "contain")

    def test_reject_unknown_nonfinite_and_out_of_range_settings(self):
        for edit in ({"framing": {"1:1": {}}}, {"framing": {"16:9": {"crop_y": float("nan")}}},
                     {"framing": {"9:16": {"crop_x": 1.1}}}, {"framing": {"9:16": {"fit": "invalid"}}},
                     {"transition": {"duration_seconds": True}}, {"transition": {"duration_seconds": float("inf")}},
                     {"transition": {"kind": "fade", "unsafe": 1}}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                validate_visual_edit(edit)

    def test_transition_clamps_without_overlap_and_ignores_last_boundary(self):
        rows = [{"duration": .2, "edit": {"transition": {"kind": "fade_black", "duration_seconds": .8}}} for _ in range(3)]
        incoming, outgoing, warnings = transition_windows(rows)
        self.assertEqual(incoming, [0, .1, .1])
        self.assertEqual(outgoing, [.1, .1, 0])
        self.assertEqual(len(warnings), 2)
        self.assertEqual(transition_windows([{"duration": 1}]), ([0], [0], []))

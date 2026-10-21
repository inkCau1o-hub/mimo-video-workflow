from copy import deepcopy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shot_timing import normalize_timing, shot_duration, subtitle_events


class TimingContractTests(unittest.TestCase):
    def test_legacy_metadata_stays_unchanged(self):
        for edit in ({}, {"subtitle": {"text": "old"}}):
            self.assertEqual(normalize_timing(edit), edit)
        self.assertEqual(shot_duration({}, 5, 2), 2)
        self.assertEqual(subtitle_events("old", {}, 2), [{"start": 0, "end": 2, "text": "old"}])

    def test_gaps_extend_narration_but_fit_inside_manual_duration(self):
        edit = normalize_timing({"lead_in_seconds": .4, "tail_out_seconds": .6})
        self.assertEqual(shot_duration(edit, 9, 3), 4)
        self.assertEqual(shot_duration(dict(edit, duration_mode="manual"), 5, 3), 5)
        with self.assertRaises(ValueError):
            shot_duration(dict(edit, duration_mode="manual"), 3.5, 3)
        with self.assertRaises(ValueError):
            shot_duration(edit, .5)
        with self.assertRaises(ValueError):
            shot_duration(edit, 3599, 3599.5)

    def test_timing_rejects_nonfinite_boolean_string_and_large_values(self):
        for key in ("lead_in_seconds", "tail_out_seconds"):
            for value in (-1, 11, True, "1", None, float("nan"), float("inf")):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    normalize_timing({key: value})

    def test_narration_captions_exclude_gaps_and_manual_remainder(self):
        edit = {"lead_in_seconds": .4, "tail_out_seconds": .6, "subtitle": {"timing_mode": "narration"}}
        self.assertEqual(subtitle_events("caption", edit, 5, 3), [{"start": .4, "end": 3.4, "text": "caption"}])
        self.assertEqual(subtitle_events("caption", edit, 5)[0]["end"], 4.4)
        self.assertEqual(subtitle_events("", edit, 5, 3), [])

    def test_cues_retain_gaps_literal_content_and_input(self):
        edit = {"subtitle": {"timing_mode": "cues", "cues": [
            {"start": .2, "end": .8, "text": "first"}, {"start": 1, "end": 2, "text": "{literal}\\N"}]}}
        before = deepcopy(edit)
        validated = normalize_timing(edit)
        events = subtitle_events("ignored", validated, 3)
        self.assertEqual(events, edit["subtitle"]["cues"])
        events[0]["text"] = "modified result"
        self.assertEqual(edit, before)
        with self.assertRaises(ValueError):
            subtitle_events("ignored", validated, 1.9)
        self.assertEqual(subtitle_events("ignored", {"subtitle": {"timing_mode": "cues", "cues": []}}, 3), [])

    def test_malformed_overlapping_unsorted_or_oversized_cues_rejected(self):
        bad = [None, {}, [None], [{"start": 0, "end": 1, "text": ""}],
               [{"start": False, "end": 1, "text": "x"}], [{"start": "0", "end": 1, "text": "x"}],
               [{"start": 0, "end": .001, "text": "x"}], [{"start": 0, "end": 2, "text": "x"}, {"start": 1, "end": 3, "text": "y"}],
               [{"start": 2, "end": 3, "text": "x"}, {"start": 0, "end": 1, "text": "y"}],
               [{"start": 0, "end": 1, "text": "x" * 10001}], [{"start": i, "end": i + .5, "text": "x"} for i in range(101)]]
        for cues in bad:
            with self.subTest(cues=str(cues)[:60]), self.assertRaises(ValueError):
                normalize_timing({"subtitle": {"timing_mode": "cues", "cues": cues}})


if __name__ == "__main__":
    unittest.main()

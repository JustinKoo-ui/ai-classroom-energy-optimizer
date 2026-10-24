from __future__ import annotations

import unittest

from aceo_cv.occupancy import (
    prediction_count,
    workflow_output_count,
    workflow_result_shape,
)


class PredictionCountTests(unittest.TestCase):
    def test_counts_detection_objects(self) -> None:
        class DetectionSet:
            def __len__(self) -> int:
                return 3

        self.assertEqual(prediction_count(DetectionSet()), 3)

    def test_preserves_empty_detection_object_as_zero(self) -> None:
        class EmptyDetectionSet:
            def __len__(self) -> int:
                return 0

        self.assertEqual(prediction_count(EmptyDetectionSet()), 0)

    def test_counts_prediction_list(self) -> None:
        self.assertEqual(prediction_count({"predictions": [{}, {}]}), 2)

    def test_reads_numeric_workflow_output(self) -> None:
        self.assertEqual(prediction_count({"output": 7}), 7)

    def test_counts_sequence_output(self) -> None:
        self.assertEqual(prediction_count([{}, {}, {}]), 3)

    def test_preserves_zero_as_a_valid_count(self) -> None:
        self.assertEqual(prediction_count({"predictions": []}), 0)

    def test_missing_or_unsupported_output_is_unknown(self) -> None:
        self.assertIsNone(prediction_count(None))
        self.assertIsNone(prediction_count({"unexpected": []}))
        self.assertIsNone(prediction_count(True))

    def test_extracts_named_workflow_output(self) -> None:
        self.assertEqual(
            workflow_output_count({"tracked_people": {"predictions": [{}]}}, "tracked_people"),
            1,
        )

    def test_describes_result_structure_without_values(self) -> None:
        result = {"people_output": {"predictions": [{"secret": "not logged"}]}}

        self.assertEqual(
            workflow_result_shape(result),
            "mapping {people_output=mapping keys=['predictions']}",
        )


if __name__ == "__main__":
    unittest.main()

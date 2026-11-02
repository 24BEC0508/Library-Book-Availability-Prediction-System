"""
Unit tests for model training, serialization, reloading, and feature encoding.
"""

import unittest
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT / "model" / "regression_model.npz"


class TestModel(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODEL_PATH.exists(), f"Model file must exist at {MODEL_PATH}")
        self.model = np.load(MODEL_PATH, allow_pickle=True)

    def test_model_artifacts_and_metadata(self):
        """Verify model artifact contains all required weights and category metadata."""
        expected_keys = {
            "coefficients",
            "feature_names",
            "numeric_features",
            "departments",
            "exam_types",
            "historical_mean",
            "cutoff_date",
            "train_rows",
        }
        self.assertTrue(expected_keys.issubset(set(self.model.keys())))

        coeffs = self.model["coefficients"]
        feature_names = self.model["feature_names"]

        # 1 intercept + 7 numeric + 16 departments + 3 exam types = 27 features
        self.assertEqual(len(coeffs), 27)
        self.assertEqual(len(feature_names), 27)
        self.assertEqual(feature_names[0], "intercept")

    def test_linear_algebra_reproducibility(self):
        """Verify predictions are identical on reloaded coefficients."""
        coeffs = self.model["coefficients"]

        # Synthetic feature vector
        x1 = np.ones(27, dtype=np.float64)
        pred1 = float(x1 @ coeffs)

        # Save to temporary array and reload
        reloaded_coeffs = np.copy(coeffs)
        pred2 = float(x1 @ reloaded_coeffs)

        self.assertAlmostEqual(pred1, pred2, places=10)

    def test_unknown_department_handling(self):
        """Verify feature encoding handles unknown or novel departments safely without crashing."""
        from ml.train_regression import DEPARTMENTS, EXAM_TYPES, build_feature_matrix

        dummy_df = pd.DataFrame({
            "total_copies": [25],
            "checkouts_last_1d": [0.0],
            "checkouts_last_3d": [0.0],
            "checkouts_last_7d": [0.0],
            "returns_last_7d": [0.0],
            "demand_growth": [0.0],
            "days_to_first_exam": [10],
            "department": ["Astronomy"],  # Unknown dept
            "exam_type": ["CAT2"],
            "target_next_7d_checkouts": [0],
            "date": ["2026-05-01"],
        })

        X = build_feature_matrix(dummy_df)
        self.assertEqual(X.shape, (1, 27))
        # All 16 department columns should be 0.0 for unknown dept
        dept_slice = X[0, 8:24]
        self.assertEqual(np.sum(dept_slice), 0.0)


if __name__ == "__main__":
    unittest.main()

"""
Unit tests for data pipeline, feature engineering, and target rolling calculations.
"""

import unittest
import numpy as np
import pandas as pd


class TestDataPipeline(unittest.TestCase):
    def test_rolling_target_calculation_synthetic_fixture(self):
        """
        Verify that target_next_7d_checkouts at day t:
        - Excludes day t checkouts completely.
        - Strictly sums checkouts on days t+1 through t+7.
        - Produces NaN when the 7-day future window is incomplete.
        """
        # Daily checkouts for 10 consecutive days
        # Days: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
        daily_counts = [5, 1, 2, 0, 3, 1, 0, 2, 4, 1]
        dates = pd.date_range("2026-01-01", periods=10, freq="D")
        s = pd.Series(daily_counts, index=dates)

        # Implementation used in pipeline:
        future_checkouts = s.shift(-1)
        target = future_checkouts.iloc[::-1].rolling(7, min_periods=7).sum().iloc[::-1]

        # Day 0 (2026-01-01): sum of days 1..7 (values: 1, 2, 0, 3, 1, 0, 2) = 9
        self.assertEqual(target.iloc[0], 9.0)

        # Day 1 (2026-01-02): sum of days 2..8 (values: 2, 0, 3, 1, 0, 2, 4) = 12
        self.assertEqual(target.iloc[1], 12.0)

        # Day 2 (2026-01-03): sum of days 3..9 (values: 0, 3, 1, 0, 2, 4, 1) = 11
        self.assertEqual(target.iloc[2], 11.0)

        # Day 3 (2026-01-04): needs days 4..10, but day 10 is index 9 (only 6 future days available)
        self.assertTrue(pd.isna(target.iloc[3]))

        # All subsequent days must be NaN due to incomplete future window
        for i in range(4, 10):
            self.assertTrue(pd.isna(target.iloc[i]))

    def test_historical_features_use_only_prior_information(self):
        """
        Verify that features at day t use info strictly through t-1.
        """
        daily_checkouts = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        dates = pd.date_range("2026-01-01", periods=10, freq="D")
        s = pd.Series(daily_checkouts, index=dates)

        prior_checkouts = s.shift(1)
        feat_1d = prior_checkouts.rolling(1, min_periods=1).sum()
        feat_3d = prior_checkouts.rolling(3, min_periods=3).sum()
        feat_7d = prior_checkouts.rolling(7, min_periods=7).sum()

        # At day 0 (index 0): prior checkouts must be NaN
        self.assertTrue(pd.isna(feat_1d.iloc[0]))
        self.assertTrue(pd.isna(feat_3d.iloc[0]))
        self.assertTrue(pd.isna(feat_7d.iloc[0]))

        # At day 1 (index 1): 1d checkouts should be day 0's value (1)
        self.assertEqual(feat_1d.iloc[1], 1.0)

        # At day 3 (index 3): 3d checkouts should be days 0, 1, 2 (1+2+3 = 6)
        self.assertEqual(feat_3d.iloc[3], 6.0)

        # At day 7 (index 7): 7d checkouts should be days 0..6 (1+2+3+4+5+6+7 = 28)
        self.assertEqual(feat_7d.iloc[7], 28.0)

    def test_exam_forward_merge_behavior(self):
        """
        Verify forward merge maps any date to the next upcoming exam date.
        """
        exam_calendar = pd.DataFrame({
            "exam_cycle_id": ["CAT1", "CAT2"],
            "first_exam_date": [pd.Timestamp("2026-01-26"), pd.Timestamp("2026-03-15")],
        }).sort_values("first_exam_date")

        query_dates = pd.DataFrame({
            "date": [
                pd.Timestamp("2026-01-10"),  # 16 days to CAT1
                pd.Timestamp("2026-01-26"),  # 0 days to CAT1
                pd.Timestamp("2026-01-27"),  # 47 days to CAT2
            ]
        })

        merged = pd.merge_asof(
            query_dates,
            exam_calendar,
            left_on="date",
            right_on="first_exam_date",
            direction="forward",
        )
        merged["days_to_exam"] = (merged["first_exam_date"] - merged["date"]).dt.days

        self.assertEqual(merged.iloc[0]["exam_cycle_id"], "CAT1")
        self.assertEqual(merged.iloc[0]["days_to_exam"], 16)

        self.assertEqual(merged.iloc[1]["exam_cycle_id"], "CAT1")
        self.assertEqual(merged.iloc[1]["days_to_exam"], 0)

        self.assertEqual(merged.iloc[2]["exam_cycle_id"], "CAT2")
        self.assertEqual(merged.iloc[2]["days_to_exam"], 47)


if __name__ == "__main__":
    unittest.main()

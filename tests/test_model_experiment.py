"""Sanity checks for the optional learned-prior experiment."""

import math
import unittest

from ml_prior import learned_priors
from model_experiment import design_matrix, load_data


class LearnedPriorTest(unittest.TestCase):
    def test_training_data_and_predictions_are_finite(self):
        history, prices, scale = load_data()
        self.assertGreater(len(history), 10_000)
        self.assertEqual(len(history), design_matrix(history, prices, scale).shape[0])
        prior, fallback_conversion = learned_priors(None)
        self.assertTrue(prior)
        self.assertTrue(0 < fallback_conversion <= 1)
        for change, conversion, count in prior.values():
            self.assertTrue(math.isfinite(change))
            self.assertTrue(-1 <= change <= 3)
            self.assertTrue(0 < conversion <= 1)
            self.assertGreater(count, 0)


if __name__ == "__main__":
    unittest.main()

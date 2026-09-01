from __future__ import annotations

import unittest

from quorum.replay import ReplayStore


class ReplayStoreTest(unittest.TestCase):
    def test_replay_is_retrievable_and_explicitly_synthetic(self) -> None:
        store = ReplayStore()

        snapshot = store.start()

        self.assertIs(store.get(snapshot.replay_id), snapshot)
        self.assertEqual(snapshot.dataset_id, "synthetic_week_v1")
        self.assertEqual(snapshot.data_classification, "synthetic")
        self.assertIn("not a measured real-world outcome", snapshot.disclaimer)
        self.assertEqual(snapshot.interrupt_budget_limit_per_person, 2)
        self.assertEqual(snapshot.people_interrupted, 3)
        self.assertEqual(snapshot.max_interruptions_per_person, 2)
        self.assertEqual(snapshot.baseline.message_count, 214)
        self.assertEqual(snapshot.quorum.interruption_count, 6)
        self.assertEqual(snapshot.quorum.decision_latency_p50_hours, 7.0)

    def test_each_start_has_an_independent_opaque_id(self) -> None:
        store = ReplayStore()

        first = store.start()
        second = store.start()

        self.assertNotEqual(first.replay_id, second.replay_id)
        self.assertIsNone(store.get("replay_missing"))

    def test_public_store_evicts_old_runs_instead_of_growing_without_bound(self) -> None:
        store = ReplayStore(max_runs=2)

        first = store.start()
        second = store.start()
        third = store.start()

        self.assertIsNone(store.get(first.replay_id))
        self.assertIs(store.get(second.replay_id), second)
        self.assertIs(store.get(third.replay_id), third)

    def test_store_rejects_a_non_positive_capacity(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_runs"):
            ReplayStore(max_runs=0)


if __name__ == "__main__":
    unittest.main()

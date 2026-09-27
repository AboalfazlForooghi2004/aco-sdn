import tempfile
import unittest
from pathlib import Path

from controller.transaction_journal import TransactionJournal


class TransactionJournalTests(unittest.TestCase):
    def test_unresolved_uses_latest_transaction_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TransactionJournal(
                Path(directory) / "transactions.jsonl"
            )
            journal.append(
                transaction_id="tx1",
                status="install_pending",
                occurred_at=1,
            )
            journal.append(
                transaction_id="tx2",
                status="rollback_pending",
                occurred_at=2,
            )
            journal.append(
                transaction_id="tx1",
                status="committed",
                occurred_at=3,
            )

            self.assertEqual(journal.unresolved(), ("tx2",))


if __name__ == "__main__":
    unittest.main()
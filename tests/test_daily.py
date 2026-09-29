import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daily import database, ingest, candidates, deliver, send_text, import_email_history, requeue_unclassified_internships


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.db = database(":memory:")

    def tearDown(self):
        self.db.close()

    def add(self, email="jobs@example.com", **changes):
        ingest(self.db, [{"text": "Node.js role Ahmedabad apply " + email}])
        score = dict(type="hiring", eligible=True, score=1, apply_email=email, is_internship=False)
        score.update(changes)
        self.db.execute("UPDATE posts SET score=?", (json.dumps(score),))
        self.db.commit()

    def test_low_score_hiring_included(self):
        self.add()
        self.assertEqual(list(candidates(self.db)), ["jobs@example.com"])

    def test_seeker_excluded(self):
        self.add(type="seeker")
        self.assertFalse(candidates(self.db))

    def test_internship_excluded_even_if_model_marks_eligible(self):
        self.add(is_internship=True, score=10)
        self.assertFalse(candidates(self.db))

    def test_missing_or_invalid_internship_flag_excluded(self):
        for value in (None, "false", 0):
            self.add(is_internship=value)
            self.assertFalse(candidates(self.db))

    def test_legacy_unsent_candidate_requeued(self):
        self.add(is_internship=None)
        requeue_unclassified_internships(self.db)
        self.assertIsNone(self.db.execute("SELECT score FROM posts").fetchone()[0])

    def test_legacy_history_preserved(self):
        self.add(is_internship=None)
        self.db.execute("INSERT INTO email_history VALUES ('jobs@example.com', 'previous')")
        requeue_unclassified_internships(self.db)
        self.assertIsNotNone(self.db.execute("SELECT score FROM posts").fetchone()[0])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM email_history").fetchone()[0], 1)

    def test_internship_not_exported_or_sent(self):
        self.add(is_internship=True)
        with tempfile.TemporaryDirectory() as folder, patch("daily.telegram") as send:
            deliver(self.db, Path(folder), "complete")
            self.assertEqual((Path(folder) / "unique_emails.txt").read_text(), "")
            self.assertNotIn("jobs@example.com", str(send.call_args_list))

    def test_uncertain_location_excluded(self):
        self.add(eligible=False, uncertain_remote=True)
        self.assertFalse(candidates(self.db))

    def test_invalid_and_invented_email_excluded(self):
        self.add(email="info@company@gmail.com")
        self.assertFalse(candidates(self.db))
        self.db.execute("UPDATE posts SET score=?", (json.dumps(dict(type="hiring", eligible=True, score=9, apply_email="invented@example.com")),))
        self.assertFalse(candidates(self.db))

    def test_normalized_duplicates(self):
        ingest(self.db, [{"text": "Node.js Job"}, {"text": "NODE.JS  job"}])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 1)

    def test_failed_delivery_remains_pending(self):
        self.add()
        with tempfile.TemporaryDirectory() as folder, patch("daily.telegram", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                deliver(self.db, Path(folder), "complete")
        self.assertEqual(len(candidates(self.db)), 1)

    def test_confirmed_delivery_not_repeated(self):
        self.add()
        with tempfile.TemporaryDirectory() as folder, patch("daily.telegram", return_value={"ok": True}):
            deliver(self.db, Path(folder), "complete")
        self.assertFalse(candidates(self.db))

    def test_local_only_does_not_deliver(self):
        self.add()
        with tempfile.TemporaryDirectory() as folder, patch("daily.telegram") as send:
            deliver(self.db, Path(folder), "complete", send_telegram=False)
            send.assert_not_called()
            self.assertEqual((Path(folder) / "unique_emails.txt").read_text().strip(), "jobs@example.com")
        self.assertFalse(candidates(self.db))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM delivered").fetchone()[0], 0)

    def test_text_only_delivery(self):
        self.add()
        with tempfile.TemporaryDirectory() as folder, patch("daily.telegram", return_value={"ok": True}) as send:
            deliver(self.db, Path(folder), "complete")
        self.assertTrue(all(call.args[0] == "sendMessage" for call in send.call_args_list))
        self.assertIn("jobs@example.com", send.call_args_list[0].args[1]["text"])

    def test_long_text_split(self):
        text = "x" * 4000
        with patch("daily.telegram") as send:
            send_text(text)
        chunks = [call.args[1]["text"] for call in send.call_args_list]
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 1800 for chunk in chunks))

    def test_report_failure_does_not_resend_accepted_emails(self):
        self.add()
        with tempfile.TemporaryDirectory() as folder, patch("daily.telegram", side_effect=[{"ok": True}, RuntimeError("failed")]):
            with self.assertRaises(RuntimeError):
                deliver(self.db, Path(folder), "complete")
        self.assertFalse(candidates(self.db))

    def test_previous_results_case_insensitive(self):
        self.add()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "logs").mkdir()
            (root / "logs" / "results_old.json").write_text(json.dumps([{"apply_email": " JOBS@EXAMPLE.COM "}]))
            import_email_history(self.db, root, root / "data")
            self.assertFalse(candidates(self.db))
            (root / "logs" / "results_old.json").unlink()
            import_email_history(self.db, root, root / "data")
            self.assertFalse(candidates(self.db))

    def test_root_results_and_previous_daily_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "data"
            folder.mkdir()
            (root / "results.json").write_text(json.dumps([{"apply_email": "old@example.com"}]))
            (folder / "unique_emails.txt").write_text("previous@example.com\n")
            import_email_history(self.db, root, folder)
        self.assertEqual({r[0] for r in self.db.execute("SELECT email FROM email_history")}, {"old@example.com", "previous@example.com"})

    def test_same_email_new_post_is_excluded(self):
        self.add()
        with tempfile.TemporaryDirectory() as folder, patch("daily.telegram", return_value={"ok": True}):
            deliver(self.db, Path(folder), "complete")
        self.add(email="JOBS@EXAMPLE.COM")
        self.assertFalse(candidates(self.db))

    def test_delivery_rechecks_new_historical_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "data"
            folder.mkdir()
            (root / "logs").mkdir()
            import_email_history(self.db, root, folder)
            self.add()
            (root / "logs" / "results_added.json").write_text(json.dumps([{"apply_email": "jobs@example.com"}]))
            with patch("daily.ROOT", root), patch("daily.telegram"):
                deliver(self.db, folder, "complete")
            self.assertEqual((folder / "unique_emails.txt").read_text(), "")

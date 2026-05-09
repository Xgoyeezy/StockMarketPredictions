from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from backend.services import job_queue_service


class JobWorkerHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self._originals = {
            "thread": job_queue_service._WORKER_THREAD,
            "last_loop_at": job_queue_service._WORKER_LAST_LOOP_AT,
            "last_success_at": job_queue_service._WORKER_LAST_SUCCESS_AT,
            "last_error_at": job_queue_service._WORKER_LAST_ERROR_AT,
            "last_error_message": job_queue_service._WORKER_LAST_ERROR_MESSAGE,
            "current_stage": job_queue_service._WORKER_CURRENT_STAGE,
            "current_stage_started_at": job_queue_service._WORKER_CURRENT_STAGE_STARTED_AT,
            "last_stage_completed_at": job_queue_service._WORKER_LAST_STAGE_COMPLETED_AT,
            "last_stage_summary": job_queue_service._WORKER_LAST_STAGE_SUMMARY,
            "background_stage_threads": dict(job_queue_service._WORKER_BACKGROUND_STAGE_THREADS),
            "background_stage_started_at": dict(job_queue_service._WORKER_BACKGROUND_STAGE_STARTED_AT),
        }
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        job_queue_service._WORKER_THREAD = self._originals["thread"]
        job_queue_service._WORKER_LAST_LOOP_AT = self._originals["last_loop_at"]
        job_queue_service._WORKER_LAST_SUCCESS_AT = self._originals["last_success_at"]
        job_queue_service._WORKER_LAST_ERROR_AT = self._originals["last_error_at"]
        job_queue_service._WORKER_LAST_ERROR_MESSAGE = self._originals["last_error_message"]
        job_queue_service._WORKER_CURRENT_STAGE = self._originals["current_stage"]
        job_queue_service._WORKER_CURRENT_STAGE_STARTED_AT = self._originals["current_stage_started_at"]
        job_queue_service._WORKER_LAST_STAGE_COMPLETED_AT = self._originals["last_stage_completed_at"]
        job_queue_service._WORKER_LAST_STAGE_SUMMARY = self._originals["last_stage_summary"]
        job_queue_service._WORKER_BACKGROUND_STAGE_THREADS = self._originals["background_stage_threads"]
        job_queue_service._WORKER_BACKGROUND_STAGE_STARTED_AT = self._originals["background_stage_started_at"]

    def test_worker_status_reports_running_but_stale_stage(self) -> None:
        now = job_queue_service._utc_now()
        job_queue_service._WORKER_THREAD = SimpleNamespace(name="stock-signals-job-worker", is_alive=lambda: True)
        job_queue_service._WORKER_LAST_LOOP_AT = now - timedelta(seconds=10)
        job_queue_service._WORKER_CURRENT_STAGE = "trade_automation_cycles"
        job_queue_service._WORKER_CURRENT_STAGE_STARTED_AT = now - timedelta(seconds=120)

        status = job_queue_service.get_job_worker_status()

        self.assertTrue(status["running"])
        self.assertTrue(status["stale"])
        self.assertEqual(status["status"], "running_but_stale")
        self.assertEqual(status["current_stage"], "trade_automation_cycles")
        self.assertGreaterEqual(status["current_stage_age_seconds"], 120)

    def test_worker_stage_failure_does_not_prevent_later_stage(self) -> None:
        def fail_stage():
            raise RuntimeError("first stage failed")

        with patch.object(job_queue_service.logger, "exception"):
            failed = job_queue_service._run_worker_stage("first", fail_stage)
        succeeded = job_queue_service._run_worker_stage("second", lambda: {"processed": 1})

        self.assertFalse(failed)
        self.assertTrue(succeeded)
        self.assertEqual(job_queue_service._WORKER_LAST_STAGE_SUMMARY["stage"], "second")
        self.assertEqual(job_queue_service._WORKER_LAST_STAGE_SUMMARY["status"], "succeeded")
        self.assertEqual(job_queue_service._WORKER_LAST_STAGE_SUMMARY["result"]["processed"], 1)

    def test_background_stage_reports_already_running_without_blocking_loop(self) -> None:
        job_queue_service._WORKER_THREAD = SimpleNamespace(name="stock-signals-job-worker", is_alive=lambda: True)
        thread = SimpleNamespace(name="stock-signals-trade_automation_cycles", is_alive=lambda: True)
        job_queue_service._WORKER_BACKGROUND_STAGE_THREADS["trade_automation_cycles"] = thread
        job_queue_service._WORKER_BACKGROUND_STAGE_STARTED_AT["trade_automation_cycles"] = (
            job_queue_service._utc_now() - timedelta(seconds=120)
        )

        result = job_queue_service._start_worker_background_stage("trade_automation_cycles", lambda: {"processed": 1})
        status = job_queue_service.get_job_worker_status()

        self.assertEqual(result["status"], "already_running")
        self.assertIn("supervised background thread", result["detail"])
        self.assertFalse(status["stale"])
        self.assertEqual(status["status"], "running")
        self.assertTrue(status["background_stage_stale"])
        self.assertGreaterEqual(status["background_stale_seconds"], 120)
        self.assertGreaterEqual(status["background_stages"]["trade_automation_cycles"]["age_seconds"], 120)

    def test_sqlite_lock_commit_helper_rolls_back_and_retries(self) -> None:
        class FakeSession:
            def __init__(self) -> None:
                self.flush_count = 0
                self.commit_count = 0
                self.rollback_count = 0

            def flush(self) -> None:
                self.flush_count += 1
                if self.flush_count == 1:
                    raise OperationalError("update async_jobs set status='running'", {}, Exception("database is locked"))

            def commit(self) -> None:
                self.commit_count += 1

            def rollback(self) -> None:
                self.rollback_count += 1

        session = FakeSession()
        with patch.object(job_queue_service.time, "sleep"):
            job_queue_service._flush_commit_with_sqlite_retry(session, context="test async job lock")

        self.assertEqual(session.rollback_count, 1)
        self.assertEqual(session.flush_count, 2)
        self.assertEqual(session.commit_count, 1)


if __name__ == "__main__":
    unittest.main()

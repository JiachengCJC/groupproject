"""Durable PostgreSQL/SQLite jobs with leases and fenced completion.

The database row is both the transactional outbox and queue. Workers poll it;
there is no gap between committing an analysis and enqueuing its work.
"""
import logging
import threading
import time
import uuid

from .database import Database

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self,db,settings):
        self.db,self.settings=db,settings
        self.worker_id="worker_"+uuid.uuid4().hex
        self.stop_event=threading.Event()
        self.thread=None
        self.last_scheduler=0

    def start(self):
        self.thread=threading.Thread(target=self.run,name="market-lens-worker",daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=5)

    def _log_failure(self,message,exc,task=None):
        # Exception chains can contain provider response bodies and secrets.
        # Content logging is opt-in; default logs retain only diagnostic IDs.
        logger.error("%s worker=%s task=%s kind=%s error_type=%s",message,self.worker_id,
                     task["id"] if task else "-",task["kind"] if task else "-",
                     type(exc).__name__,exc_info=self.settings.trace_content)

    def run(self):
        while not self.stop_event.is_set():
            try:
                if time.monotonic()-self.last_scheduler>10:
                    try:
                        from .extensions import enqueue_due_schedules
                    except ImportError:
                        pass
                    else:
                        enqueue_due_schedules(self.db,self.settings)
                    self.last_scheduler=time.monotonic()
                if not self.run_once():self.stop_event.wait(self.settings.worker_poll_seconds)
            except Exception as exc:
                self._log_failure("Worker dispatch failed",exc)
                self.stop_event.wait(max(1,self.settings.worker_poll_seconds))

    def run_once(self):
        task=self.db.claim_task(self.worker_id,self.settings.job_lease_seconds)
        if not task:return False
        scoped=self.db.scoped(task["workspace_id"],task["owner_id"])
        scoped._lease=(task["id"],task["lease_token"])
        finished=threading.Event()
        def heartbeat():
            while not finished.wait(max(1,self.settings.job_lease_seconds/3)):
                try:
                    if not scoped.heartbeat_task(task["id"],task["lease_token"],self.settings.job_lease_seconds):return
                except Exception as exc:
                    self._log_failure("Task heartbeat failed",exc,task)
        pulse=threading.Thread(target=heartbeat,name="job-heartbeat",daemon=True)
        pulse.start()
        try:
            if task["kind"]=="analysis":
                from .engine import Engine
                run=scoped.run(task["payload"]["run_id"])
                if run["status"] in {"queued","running"}:
                    Engine(scoped,self.settings).run_job(run["id"])
                    run=scoped.run(run["id"])
                if run["status"]=="failed":
                    scoped.finish_task(task["id"],task["lease_token"],error=run["error"])
                elif run["status"]=="cancelled":
                    pass
                else:
                    scoped.finish_task(task["id"],task["lease_token"],result={"run_id":run["id"],"status":run["status"]})
            elif task["kind"]=="upload":
                from .ingestion import ingest
                from .providers import Providers
                payload=task["payload"]
                original=scoped.upload(payload["upload_id"])
                result=ingest(scoped,self.settings,Providers(self.settings),original["filename"],original["content"],
                              dataset_id=payload.get("dataset_id"),category=payload.get("category","General"),
                              vision_provider=payload.get("vision_provider","none"),vision_model=payload.get("vision_model"))
                scoped.finish_task(task["id"],task["lease_token"],result=result)
            else:
                from .extensions import execute_job
                result=execute_job(task["kind"],task["payload"],scoped,self.settings)
                scoped.finish_task(task["id"],task["lease_token"],result=result)
        except Exception as exc:
            self._log_failure("Task execution failed",exc,task)
            # Do not expose provider payloads, credentials or untrusted exception content.
            error="Task execution failed. Check the task configuration and server diagnostic IDs before retrying."
            scoped.finish_task(task["id"],task["lease_token"],error=error,retryable=False)
        finally:
            finished.set();pulse.join(timeout=2)
        return True

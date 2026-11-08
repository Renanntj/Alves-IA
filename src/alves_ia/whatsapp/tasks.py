from datetime import UTC, datetime, timedelta

from celery import Celery
from sqlalchemy import select
from sqlalchemy.orm import Session

from alves_ia.config import get_settings
from alves_ia.database import get_engine, set_clinic

from .models import ChannelRoute, Outbox
from .processing import process_job

app = Celery("alves_ia", broker=get_settings().redis_url.get_secret_value())
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    beat_schedule={"outbox": {"task": "whatsapp.dispatch", "schedule": 10.0}},
)


def dispatch() -> None:
    with Session(get_engine()) as session:
        clinics = list(session.scalars(select(ChannelRoute.clinic_id).distinct()))
    for clinic_id in clinics:
        with Session(get_engine()) as session, session.begin():
            set_clinic(session, clinic_id)
            now = datetime.now(UTC)
            stale = session.scalars(
                select(Outbox)
                .where(
                    Outbox.state == "processing",
                    Outbox.available_at < now - timedelta(minutes=2),
                )
                .limit(100)
                .with_for_update(skip_locked=True)
            )
            for row in stale:
                row.state, row.error_code = "unknown", "interrupted_attempt"
            jobs = session.scalars(
                select(Outbox)
                .where(
                    Outbox.state == "pending",
                    Outbox.available_at <= now,
                )
                .order_by(Outbox.available_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
            for job in jobs:
                app.send_task("whatsapp.process", args=[str(clinic_id), str(job.id)])
                job.available_at = now + timedelta(seconds=60)


app.task(name="whatsapp.dispatch")(dispatch)
app.task(name="whatsapp.process")(process_job)

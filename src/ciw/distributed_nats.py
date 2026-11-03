"""JetStream wake-ups for PostgreSQL custody; never a second job authority.

SPDX-License-Identifier: AGPL-3.0-or-later
The trusted host supplies an already authenticated JetStream context. This
module creates no accounts, credentials, connections, streams or consumers until
the host explicitly invokes the corresponding provisioning/binding method.
"""
from __future__ import annotations

import asyncio
from hashlib import sha256
import re

from .distributed_queue import PostgresQueue, QueueRefusal, _TERMINAL, _require


class JetStreamBridge:
    """Transactional outbox publisher and durable pull-consumer adapter.

    PostgreSQL makes execution idempotent permanently. Nats-Msg-Id only reduces
    duplicate notifications inside the broker's finite deduplication window.
    A broker acknowledgement never authorizes or completes an execution.
    """
    def __init__(self, queue, jetstream, *, stream, durable, replicas=1, ack_wait=30):
        _require(isinstance(queue, PostgresQueue), "distributed_postgres_queue_required")
        for value in (stream, durable):
            _require(type(value) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value),
                     "distributed_nats_name_invalid")
        _require(type(replicas) is int and 1 <= replicas <= 3, "distributed_nats_replicas_invalid")
        _require(type(ack_wait) in (int, float) and 0.2 <= ack_wait <= 300, "distributed_nats_ack_wait_invalid")
        self.queue, self.js, self.stream, self.durable = queue, jetstream, stream, durable
        self.replicas, self.ack_wait = replicas, float(ack_wait)
        self.subject = "odsp." + queue.queue_id[2:] + ".ready"
        self.subscription = None

    def _stream_config(self):
        from nats.js.api import StreamConfig, RetentionPolicy, StorageType, DiscardPolicy
        return StreamConfig(name=self.stream, subjects=[self.subject], retention=RetentionPolicy.WORK_QUEUE,
                            storage=StorageType.FILE, discard=DiscardPolicy.NEW,
                            max_msgs=self.queue.limits.max_jobs, max_bytes=self.queue.limits.max_jobs * 2048,
                            max_msg_size=1024, max_age=0, max_consumers=1,
                            num_replicas=self.replicas, duplicate_window=120)

    def _consumer_config(self):
        from nats.js.api import ConsumerConfig, AckPolicy, DeliverPolicy, ReplayPolicy
        return ConsumerConfig(durable_name=self.durable, filter_subject=self.subject,
                              ack_policy=AckPolicy.EXPLICIT, ack_wait=self.ack_wait,
                              deliver_policy=DeliverPolicy.ALL, replay_policy=ReplayPolicy.INSTANT,
                              max_deliver=-1, max_ack_pending=self.queue.limits.max_running,
                              max_waiting=64)

    def _check_stream(self, config):
        expected = self._stream_config()
        for field in ("name", "subjects", "retention", "storage", "discard", "max_msgs", "max_bytes",
                      "max_msg_size", "max_age", "max_consumers", "num_replicas", "duplicate_window"):
            _require(getattr(config, field) == getattr(expected, field), "distributed_nats_stream_config_mismatch")
        # A source, mirror, republish destination, transform or purge policy can
        # violate the isolated notification contract even when subjects match.
        for field in ("sources", "mirror", "republish", "subject_transform"):
            _require(not getattr(config, field, None), "distributed_nats_stream_config_mismatch")

    def _check_consumer(self, config):
        expected = self._consumer_config()
        for field in ("durable_name", "filter_subject", "ack_policy", "ack_wait", "deliver_policy",
                      "replay_policy", "max_deliver", "max_ack_pending", "max_waiting"):
            _require(getattr(config, field) == getattr(expected, field), "distributed_nats_consumer_config_mismatch")
        _require(not config.deliver_subject and not getattr(config, "filter_subjects", None)
                 and not getattr(config, "backoff", None), "distributed_nats_consumer_config_mismatch")

    async def provision(self):
        """Explicit admin action for one already authorized stream and durable.

        Existing resources are verified, never overwritten. Runtime workers
        should use bind() with stream/consumer creation permissions withheld.
        """
        from nats.js.errors import NotFoundError
        try:
            stream = await self.js.stream_info(self.stream)
        except NotFoundError:
            stream = await self.js.add_stream(config=self._stream_config())
        self._check_stream(stream.config)
        try:
            consumer = await self.js.consumer_info(self.stream, self.durable)
        except NotFoundError:
            consumer = await self.js.add_consumer(self.stream, config=self._consumer_config())
        self._check_consumer(consumer.config)
        await self.bind()

    async def bind(self):
        """Read back the exact isolation/custody contract before consuming."""
        self._check_stream((await self.js.stream_info(self.stream)).config)
        self._check_consumer((await self.js.consumer_info(self.stream, self.durable)).config)
        self.subscription = await self.js.pull_subscribe_bind(durable=self.durable, stream=self.stream)

    async def publish_pending(self, *, limit=16):
        """Only a confirmed PubAck permits marking this outbox row published.

        A crash/timeout between publish and database update leaves it pending.
        Subsequent publication uses exactly the same immutable ID and payload.
        """
        self._check_stream((await self.js.stream_info(self.stream)).config)
        rows = await asyncio.to_thread(self.queue.pending_notifications, limit=limit)
        count = 0
        for row in rows:
            _require(sha256(row["payload"]).hexdigest() == row["event_id"], "distributed_outbox_integrity")
            ack = await self.js.publish(self.subject, row["payload"], stream=self.stream,
                                        headers={"Nats-Msg-Id": row["event_id"]}, timeout=5)
            _require(ack.stream == self.stream and type(ack.seq) is int and ack.seq > 0,
                     "distributed_publish_ack_invalid")
            await asyncio.to_thread(self.queue.notification_published, row["event_id"],
                                    stream=ack.stream, sequence=ack.seq)
            count += 1
        return count

    async def process_one(self, worker, dispatch, *, timeout=1):
        """Fetch at most one notification; ack only after a durable terminal read.

        A concurrent claim, revoked permission, unavailable database, cancellation
        or uncertain broker ack leaves a retryable notification, never permission
        to replay a provider. Redelivery consults PostgreSQL again.
        """
        from nats.errors import TimeoutError as NatsTimeout
        _require(self.subscription is not None, "distributed_nats_bind_required")
        _require(type(timeout) in (int, float) and 0 < timeout <= 30, "distributed_nats_timeout_invalid")
        try:
            messages = await self.subscription.fetch(batch=1, timeout=timeout)
        except NatsTimeout:
            return None
        message = messages[0]
        job = None
        try:
            _require(message.subject == self.subject, "distributed_event_wrong_subject")
            job = await asyncio.to_thread(self.queue.notification_job, message.data)
            # Lost workers become unknown. This is custody recovery, never an
            # automatic retry, even if no provider was actually reached.
            await asyncio.to_thread(self.queue.recover)
            job = await asyncio.to_thread(self.queue.inspect, job["job_id"])
            if job["status"] not in _TERMINAL:
                await asyncio.to_thread(self.queue.execute_one, worker, dispatch, job_id=job["job_id"])
                job = await asyncio.to_thread(self.queue.inspect, job["job_id"])
            if job["status"] in _TERMINAL:
                await message.ack_sync(timeout=5)
                return job
            await message.nak(delay=min(self.ack_wait, 5))
            return {"status": "notification_pending", "job_id": job["job_id"], "authorizes_execution": False}
        except BaseException:
            # In particular, never change a committed completion to unknown
            # after an ack failure, and never retry a canonical invocation here.
            try:
                if job is not None:
                    job = await asyncio.to_thread(self.queue.inspect, job["job_id"])
                    if job["status"] in _TERMINAL:
                        await message.ack_sync(timeout=5)
                    else:
                        await message.nak(delay=min(self.ack_wait, 5))
                else:
                    await message.nak(delay=min(self.ack_wait, 5))
            except Exception:
                pass  # No ack => JetStream's normal redelivery/reconciliation.
            raise

    async def cycle(self, worker, dispatch, *, timeout=1):
        """One host-supervised reconciliation/publication/consumption cycle.

        Missing broker resources refuse until an authorized administrator
        provisions them. Every cycle recovers expired claims as unknown and
        republishes still-queued wake-ups, closing the broker-loss gap without
        treating the broker's finite deduplication window as execution authority.
        """
        await asyncio.to_thread(self.queue.recover)
        await asyncio.to_thread(self.queue.reconcile_notifications)
        await self.publish_pending()
        return await self.process_one(worker, dispatch, timeout=timeout)

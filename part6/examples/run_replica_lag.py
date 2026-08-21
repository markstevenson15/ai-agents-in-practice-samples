"""Demonstration of Read-Replica Replication Lag Hazard.

Scenario:
In distributed cloud architectures (e.g., Azure Cosmos DB multi-region, PostgreSQL
read replicas, AWS Aurora reader instances), read queries are directed to read
replicas to avoid loading the primary writer.

Replicas lag behind the primary by replication delays (e.g. 50ms to 5000ms).

When an agent executes an asynchronous cancellation:
1. The mutation is committed to the PRIMARY database (`cancelled`).
2. The agent executes `wait_and_recheck` against a READ REPLICA.
3. The read replica returns stale data (`pending` or `open`) during the lag window.
4. If the agent's retry budget is too tight or lacks causal consistency tokens,
   the agent concludes verification failed and triggers a false human escalation,
   creating alert fatigue.

Production Fix: Authoritative re-reads must bypass read replicas (primary routing)
or pass causal consistency session tokens (e.g. Cosmos DB Session Consistency / ETag).
"""

from __future__ import annotations

import sys
from pathlib import Path

PART6 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PART6))

from loop import Budget, DeterministicDecider, load_skill, run_agent_loop  # noqa: E402
from tools import OrderStore, RefundStore, Tools  # noqa: E402

ORDER_ID = "TN-100457"


class LaggingReadReplicaOrderStore(OrderStore):
    """Simulates a database architecture with a Primary writer and a Lagging Read Replica."""

    def __init__(self, order_id: str, lag_reads: int = 3) -> None:
        super().__init__(order_id, settle_after_reads=0, settles_to="cancelled")
        self.lag_reads = lag_reads
        self._replica_read_count = 0
        self.primary_status = "open"

    def cancel_order(self, order_id: str, idempotency_key: str) -> dict:
        res = super().cancel_order(order_id, idempotency_key)
        # Primary writer immediately updates to "cancelled":
        self.primary_status = "cancelled"
        return res

    def get_order_status(self, order_id: str) -> dict:
        """Reads from the LAGGY READ REPLICA."""
        if not self._cancel_accepted:
            return {"order_id": order_id, "status": "open"}
        self._replica_read_count += 1
        if self._replica_read_count <= self.lag_reads:
            # Replica still reports stale status ('pending') after cancellation:
            return {"order_id": order_id, "status": "pending", "replica_lag": True}
        # Replica finally caught up with primary:
        return {"order_id": order_id, "status": self.primary_status, "replica_lag": False}


def main() -> None:
    # Set up store where primary commits immediately, but replica lags for 10 reads
    laggy_order_store = LaggingReadReplicaOrderStore(ORDER_ID, lag_reads=10)
    refund_store = RefundStore(ORDER_ID, settle_after_reads=0, order_reader=lambda: {"status": laggy_order_store.primary_status})
    tools = Tools(order=laggy_order_store, refund=refund_store)

    print("Running Agent Loop with step budget (max_steps=5) against Lagging Replica (lag=10 reads)...")
    state, trace = run_agent_loop(
        tools,
        DeterministicDecider(),
        order_id=ORDER_ID,
        skill=load_skill(),
        scenario="read_replica_lag",
        verify_enabled=True,
        budget=Budget(max_steps=5),
        retries=1,
        backoff=0.0,
    )

    print("\n=================================================================")
    print("READ REPLICA LAG RESULTS:")
    print("=================================================================")
    print(f"  Agent Loop Stop Reason    : {trace.stop_reason}")
    print(f"  Agent Belief State        : cancellation={state.cancellation_status}, refund={state.refund_status}")
    print(f"  Primary DB True Status    : {laggy_order_store.primary_status} (Already CANCELLED on primary!)")
    print(f"  Replica Stale Read Count  : {laggy_order_store._replica_read_count}")
    print("=================================================================")

    if laggy_order_store.primary_status == "cancelled" and state.cancellation_status != "cancelled":
        print("🚨 FALSE ESCALATION / TIMEOUT CONFIRMED:")
        print("   The Primary DB committed the cancellation, but the agent polled a lagging")
        print("   read replica and exhausted its retry budget, triggering a false human escalation.")
        print("\n   -> Production Fix: Re-reads must use causal consistency tokens (e.g. Cosmos DB")
        print("      session tokens) or route directly to the primary writer.")


if __name__ == "__main__":
    main()

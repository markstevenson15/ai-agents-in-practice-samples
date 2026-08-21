"""Demonstration of TOCTOU (Time-of-Check to Time-of-Use) Race Hazard.

Scenario:
Even with the SAFE verify-before-commit agent loop enabled (verify_enabled=True),
client-side verification only observes a momentary point-in-time snapshot.
If an external event (such as a warehouse fulfillment scan or customer service
override) mutates the order status AFTER the agent re-reads "cancelled" but
BEFORE the agent triggers "issue_refund", the agent proceeds to issue a refund
on an order that is no longer cancelled.

This demonstrates why client-side agent verification MUST be paired with backend
database-level conditional atomic transactions (e.g. UPDATE orders WHERE status='cancelled').
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the part6 packages importable no matter where this is launched from.
PART6 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PART6))

from loop import Budget, DeterministicDecider, load_skill, run_agent_loop  # noqa: E402
from tools import OrderStore, Tools, UnsafeRefundStore  # noqa: E402

ORDER_ID = "TN-100457"


def main() -> None:
    # 1. Order store settles to 'cancelled' on the first re-read:
    order_store = OrderStore(ORDER_ID, settle_after_reads=1, settles_to="cancelled")

    # 2. In microservices without cross-domain distributed locks, the refund service
    # accepts refund requests on valid accounts (modeled by UnsafeRefundStore):
    refund_store = UnsafeRefundStore(ORDER_ID, settle_after_reads=1)

    tools = Tools(order=order_store, refund=refund_store)

    # 3. Simulate a concurrent external event (e.g., warehouse item shipment or admin override)
    # that occurs right after the agent verifies the order is cancelled:
    original_get_order = order_store.get_order_status

    def intercepted_get_order(oid: str) -> dict:
        result = original_get_order(oid)
        # As soon as the verification read observes "cancelled", the concurrent warehouse event fires:
        if result.get("status") == "cancelled":
            print("\n  [CONCURRENT EVENT] Warehouse scanner processes package onto delivery truck!")
            print("  [CONCURRENT EVENT] Order status mutated: 'cancelled' -> 'shipped'\n")
            order_store.status = "shipped"
        return result

    tools.order.get_order_status = intercepted_get_order

    print("Running Agent Loop with SAFE verification enabled (verify_enabled=True)...")
    state, trace = run_agent_loop(
        tools,
        DeterministicDecider(),
        order_id=ORDER_ID,
        skill=load_skill(),
        scenario="toctou_concurrency_race",
        verify_enabled=True,  # SAFE verify-before-commit is active!
        budget=Budget(max_steps=12),
        retries=1,
        backoff=0.0,
    )

    out = PART6 / "traces" / "toctou_trace.json"
    trace.write(out)

    print("\n" + "=" * 65)
    print("TOCTOU RUN RESULTS:")
    print("=" * 65)
    print(f"  Agent Loop Outcome        : {trace.stop_reason}")
    print(f"  Agent Cancellation State  : {state.cancellation_status} (Agent believed it was cancelled)")
    print(f"  Agent Refund State        : {state.refund_status} (Agent completed refund)")
    print(f"  Actual Real-World Status  : {order_store.status}")
    print(f"  Total Refunds Issued      : {tools.refund.refund_effect_count}")
    print("=" * 65)

    if order_store.status == "shipped" and tools.refund.refund_effect_count > 0:
        print("🚨 TOCTOU RACE CONFIRMED:")
        print("   The agent verified cancellation at Step T, but between T and T+1,")
        print("   the real world changed to 'shipped'. The agent issued a refund anyway.")
        print("   -> Lesson: Client-side loop verification does not replace backend ACID transactions!")
    print(f"\nTrace written to: {out}\n")


if __name__ == "__main__":
    main()

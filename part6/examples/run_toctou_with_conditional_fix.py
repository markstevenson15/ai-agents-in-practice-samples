"""Demonstration of the Production Fix for TOCTOU Concurrency Races.

Scenario:
In this scenario, the TOCTOU event occurs (warehouse scans package to 'shipped' right after
the agent re-reads 'cancelled').

HOWEVER, the backend refund service is implemented with production-grade CONDITIONAL
ATOMIC ENFORCEMENT (modeled by RefundStore with order_reader).

When the agent attempts to execute `issue_refund`, the backend database checks:
    WHERE order_id = 4471 AND status = 'cancelled'

Because the order is now 'shipped', the backend REJECTS the refund:
    {"status": "rejected", "reason": "order_not_cancelled"}

The agent loop detects this backend rejection, does NOT issue money, and cleanly
escalates to human support. Money is saved!
"""

from __future__ import annotations

import sys
from pathlib import Path

PART6 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PART6))

from loop import Budget, DeterministicDecider, load_skill, run_agent_loop  # noqa: E402
from tools import OrderStore, RefundStore, Tools  # noqa: E402

ORDER_ID = "TN-100457"


def main() -> None:
    order_store = OrderStore(ORDER_ID, settle_after_reads=1, settles_to="cancelled")

    # Production-grade RefundStore with authoritative backend conditional checking:
    refund_store = RefundStore(
        ORDER_ID,
        settle_after_reads=1,
        order_reader=lambda: order_store.peek_order_status(ORDER_ID),  # Authoritative check at mutation time!
    )

    tools = Tools(order=order_store, refund=refund_store)

    original_get_order = order_store.get_order_status

    def intercepted_get_order(oid: str) -> dict:
        result = original_get_order(oid)
        if result.get("status") == "cancelled":
            print("\n  [CONCURRENT EVENT] Warehouse scanner processes package onto delivery truck!")
            print("  [CONCURRENT EVENT] Order status mutated: 'cancelled' -> 'shipped'\n")
            order_store.settles_to = "shipped"
        return result

    tools.order.get_order_status = intercepted_get_order

    print("Running Agent Loop with Backend Conditional Enforcement Fix...")
    state, trace = run_agent_loop(
        tools,
        DeterministicDecider(),
        order_id=ORDER_ID,
        skill=load_skill(),
        scenario="toctou_with_conditional_fix",
        verify_enabled=True,
        budget=Budget(max_steps=12),
        retries=1,
        backoff=0.0,
    )

    out = PART6 / "traces" / "toctou_fixed_trace.json"
    trace.write(out)

    print("\n" + "=" * 65)
    print("CONDITIONAL FIX RUN RESULTS:")
    print("=" * 65)
    print(f"  Agent Loop Stop Reason    : {trace.stop_reason}")
    print(f"  Actual Real-World Status  : {order_store.settles_to}")
    print(f"  Total Refunds Issued      : {refund_store.refund_effect_count} (ZERO money lost!)")
    print(f"  Last Tool Result          : {state.last_tool_result}")
    print("=" * 65)

    if order_store.settles_to == "shipped" and refund_store.refund_effect_count == 0:
        print("✅ TOCTOU RACE SAFELY PREVENTED:")
        print("   Even though the agent loop thought the order was cancelled,")
        print("   the backend database caught the race condition at write-time and REJECTED the refund.")
        print("   -> Money was protected!")
    print(f"\nTrace written to: {out}\n")


if __name__ == "__main__":
    main()

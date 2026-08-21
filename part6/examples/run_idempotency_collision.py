"""Demonstration of Idempotency Key Collision Hazard.

Scenario:
The article uses static idempotency keys formatted as:
    key = f"{action}:{order_id}"  (e.g., "issue_refund:TN-100457")

In real-world e-commerce, an order may have:
1. Multiple partial refunds (e.g., partial return of item A, then item B).
2. A second distinct refund intent (e.g., customer support refunding shipping fee).
3. A subsequent workflow run for a separate charge on the same order.

If the agent generates static keys scoped only to the order ID, the second distinct
refund is treated by the payment backend as an IDEMPOTENT REPLAY of the first refund.
The backend returns {"status": "accepted", "idempotent_replay": True} without moving money.
The agent loop reports success, but the customer never receives their funds!

Fix: Idempotency keys must be composite: scoped to a unique Intent/Run ID
(e.g., f"{intent_id}:{action}:{order_id}").
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
    # Set up shared backend store for order and refunds
    order_store = OrderStore(ORDER_ID, settle_after_reads=0, settles_to="cancelled")
    refund_store = RefundStore(ORDER_ID, settle_after_reads=0, order_reader=lambda: order_store.peek_order_status(ORDER_ID))
    tools = Tools(order=order_store, refund=refund_store)
    decider = DeterministicDecider()
    skill = load_skill()

    print("=================================================================")
    print("RUN 1: Processing First Legitimate Refund (Item Return #1)")
    print("=================================================================")
    state1, trace1 = run_agent_loop(
        tools,
        decider,
        order_id=ORDER_ID,
        skill=skill,
        scenario="idempotency_run_1",
        verify_enabled=True,
        budget=Budget(max_steps=10),
    )
    print(f"  Run 1 Outcome         : {trace1.stop_reason}")
    print(f"  Run 1 Refund Status   : {state1.refund_status}")
    print(f"  Backend Payouts Count : {refund_store.refund_effect_count}")

    print("\n=================================================================")
    print("RUN 2: Processing Second Legitimate Refund (Shipping Fee Return)")
    print("       (Using static key `issue_refund:TN-100457`)")
    print("=================================================================")
    
    # Reset the refund settle status for run 2 so refund can be initiated again
    refund_store._issued = False
    refund_store._reads_since_issue = 0

    state2, trace2 = run_agent_loop(
        tools,
        decider,
        order_id=ORDER_ID,
        skill=skill,
        scenario="idempotency_run_2_static_key",
        verify_enabled=True,
        budget=Budget(max_steps=10),
    )

    print(f"  Run 2 Outcome         : {trace2.stop_reason}")
    print(f"  Run 2 Refund Status   : {state2.refund_status} (Agent reports COMPLETED!)")
    print(f"  Backend Payouts Count : {refund_store.refund_effect_count} (Should be 2, but is still 1!)")
    print("=================================================================")

    if refund_store.refund_effect_count == 1:
        print("🚨 IDEMPOTENCY KEY COLLISION CONFIRMED:")
        print("   Run 2 was treated as a duplicate replay of Run 1 because the key was static:")
        print(f"   Key: 'issue_refund:{ORDER_ID}'")
        print("   The customer NEVER received their second refund even though the agent reported success!")
        print("\n   -> Production Fix: Idempotency keys must be composite: f'{intent_id}:{action}:{order_id}'")


if __name__ == "__main__":
    main()

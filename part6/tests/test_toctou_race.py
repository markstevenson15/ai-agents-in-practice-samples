"""Tests demonstrating the Time-of-Check to Time-of-Use (TOCTOU) race condition."""

from loop import Budget, DeterministicDecider, load_skill, run_agent_loop
from loop.state import CancellationStatus as C
from loop.state import RefundStatus as R
from tools import OrderStore, Tools, UnsafeRefundStore

ORDER_ID = "TN-100457"


def test_toctou_concurrency_race_between_verify_and_refund():
    """Verify that even with verify_enabled=True, state changes between verification

    and the refund call can result in refunds on non-cancelled orders if the backend
    does not enforce conditional atomic mutations.
    """
    order_store = OrderStore(ORDER_ID, settle_after_reads=1, settles_to="cancelled")
    refund_store = UnsafeRefundStore(ORDER_ID, settle_after_reads=1)
    tools = Tools(order=order_store, refund=refund_store)

    original_get_order = order_store.get_order_status

    def intercepted_get_order(oid: str) -> dict:
        result = original_get_order(oid)
        if result.get("status") == "cancelled":
            # Concurrently mutate order in the real world immediately upon verification read
            order_store.status = "shipped"
        return result

    tools.order.get_order_status = intercepted_get_order

    state, trace = run_agent_loop(
        tools,
        DeterministicDecider(),
        order_id=ORDER_ID,
        skill=load_skill(),
        scenario="toctou_race",
        verify_enabled=True,
        budget=Budget(max_steps=10),
        retries=1,
        backoff=0.0,
    )

    # Agent's internal belief is that cancellation was observed and refund completed:
    assert state.cancellation_status == C.CANCELLED
    assert state.refund_status == R.COMPLETED
    assert trace.stop_reason == "terminal_action_reached"

    # But the real world status is shipped, and refund was paid out:
    assert order_store.status == "shipped"
    assert refund_store.refund_effect_count == 1

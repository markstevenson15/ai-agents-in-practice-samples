"""Test demonstrating the conditional execution fix for TOCTOU concurrency races."""

from loop import Budget, DeterministicDecider, load_skill, run_agent_loop
from tools import OrderStore, RefundStore, Tools

ORDER_ID = "TN-100457"


def test_backend_conditional_enforcement_prevents_toctou_payout():
    """Verify that backend conditional enforcement rejects a refund if the world

    state changed to 'shipped' concurrently after the agent's verification read.
    """
    order_store = OrderStore(ORDER_ID, settle_after_reads=1, settles_to="cancelled")
    refund_store = RefundStore(
        ORDER_ID,
        settle_after_reads=1,
        order_reader=lambda: order_store.peek_order_status(ORDER_ID),
    )
    tools = Tools(order=order_store, refund=refund_store)

    original_get_order = order_store.get_order_status

    def intercepted_get_order(oid: str) -> dict:
        result = original_get_order(oid)
        if result.get("status") == "cancelled":
            order_store.settles_to = "shipped"
        return result

    tools.order.get_order_status = intercepted_get_order

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

    assert trace.stop_reason == "refund_rejected_by_backend"
    assert order_store.settles_to == "shipped"
    # Zero money refunded:
    assert refund_store.refund_effect_count == 0
    assert state.last_tool_result["status"] == "rejected"

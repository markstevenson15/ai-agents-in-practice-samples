"""Test demonstrating the danger of static idempotency keys across multiple operations."""

from loop import Budget, DeterministicDecider, load_skill, run_agent_loop
from loop.state import RefundStatus as R
from tools import OrderStore, RefundStore, Tools

ORDER_ID = "TN-100457"


def test_static_idempotency_key_collides_on_subsequent_refund():
    """Verify that using static key `issue_refund:{order_id}` causes a second

    legitimate refund operation to be dropped as a replay by the payment store.
    """
    order_store = OrderStore(ORDER_ID, settle_after_reads=0, settles_to="cancelled")
    refund_store = RefundStore(ORDER_ID, settle_after_reads=0, order_reader=lambda: order_store.peek_order_status(ORDER_ID))
    tools = Tools(order=order_store, refund=refund_store)
    decider = DeterministicDecider()
    skill = load_skill()

    # Run 1: First refund succeeds
    state1, _ = run_agent_loop(
        tools,
        decider,
        order_id=ORDER_ID,
        skill=skill,
        scenario="idempotency_run_1",
        verify_enabled=True,
        budget=Budget(max_steps=10),
    )
    assert state1.refund_status == R.COMPLETED
    assert refund_store.refund_effect_count == 1

    # Run 2: Second distinct refund for same order (reset settle clock to simulate new intent)
    refund_store._issued = False
    refund_store._reads_since_issue = 0

    run_agent_loop(
        tools,
        decider,
        order_id=ORDER_ID,
        skill=skill,
        scenario="idempotency_run_2",
        verify_enabled=True,
        budget=Budget(max_steps=10),
    )

    # The backend was called with the exact same static key, so effect count is STILL 1
    assert refund_store.refund_effect_count == 1

"""Test demonstrating read-replica lag causing false loop timeouts."""

from loop import Budget, DeterministicDecider, load_skill, run_agent_loop
from loop.state import CancellationStatus as C
from tools import RefundStore, Tools
from examples.run_replica_lag import LaggingReadReplicaOrderStore

ORDER_ID = "TN-100457"


def test_read_replica_lag_causes_step_budget_exhaustion():
    """Verify that polling a lagging read replica exhausts the agent's budget

    even when the primary database committed the update.
    """
    laggy_order_store = LaggingReadReplicaOrderStore(ORDER_ID, lag_reads=10)
    refund_store = RefundStore(ORDER_ID, settle_after_reads=0, order_reader=lambda: {"status": laggy_order_store.primary_status})
    tools = Tools(order=laggy_order_store, refund=refund_store)

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

    # Primary succeeded:
    assert laggy_order_store.primary_status == "cancelled"
    # But agent exhausted budget while reading stale replica:
    assert trace.stop_reason == "step_cap_reached"
    assert state.cancellation_status == C.PENDING

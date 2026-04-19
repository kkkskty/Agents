"""intent_router：depends_on 字符串形态（task_0 / uuid:task_0）。"""

from app.agents.intent_router import IntentRouterAgent
from app.core.step_dag import global_step_id


def test_item_string_global_form_same_turn() -> None:
    ir = IntentRouterAgent()
    tid = "turn-a"
    ref = ir._item_to_step_ref(f"{tid}:task_0", turn_id=tid, max_local_index_exclusive=1)
    assert ref is not None
    assert ref.turn_id == tid
    assert ref.step_id == global_step_id(tid, "task_0")


def test_item_string_global_form_cross_turn() -> None:
    ir = IntentRouterAgent()
    prev = "prev-turn-uuid"
    cur = "cur-turn-uuid"
    ref = ir._item_to_step_ref(f"{prev}:task_0", turn_id=cur, max_local_index_exclusive=0)
    assert ref is not None
    assert ref.turn_id == prev
    assert ref.step_id == f"{prev}:task_0"

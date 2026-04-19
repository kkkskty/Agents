"""StepRef 同轮环检测与 step_id 工具。"""

import pytest

from app.core.step_dag import detect_cycle_same_turn, global_step_id, validate_same_turn_refs
from app.core.state import OrderTask, QueryTask, StepRef


def test_global_step_id() -> None:
    assert global_step_id("t-uuid", "task_0") == "t-uuid:task_0"


def test_validate_same_turn_refs_ok() -> None:
    tid = "turn-a"
    t0 = f"{tid}:task_0"
    t1 = f"{tid}:task_1"
    tasks = [
        QueryTask(id=t0, text="a", depends_on=[]),
        OrderTask(
            id=t1,
            text="b",
            depends_on=[StepRef(turn_id=tid, step_id=t0)],
        ),
    ]
    validate_same_turn_refs(tasks, tid)


def test_cycle_raises() -> None:
    tid = "turn-a"
    t0 = f"{tid}:task_0"
    t1 = f"{tid}:task_1"
    tasks = [
        QueryTask(id=t0, text="a", depends_on=[StepRef(tid, t1)]),
        QueryTask(id=t1, text="b", depends_on=[StepRef(tid, t0)]),
    ]
    with pytest.raises(ValueError, match="环"):
        detect_cycle_same_turn(tasks, tid)

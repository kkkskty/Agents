"""StepRef / turn_id / SessionStore 行为场景（不调用意图 LLM）。"""

from uuid import uuid4

import pytest

from app.core.orchestrator import MultiAgentOrchestrator
from app.core.session_store import SessionStore
from app.core.state import OrderTask, QueryTask, StepArtifact, StepRef


@pytest.fixture
def orch() -> MultiAgentOrchestrator:
    return MultiAgentOrchestrator(SessionStore())


def test_coerce_depends_dict(orch: MultiAgentOrchestrator) -> None:
    refs = orch._coerce_depends([{"turn_id": "ta", "step_id": "ta:task_0"}])
    assert len(refs) == 1 and refs[0].turn_id == "ta"


def test_deps_satisfied_same_turn_waiting(orch: MultiAgentOrchestrator) -> None:
    tid = str(uuid4())
    g0 = f"{tid}:task_0"
    g1 = f"{tid}:task_1"
    t0 = QueryTask(id=g0, text="q", depends_on=[])
    t1 = OrderTask(id=g1, text="o", depends_on=[StepRef(tid, g0)])
    t0.status = "pending"
    t1.status = "pending"
    m = {t0.id: t0, t1.id: t1}
    assert not orch._deps_satisfied(t1, m, "sid", tid)


def test_deps_satisfied_same_turn_done(orch: MultiAgentOrchestrator) -> None:
    tid = str(uuid4())
    g0 = f"{tid}:task_0"
    g1 = f"{tid}:task_1"
    t0 = QueryTask(id=g0, text="q", depends_on=[])
    t1 = OrderTask(id=g1, text="o", depends_on=[StepRef(tid, g0)])
    t0.status = "done"
    t1.status = "pending"
    m = {t0.id: t0, t1.id: t1}
    assert orch._deps_satisfied(t1, m, "sid", tid)


def test_deps_satisfied_history_missing_artifact(orch: MultiAgentOrchestrator) -> None:
    tid = str(uuid4())
    old_tid = str(uuid4())
    old_step = f"{old_tid}:task_0"
    g0 = f"{tid}:task_0"
    t0 = OrderTask(id=g0, text="o", depends_on=[StepRef(old_tid, old_step)])
    m = {t0.id: t0}
    assert not orch._deps_satisfied(t0, m, "sid", tid)


def test_deps_satisfied_history_present(orch: MultiAgentOrchestrator) -> None:
    sid = "sess1"
    tid = str(uuid4())
    old_tid = str(uuid4())
    old_step = f"{old_tid}:task_0"
    orch.session_store.put_step_artifact(
        sid,
        StepArtifact(
            turn_id=old_tid,
            step_id=old_step,
            intent="query",
            status="ok",
            message="ok",
            payload={"outputs": {}},
        ),
    )
    g0 = f"{tid}:task_0"
    t0 = OrderTask(id=g0, text="o", depends_on=[StepRef(old_tid, old_step)])
    m = {t0.id: t0}
    assert orch._deps_satisfied(t0, m, sid, tid)


def test_next_ready_respects_order(orch: MultiAgentOrchestrator) -> None:
    tid = str(uuid4())
    g0 = f"{tid}:task_0"
    g1 = f"{tid}:task_1"
    tasks = [
        QueryTask(id=g0, text="q", depends_on=[]),
        OrderTask(id=g1, text="o", depends_on=[StepRef(tid, g0)]),
    ]
    assert orch._next_ready_task_index(tasks, "any", tid) == 0
    tasks[0].status = "done"
    assert orch._next_ready_task_index(tasks, "any", tid) == 1

"""StepRef DAG：同轮依赖环检测与全局 step id 工具。"""

from __future__ import annotations

from app.core.state import StepRef, Task


def global_step_id(turn_id: str, local_task_id: str) -> str:
    """local_task_id 形如 task_0；返回会话内全局 step_id。"""
    lid = local_task_id.strip()
    if not lid:
        raise ValueError("empty local_task_id")
    return f"{turn_id}:{lid}"


def validate_same_turn_refs(tasks: list[Task], current_turn_id: str) -> None:
    """同轮 StepRef 必须指向已声明的 step_id。"""
    ids = {t.id for t in tasks}
    for t in tasks:
        for ref in t.depends_on:
            if ref.turn_id != current_turn_id:
                continue
            if ref.step_id not in ids:
                raise ValueError(
                    f"task {t.id!r} depends_on 引用未知同轮步骤 {ref.step_id!r}，已知: {sorted(ids)}"
                )


def detect_cycle_same_turn(tasks: list[Task], current_turn_id: str) -> None:
    """同轮依赖图中若存在环则报错。历史 turn 依赖不参与环检测。"""
    ids = {t.id for t in tasks}
    adj: dict[str, list[str]] = {i: [] for i in ids}
    for t in tasks:
        for ref in t.depends_on:
            if ref.turn_id != current_turn_id:
                continue
            u = ref.step_id
            v = t.id
            if u not in ids or v not in ids:
                continue
            adj.setdefault(u, []).append(v)

    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node: str) -> None:
        if node in visiting:
            raise ValueError(f"同轮子任务依赖存在环，涉及节点 {node!r}")
        if node in visited:
            return
        visiting.add(node)
        for w in adj.get(node, []):
            dfs(w)
        visiting.remove(node)
        visited.add(node)

    for nid in ids:
        if nid not in visited:
            dfs(nid)

"""
通用状态树 DFS 推进范式（guard / done / action 由代码定义，不依赖 LLM）。

各项目子树（launch / deploy / retry / observer）各自声明 StateTreeNode 森林，
统一通过 dfs_next_action 选择下一 action；节点尝试次数与完成态由宿主 state 维护。

后续推广示例：
- deploy: check_package → build_native → install_apk → verify_package
- retry: classify_failure → analyze_log → patch_config → redeploy
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

FactsT = TypeVar("FactsT")
StateT = TypeVar("StateT")
ActionT = TypeVar("ActionT")


GuardFn = Callable[[StateT, FactsT], bool]
DoneFn = Callable[[StateT], bool]
AttemptsFn = Callable[[StateT, str], int]


@dataclass(frozen=True, slots=True)
class StateTreeNode(Generic[StateT, FactsT, ActionT]):
    """状态树节点：DFS 按 children 顺序找第一个 guard 命中且未 done 的叶子/分支。"""

    id: str
    action: ActionT | None
    guard: GuardFn[StateT, FactsT]
    done: DoneFn[StateT]
    children: tuple[StateTreeNode[StateT, FactsT, ActionT], ...] = ()
    max_attempts: int = 3


@dataclass(frozen=True, slots=True)
class StateTreeDecision(Generic[ActionT]):
    """DFS 决策结果。"""

    action: ActionT | None
    node_id: str
    reason: str = ""


@dataclass
class TreeTrace:
    """可写入 audit / graph state 的遍历轨迹。"""

    visited: list[str] = field(default_factory=list)
    selected_node: str = ""
    selected_action: str = ""


def dfs_next_action(
    root: StateTreeNode[StateT, FactsT, ActionT],
    state: StateT,
    facts: FactsT,
    *,
    node_attempts: AttemptsFn[StateT, str],
    trace: TreeTrace | None = None,
) -> StateTreeDecision[ActionT]:
    """
    深度优先：在第一个 guard 为真的子树中，找第一个未完成且未超尝试次数的节点。
    若节点有 children，优先深入 children；否则返回该节点的 action。
    """
    stack: list[StateTreeNode[StateT, FactsT, ActionT]] = list(root.children)
    if trace is not None:
        trace.visited.append(root.id)

    while stack:
        node = stack.pop(0)
        if trace is not None:
            trace.visited.append(node.id)

        if not node.guard(state, facts):
            continue
        if node.done(state):
            continue
        if node_attempts(state, node.id) >= node.max_attempts:
            continue

        if node.children:
            for child in reversed(node.children):
                stack.insert(0, child)
            continue

        if node.action is None:
            continue

        if trace is not None:
            trace.selected_node = node.id
            trace.selected_action = str(node.action)
        return StateTreeDecision(
            action=node.action,
            node_id=node.id,
            reason=f"dfs:{node.id}",
        )

    return StateTreeDecision(action=None, node_id="", reason="dfs:no_eligible_node")

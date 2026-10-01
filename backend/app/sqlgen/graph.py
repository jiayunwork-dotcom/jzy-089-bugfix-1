"""关联图：把模型里声明的关联看成无向图，为一次查询推导 JOIN 链路。

规则（自动化测试覆盖）：
- 只引用单表 -> 不产生任何 JOIN；
- 跨表查询走 BFS 生成树：覆盖所有被引用表，且每张表最多连接一次；
- BFS 的邻接顺序按 (对端表名, 关联id) 排序，保证同样输入得到同样路径。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from ..modeling.schema import ModelSpec, RelationSpec


@dataclass(frozen=True)
class JoinEdge:
    relation: RelationSpec
    parent: str
    child: str

    def child_per_parent_is_many(self) -> bool:
        """沿 parent -> child 方向，一个 parent 行是否对应多个 child 行。"""
        rel = self.relation
        if self.parent == rel.left_table:
            return rel.cardinality in ("1:N", "N:N")
        # parent 是 right 侧，基数方向取反
        return rel.cardinality in ("N:1", "N:N")

    def parent_per_child_is_many(self) -> bool:
        """沿 child -> parent 反方向，一个 child 行是否对应多个 parent 行。

        N:1 关联被当成 parent=多侧、child=一侧 连接时（根在多侧），
        一侧子树的每行会被多侧父表复制——放大检测的另一方向必须靠它。
        """
        rel = self.relation
        if self.parent == rel.left_table:
            return rel.cardinality in ("N:1", "N:N")
        return rel.cardinality in ("1:N", "N:N")

    def on_clause(self, qname=None) -> str:
        """ON 等值条件。

        qname(table_name) -> 全限定（含模式）表名；为 None 时退回裸表名
        （仅旧测试/调试使用，生产路径总是传入模型解析器）。
        """
        rel = self.relation
        if self.parent == rel.left_table:
            p_col, c_col = rel.left_column, rel.right_column
        else:
            p_col, c_col = rel.right_column, rel.left_column
        p_name = qname(self.parent) if qname is not None else f'"{self.parent}"'
        c_name = qname(self.child) if qname is not None else f'"{self.child}"'
        return f'{p_name}."{p_col}" = {c_name}."{c_col}"'


class JoinGraph:
    def __init__(self, model: ModelSpec):
        self.model = model
        self._adj: Dict[str, List[Tuple[str, RelationSpec]]] = {}
        for rel in model.relations:
            self._adj.setdefault(rel.left_table, []).append(
                (rel.right_table, rel))
            self._adj.setdefault(rel.right_table, []).append(
                (rel.left_table, rel))

    def spanning_tree(self, root: str, wanted: Set[str]
                      ) -> List[JoinEdge]:
        """从 root 出发 BFS，返回覆盖 wanted 的生成树边（按 BFS 序）。"""
        if root not in wanted:
            raise ValueError(f"根表 {root} 不在引用表集合中")
        missing = [t for t in wanted if not self.model.has_table(t)]
        if missing:
            raise ValueError(f"模型中不存在表：{missing}")

        visited: Set[str] = {root}
        edges: List[JoinEdge] = []
        frontier = [root]
        while frontier:
            nxt_frontier: List[str] = []
            for node in frontier:
                neighbors = sorted(self._adj.get(node, []),
                                   key=lambda x: (x[0], x[1].id))
                for other, rel in neighbors:
                    if other in visited:
                        continue
                    visited.add(other)
                    edges.append(JoinEdge(relation=rel, parent=node, child=other))
                    nxt_frontier.append(other)
            frontier = nxt_frontier

        unreachable = wanted - visited
        if unreachable:
            raise ValueError(
                f"以下表与 {root} 之间没有声明关联，无法生成 JOIN："
                f"{sorted(unreachable)}")
        # 只保留"通向被引用表"所需的边：裁掉挂不到 wanted 的叶子分支
        return self._prune(edges, root, wanted)

    def _prune(self, edges: List[JoinEdge], root: str,
               wanted: Set[str]) -> List[JoinEdge]:
        children: Dict[str, List[JoinEdge]] = {}
        for e in edges:
            children.setdefault(e.parent, []).append(e)

        keep: List[JoinEdge] = []

        def useful(table: str) -> bool:
            child_edges = children.get(table, [])
            keep_here = table in wanted
            for e in child_edges:
                if useful(e.child):
                    keep.append(e)
                    keep_here = True
            return keep_here

        useful(root)
        # useful 按 children 顺序（即 BFS 同层、表名序）追加，天然有序；
        # 但子先于父被 append，需要按原 BFS 序输出
        order = {id(e): i for i, e in enumerate(edges)}
        keep.sort(key=lambda e: order[id(e)])
        return keep

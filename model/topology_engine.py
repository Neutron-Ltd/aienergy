"""Topology parsing and lookup engine for relation.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class NodeMeta:
    """Normalized node metadata used by the imputation pipeline."""

    node_id: int
    name: str
    category: str
    parent_id: Optional[int]


class TopologyEngine:
    """Fast topology lookups for parent/children/sibling relationships."""

    def __init__(self, relation_json_path: str | Path) -> None:
        self.relation_json_path = Path(relation_json_path)
        self._nodes: Dict[int, NodeMeta] = {}
        self._children: Dict[int, List[int]] = {}
        self._load()

    def _load(self) -> None:
        if not self.relation_json_path.exists():
            raise FileNotFoundError(f"relation.json not found: {self.relation_json_path}")
        with self.relation_json_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)

        root = payload.get("root_node")
        if not isinstance(root, dict):
            raise ValueError("relation.json is invalid: missing `root_node` object.")
        self._walk(node=root, parent_id=None)

    def _walk(self, node: dict, parent_id: Optional[int]) -> None:
        node_id = node.get("id")
        if node_id is None:
            return

        node_id = int(node_id)
        meta = NodeMeta(
            node_id=node_id,
            name=str(node.get("name", f"node_{node_id}")),
            category=str(node.get("category", "unknown")),
            parent_id=parent_id,
        )
        self._nodes[node_id] = meta

        sub_nodes = node.get("sub_nodes", []) or []
        child_ids: list[int] = []
        for child in sub_nodes:
            child_id = child.get("id")
            if child_id is None:
                continue
            child_ids.append(int(child_id))
            self._walk(child, parent_id=node_id)
        self._children[node_id] = child_ids

    def has_node(self, node_id: int) -> bool:
        return node_id in self._nodes

    def get_node(self, node_id: int) -> NodeMeta:
        if node_id not in self._nodes:
            raise KeyError(f"Node {node_id} not found in topology.")
        return self._nodes[node_id]

    def get_parent(self, node_id: int) -> Optional[int]:
        return self.get_node(node_id).parent_id

    def get_children(self, node_id: int) -> List[int]:
        return list(self._children.get(node_id, []))

    def get_siblings(self, node_id: int) -> List[int]:
        parent_id = self.get_parent(node_id)
        if parent_id is None:
            return []
        return [child for child in self.get_children(parent_id) if child != node_id]

    def category(self, node_id: int) -> str:
        return self.get_node(node_id).category

    def all_node_ids(self) -> List[int]:
        return list(self._nodes.keys())


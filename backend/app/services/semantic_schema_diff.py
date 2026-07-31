"""Dependency-free schema normalization and diff primitives."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional


def normalize_schema_snapshot(tables: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_tables = []
    for table in tables:
        columns = [
            {
                "name": str(column.get("name") or ""),
                "type": str(column.get("type") or "").lower(),
                "comment": str(column.get("comment") or ""),
                "primary_key": bool(column.get("primary_key")),
                "indexed": bool(column.get("indexed")),
                "ordinal_position": int(column.get("ordinal_position", position)),
            }
            for position, column in enumerate(table.get("columns") or [])
        ]
        columns.sort(key=lambda item: (item["ordinal_position"], item["name"]))
        normalized_tables.append({"name": str(table.get("name") or ""), "kind": str(table.get("kind") or "table"), "comment": str(table.get("comment") or table.get("description") or ""), "columns": columns})
    normalized_tables.sort(key=lambda item: (item["kind"], item["name"]))
    return {"dialect": "mysql", "tables": normalized_tables}


def schema_fingerprint(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def diff_schema_snapshots(base: Optional[dict[str, Any]], target: dict[str, Any]) -> list[dict[str, Any]]:
    base_tables = {item["name"]: item for item in (base or {}).get("tables", [])}
    target_tables = {item["name"]: item for item in target.get("tables", [])}
    diff: list[dict[str, Any]] = []
    for name in sorted(target_tables.keys() - base_tables.keys()):
        diff.append({"object_type": "table", "change_type": "added", "physical_identity": name, "before": None, "after": target_tables[name], "severity": "info", "blocking": False})
        diff.extend(
            {"object_type": "column", "change_type": "added", "physical_identity": f"{name}.{column['name']}", "table_name": name, "before": None, "after": column, "severity": "info", "blocking": False}
            for column in target_tables[name].get("columns", [])
        )
    for name in sorted(base_tables.keys() - target_tables.keys()):
        diff.append({"object_type": "table", "change_type": "removed", "physical_identity": name, "before": base_tables[name], "after": None, "severity": "critical", "blocking": True})
        diff.extend(
            {"object_type": "column", "change_type": "removed", "physical_identity": f"{name}.{column['name']}", "table_name": name, "before": column, "after": None, "severity": "critical", "blocking": True}
            for column in base_tables[name].get("columns", [])
        )
    for table_name in sorted(base_tables.keys() & target_tables.keys()):
        before_table, after_table = base_tables[table_name], target_tables[table_name]
        table_fields = {key: (before_table.get(key), after_table.get(key)) for key in ("kind", "comment") if before_table.get(key) != after_table.get(key)}
        if table_fields:
            diff.append({"object_type": "table", "change_type": "modified", "physical_identity": table_name, "before": {key: value[0] for key, value in table_fields.items()}, "after": {key: value[1] for key, value in table_fields.items()}, "severity": "warning" if "kind" in table_fields else "info", "blocking": "kind" in table_fields})
        before_columns = {item["name"]: item for item in before_table.get("columns", [])}
        after_columns = {item["name"]: item for item in after_table.get("columns", [])}
        for name in sorted(after_columns.keys() - before_columns.keys()):
            diff.append({"object_type": "column", "change_type": "added", "physical_identity": f"{table_name}.{name}", "table_name": table_name, "before": None, "after": after_columns[name], "severity": "info", "blocking": False})
        for name in sorted(before_columns.keys() - after_columns.keys()):
            diff.append({"object_type": "column", "change_type": "removed", "physical_identity": f"{table_name}.{name}", "table_name": table_name, "before": before_columns[name], "after": None, "severity": "critical", "blocking": True})
        for name in sorted(before_columns.keys() & after_columns.keys()):
            before_column, after_column = before_columns[name], after_columns[name]
            changed = {key: (before_column.get(key), after_column.get(key)) for key in ("type", "comment", "primary_key", "indexed", "ordinal_position") if before_column.get(key) != after_column.get(key)}
            if changed:
                structural = any(key in changed for key in ("type", "primary_key"))
                diff.append({"object_type": "column", "change_type": "modified", "physical_identity": f"{table_name}.{name}", "table_name": table_name, "before": {key: value[0] for key, value in changed.items()}, "after": {key: value[1] for key, value in changed.items()}, "severity": "critical" if structural else "info", "blocking": structural})
    return diff


def summarize_diff(diff: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {"added": 0, "modified": 0, "removed": 0, "blocking": 0, "total": len(diff)}
    for item in diff:
        summary[item["change_type"]] += 1
        summary["blocking"] += int(bool(item.get("blocking")))
    return summary

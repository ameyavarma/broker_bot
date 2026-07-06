"""One-off diagnostic: dump every field IBKR reports for positions and fills,
so we can decide which are worth capturing. Safe to delete later.

    python tests/inspect_fields.py   (run from the project root)
"""
import dataclasses
import sys
from pathlib import Path

# Internal modules live in ../core; put it on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import connection


def all_fields(obj):
    """Return {field_name: value} for an ib_async dataclass or namedtuple."""
    if dataclasses.is_dataclass(obj):
        return {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}
    if hasattr(obj, "_fields"):  # namedtuple
        return {name: getattr(obj, name) for name in obj._fields}
    return vars(obj)


def show(title, obj):
    print(f"\n=== {title}: {type(obj).__name__} ===")
    for k, v in all_fields(obj).items():
        if k == "contract":
            continue  # shown separately, fully expanded
        print(f"  {k:28s} = {v!r}")


with connection.connect() as ib:
    positions = ib.positions()
    fills = ib.reqExecutions()

    print(f"\n######## POSITIONS ({len(positions)}) ########")
    for p in positions:
        show("Position", p)
        show("Position.contract", p.contract)

    print(f"\n######## FILLS ({len(fills)}) ########")
    for f in fills:
        show("Fill.contract", f.contract)
        show("Fill.execution", f.execution)
        show("Fill.commissionReport", f.commissionReport)

#!/usr/bin/env python3
"""Inspect compiled dialogue graph .bytes files extracted from resources.assets."""

from __future__ import annotations

import argparse
import re
import struct
from dataclasses import dataclass
from pathlib import Path


POP_DIALOG_RE = re.compile(r"^PopDialog\((\d+)\)$")


@dataclass(frozen=True)
class Transition:
    to_state: int
    condition: str


@dataclass(frozen=True)
class State:
    state_id: int
    name: str
    instructions: tuple[str, ...]
    transitions: tuple[Transition, ...]


@dataclass(frozen=True)
class Graph:
    filename: str
    states: tuple[State, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse compiled dialogue graph .bytes files and show PopDialog order.",
    )
    parser.add_argument(
        "graphs",
        nargs="+",
        type=Path,
        help="Extracted .bytes graph file(s).",
    )
    parser.add_argument(
        "--mode",
        choices=("walk", "popdialogs", "full"),
        default="walk",
        help=(
            "walk: follow first transition from Entry; "
            "popdialogs: list states containing PopDialog; "
            "full: print all states/transitions."
        ),
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=200,
        help="Maximum transition steps for --mode walk.",
    )
    return parser.parse_args()


def read_u32(raw: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from("<I", raw, offset)[0], offset + 4


def read_aligned_string(raw: bytes, offset: int) -> tuple[str, int]:
    size, offset = read_u32(raw, offset)
    data = raw[offset : offset + size]
    offset += size
    return data.split(b"\0", 1)[0].decode("utf-8", "replace"), offset


def parse_graph(path: Path) -> Graph:
    raw = path.read_bytes()
    offset = 0
    _version, offset = read_u32(raw, offset)
    _endian_check, offset = read_u32(raw, offset)
    filename, offset = read_aligned_string(raw, offset)
    state_count, offset = read_u32(raw, offset)

    states: list[State] = []
    for state_id in range(state_count):
        name, offset = read_aligned_string(raw, offset)
        _shape, offset = read_u32(raw, offset)
        instruction_count, offset = read_u32(raw, offset)

        instructions: list[str] = []
        for _ in range(instruction_count):
            compiled_size, offset = read_u32(raw, offset)
            offset += compiled_size
            debug_text, offset = read_aligned_string(raw, offset)
            instructions.append(debug_text)

        transition_count, offset = read_u32(raw, offset)
        transitions: list[Transition] = []
        for _ in range(transition_count):
            to_state, offset = read_u32(raw, offset)
            compiled_size, offset = read_u32(raw, offset)
            offset += compiled_size
            condition, offset = read_aligned_string(raw, offset)
            transitions.append(Transition(to_state=to_state, condition=condition or "1"))

        states.append(
            State(
                state_id=state_id,
                name=name,
                instructions=tuple(instructions),
                transitions=tuple(transitions),
            )
        )

    if offset != len(raw):
        raise ValueError(f"{path}: parsed {offset} bytes, file has {len(raw)} bytes")

    return Graph(filename=filename, states=tuple(states))


def popdialog_ids(state: State) -> list[str]:
    ids: list[str] = []
    for instruction in state.instructions:
        match = POP_DIALOG_RE.match(instruction)
        if match:
            ids.append(match.group(1))
    return ids


def print_full(graph: Graph) -> None:
    print(f"=== {graph.filename} ({len(graph.states)} states) ===")
    for state in graph.states:
        short = "; ".join(state.instructions) if state.instructions else state.name
        print(f"{state.state_id:02d}: {short}")
        for transition in state.transitions:
            print(f"    -> {transition.to_state:02d} [{transition.condition}]")


def print_popdialogs(graph: Graph) -> None:
    print(f"=== {graph.filename} ===")
    found = False
    for state in graph.states:
        ids = popdialog_ids(state)
        if not ids:
            continue
        found = True
        print(f"{state.state_id:02d}: {' -> '.join(ids)}")
        for transition in state.transitions:
            print(f"    -> {transition.to_state:02d} [{transition.condition}]")
    if not found:
        print("(no PopDialog states)")


def print_walk(graph: Graph, max_steps: int) -> None:
    print(f"=== {graph.filename} ===")
    by_id = {state.state_id: state for state in graph.states}
    entry = next((state for state in graph.states if state.name == "Entry"), None)
    if entry is None:
        print("(no Entry state)")
        return

    current_id = entry.state_id
    seen: set[int] = set()
    dialogs: list[str] = []

    for _ in range(max_steps):
        if current_id in seen:
            print(f"loop detected at state {current_id:02d}")
            break
        seen.add(current_id)
        state = by_id[current_id]
        ids = popdialog_ids(state)
        if ids:
            dialogs.extend(ids)
            print(f"{state.state_id:02d}: PopDialog {' -> '.join(ids)}")

        if not state.transitions:
            break

        # Transitions are stored in the exported priority order. For a linear
        # story flow, the first transition is the runtime path to inspect.
        current_id = state.transitions[0].to_state
    else:
        print(f"stopped after --max-steps={max_steps}")

    print("PopDialog order:", " -> ".join(dialogs) if dialogs else "(none)")


def main() -> int:
    args = parse_args()
    for index, path in enumerate(args.graphs):
        if index:
            print()
        graph = parse_graph(path)
        if args.mode == "full":
            print_full(graph)
        elif args.mode == "popdialogs":
            print_popdialogs(graph)
        else:
            print_walk(graph, args.max_steps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

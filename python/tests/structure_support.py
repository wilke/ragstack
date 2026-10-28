"""Synthetic structured documents for the #828 structure tests.

Every fixture here is built by an EMITTER that appends strings and records the
offset each one landed at (design note §2: offsets are accumulated while the
text is built, never recovered afterwards), so the offsets are exact by
construction. The text is synthetic — no corpus content.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

from ragstack.ingestion.structure import content_sha256
from ragstack.models import Block, DocumentStructure, Section


@dataclass
class _Node:
    title: str | None
    level: int
    rank: int | None
    start: int
    end: int = -1
    roles: list[str] | None = None
    role_source: str | None = None
    children: list[_Node] = field(default_factory=list)


class Emitter:
    """Append text; open/close sections and record blocks at emitter
    positions. ``build()`` extends section ends to the next start (§2
    invariant 3) and returns ``(content, DocumentStructure)``."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.pos = 0
        self.blocks: list[Block] = []
        self.roots: list[_Node] = []
        self.stack: list[_Node] = []

    def _emit(self, s: str) -> tuple[int, int]:
        start = self.pos
        self.parts.append(s)
        self.pos += len(s)
        return start, self.pos

    def section(self, title: str | None, level: int, *, rank: int | None = None,
                roles: list[str] | None = None, role_source: str | None = None,
                marker: str = "#") -> None:
        """Open a section at ``level`` (tree depth), closing deeper/equal ones,
        and emit its heading line (``## Title``) with a title-only heading
        block."""
        while self.stack and self.stack[-1].level >= level:
            self.stack.pop()
        assert len(self.stack) == level - 1, "levels must not skip in the fixture"
        node = _Node(title=title, level=level, rank=rank, start=self.pos,
                     roles=roles, role_source=role_source)
        (self.stack[-1].children if self.stack else self.roots).append(node)
        self.stack.append(node)
        if title is not None:
            self._emit(marker * (rank or level) + " ")
            s, e = self._emit(title)
            self.blocks.append(Block(start=s, end=e, kind="heading"))
            self._emit("\n\n")

    def block(self, text: str, kind: str = "prose", label: str | None = None,
              sep: str = "\n\n") -> tuple[int, int]:
        s, e = self._emit(text)
        self.blocks.append(Block(start=s, end=e, kind=kind, label=label))
        self._emit(sep)
        return s, e

    def text(self, text: str) -> None:
        """Text that is in no block (blocks need not cover the text)."""
        self._emit(text)

    def build(self, *, source: str = "parsed", adapter: str = "test/1"
              ) -> tuple[str, DocumentStructure]:
        content = "".join(self.parts)
        n = len(content)
        sections: list[Section] = []

        def close(nodes: list[_Node], end: int) -> None:
            for i, node in enumerate(nodes):
                node.end = nodes[i + 1].start if i + 1 < len(nodes) else end
                close(node.children, node.end)

        close(self.roots, n)

        def walk(nodes: list[_Node], path: list[str]) -> None:
            for node in nodes:
                p = [*path, node.title] if node.title is not None else list(path)
                sections.append(Section(
                    start=node.start, end=node.end, level=node.level, title=node.title,
                    path=p, roles=node.roles, role_source=node.role_source,
                    rank=node.rank, locator=f"L{node.start}",
                ))
                walk(node.children, p)

        walk(self.roots, [])
        return content, DocumentStructure(
            content_sha256=content_sha256(content), sections=sections,
            blocks=self.blocks, structure_source=source, adapter=adapter,
        )


def paper() -> tuple[str, DocumentStructure]:
    """A small synthetic paper: an untitled preamble, *Methods* with two
    subsections (one citing a figure and a table), and a combined
    *Results and Discussion* section holding a figure caption."""
    em = Emitter()
    em.section(None, 1)
    em.block("A synthetic preamble paragraph that has no heading of its own.")
    em.section("Methods", 1)
    em.block("Lead paragraph of the methods section, before any subsection.")
    em.section("Sample preparation", 2)
    em.block("Samples were prepared as shown in Fig. 2 and summarised in Table 1. "
             "The preparation took several careful steps over many days.")
    em.section("Statistics", 2)
    em.block("A paired comparison was run on every measured quantity.")
    em.section("Results and Discussion", 1)
    em.block("Figure 1. A synthetic caption describing the first figure.",
             kind="figure_caption", label="Figure 1")
    em.block("The results agree with the expectation stated in Figs. 1 and 3.")
    return em.build()


def random_structure(seed: int) -> tuple[str, DocumentStructure]:
    """A random valid tree (depth <= 4, titled and untitled sections, blocks)
    for the property tests."""
    rng = random.Random(seed)
    em = Emitter()
    em.section(None, 1)
    em.block("preamble " * rng.randint(1, 5))
    level = 1
    for i in range(rng.randint(1, 12)):
        level = rng.randint(1, min(4, level + 1))
        titled = rng.random() < 0.8
        em.section(f"Heading {i}" if titled else None, level)
        # An untitled section must hold something, or it would be empty.
        for _ in range(rng.randint(0 if titled else 1, 3)):
            kind = rng.choice(["prose", "code", "list", "quote", "math"])
            em.block(f"block {i} " * rng.randint(1, 8), kind=kind)
        if rng.random() < 0.3:
            em.text("loose text\n")
    return em.build()


def record(content: str, structure: DocumentStructure | dict | None, *,
           path: str = "/corpus/synthetic-structured-paper.pdf",
           metadata: dict | None = None) -> dict:
    rec: dict = {"text": content, "path": path, "metadata": metadata or {"title": "T"}}
    if structure is not None:
        rec["structure"] = (structure.model_dump() if isinstance(structure, DocumentStructure)
                            else structure)
    return rec


def write_shard(path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")

"""Pure-Python STEP (ISO 10303-21) parser → assembly BOM tree.

Why no pythonocc-core / OpenCascade?
  pythonocc-core needs a 200MB+ native dependency (OCCT) compiled to match
  the Python ABI. Adds significant deploy complexity and we don't actually
  need geometry — only the assembly tree (parent/child PRODUCT links).

What this extracts:
  - PRODUCT entities                           → part_number, part_name
  - PRODUCT_DEFINITION → FORMATION → PRODUCT   → linkage chain
  - NEXT_ASSEMBLY_USAGE_OCCURRENCE (NAUO)      → parent→child edges

Output: ordered list of dicts shaped like BOMNode (level / part_number /
part_name / quantity / sort_order / parent_pd_index). Parent linking by
DB id happens in the upload route after the BOMNode rows are flushed.

Limitations (acceptable for v1):
  - Ignores geometry (volumes, materials, bounding boxes)
  - Doesn't handle SPECIFIED_HIGHER_USAGE_OCCURRENCE / MAPPED_ITEM
    assembly variants — only standard NAUO
  - If multiple NAUOs share the same (parent, child) pair we sum to qty;
    we don't try to deduplicate transformations (same child placed twice
    at different positions counts as qty=2)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


# STEP files are nominally 7-bit ASCII with non-ASCII chars escaped as
# \X\xx\ (Latin-1 hex) or \X2\xxxx\X0\ (UTF-16BE hex). Reality: Chinese
# SolidWorks / Pro-E etc. just dump raw platform-encoding bytes into strings.
# Try strict decoders in order of likelihood; fall back to latin-1 (lossless
# but mojibake-y) so we never crash.
_DECODE_CANDIDATES = ("utf-8", "gb18030", "shift_jis", "cp1252")


def _decode_bytes(data: bytes) -> str:
    """Best-effort decode. Tries UTF-8 → GB18030 → Shift_JIS → CP1252 → Latin-1."""
    for enc in _DECODE_CANDIDATES:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


# STEP-standard escape sequences inside string literals.
#   \X\HH         single 8-bit char (Latin-1 code point)
#   \X2\HHHH...\X0\   one or more UTF-16BE code points, hex
#   \X4\HHHHHHHH...\X0\  UTF-32BE code points (rare)
#   \S\<c>        Latin-1 with high bit set: code point = ord(c) + 128
#   \P<x>\        page directive (we drop)
_RE_X4 = re.compile(r"\\X4\\((?:[0-9A-Fa-f]{8})+)\\X0\\")
_RE_X2 = re.compile(r"\\X2\\((?:[0-9A-Fa-f]{4})+)\\X0\\")
_RE_X1 = re.compile(r"\\X\\([0-9A-Fa-f]{2})")
_RE_S  = re.compile(r"\\S\\(.)")


def _unescape_step_string(s: str) -> str:
    """Apply STEP ISO 10303-21 string escapes, in the right order."""
    def x4_sub(m: re.Match) -> str:
        hex_blob = m.group(1)
        chars = [chr(int(hex_blob[i:i + 8], 16)) for i in range(0, len(hex_blob), 8)]
        return "".join(chars)

    def x2_sub(m: re.Match) -> str:
        hex_blob = m.group(1)
        chars = [chr(int(hex_blob[i:i + 4], 16)) for i in range(0, len(hex_blob), 4)]
        return "".join(chars)

    def x1_sub(m: re.Match) -> str:
        return chr(int(m.group(1), 16))

    def s_sub(m: re.Match) -> str:
        c = m.group(1)
        if not c:
            return ""
        return chr(ord(c) + 128)

    s = _RE_X4.sub(x4_sub, s)
    s = _RE_X2.sub(x2_sub, s)
    s = _RE_X1.sub(x1_sub, s)
    s = _RE_S.sub(s_sub, s)
    return s


# Match a single entity record after we've stripped HEADER/ENDSEC and split by ';'.
# Allows multi-line entries (we'll have already collapsed whitespace).
_ENTITY_RE = re.compile(
    r"^\s*#(\d+)\s*=\s*([A-Z_][A-Z0-9_]*)\s*\((.*)\)\s*$",
    re.DOTALL,
)


@dataclass
class _Entity:
    ref: int
    name: str
    args_raw: str
    args: list[object] = field(default_factory=list)


def _tokenize_args(s: str) -> list[object]:
    """Tokenize a STEP entity argument list.

    Returns a flat list where each element is one of:
      - str (quoted string, unescaped)
      - int   (entity reference like #123 → just the int 123)
      - tuple/list (parenthesized sub-list, recursively tokenized)
      - "$"  (UNSET marker, kept as the literal string "$")
      - "*"  (DERIVED marker)
      - bare number/identifier as a string

    Only handles the fragment of STEP we care about. Sufficient for
    PRODUCT / PRODUCT_DEFINITION / PRODUCT_DEFINITION_FORMATION / NAUO.
    """
    out: list[object] = []
    i, n = 0, len(s)

    while i < n:
        c = s[i]

        if c.isspace() or c == ",":
            i += 1
            continue

        # Quoted string. STEP uses single-quote-doubled to escape: 'it''s'
        if c == "'":
            i += 1
            buf: list[str] = []
            while i < n:
                if s[i] == "'":
                    if i + 1 < n and s[i + 1] == "'":
                        buf.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(s[i])
                i += 1
            out.append(_unescape_step_string("".join(buf)))
            continue

        # Sub-list
        if c == "(":
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                ch = s[j]
                if ch == "'":
                    # skip over string
                    j += 1
                    while j < n:
                        if s[j] == "'":
                            if j + 1 < n and s[j + 1] == "'":
                                j += 2
                                continue
                            j += 1
                            break
                        j += 1
                    continue
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                j += 1
            inner = s[i + 1 : j - 1]
            out.append(_tokenize_args(inner))
            i = j
            continue

        # Entity reference
        if c == "#":
            j = i + 1
            while j < n and s[j].isdigit():
                j += 1
            out.append(int(s[i + 1 : j]))
            i = j
            continue

        # Bare token — number, identifier, $, *, .ENUM., etc.
        j = i
        while j < n and s[j] not in (",", "(", ")", "'"):
            j += 1
        tok = s[i:j].strip()
        if tok:
            out.append(tok)
        i = j

    return out


def _parse_entities(text: str) -> dict[int, _Entity]:
    """Parse the DATA section into {ref: _Entity}."""
    # Isolate DATA section. STEP files have HEADER then DATA.
    m = re.search(r"DATA\s*;(.*?)ENDSEC\s*;", text, re.DOTALL | re.IGNORECASE)
    body = m.group(1) if m else text

    # Strip /* ... */ comments
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)

    # Records terminate with ';'. But strings may legally contain ';' so
    # we walk char-by-char tracking string state.
    records: list[str] = []
    buf: list[str] = []
    in_str = False
    j, m2 = 0, len(body)
    while j < m2:
        ch = body[j]
        if ch == "'":
            if in_str and j + 1 < m2 and body[j + 1] == "'":
                buf.append("''")
                j += 2
                continue
            in_str = not in_str
            buf.append(ch)
            j += 1
            continue
        if ch == ";" and not in_str:
            rec = "".join(buf).strip()
            if rec:
                records.append(rec)
            buf = []
            j += 1
            continue
        buf.append(ch)
        j += 1

    entities: dict[int, _Entity] = {}
    for rec in records:
        m3 = _ENTITY_RE.match(rec)
        if not m3:
            continue
        ref = int(m3.group(1))
        name = m3.group(2).upper()
        args_raw = m3.group(3)
        ent = _Entity(ref=ref, name=name, args_raw=args_raw)
        try:
            ent.args = _tokenize_args(args_raw)
        except Exception:
            ent.args = []
        entities[ref] = ent
    return entities


def _is_pd_formation(name: str) -> bool:
    # PRODUCT_DEFINITION_FORMATION and _WITH_SPECIFIED_SOURCE
    return name == "PRODUCT_DEFINITION_FORMATION" or name.startswith(
        "PRODUCT_DEFINITION_FORMATION"
    )


def _is_nauo(name: str) -> bool:
    return name == "NEXT_ASSEMBLY_USAGE_OCCURRENCE"


@dataclass
class StepNode:
    part_number: str
    part_name: str
    description: str | None
    quantity: int
    level: int
    sort_order: int
    parent_index: int | None  # index into the resulting list, or None for root
    pd_ref: int  # PD entity ref this node corresponds to (for debug)


def parse_step(data: bytes | str) -> list[StepNode]:
    """Parse STEP file content → flat ordered list of StepNode.

    Returns nodes in DFS pre-order with parent_index pointing at the parent
    row (None for roots). Levels are 0-based from each root.
    """
    text = _decode_bytes(data) if isinstance(data, bytes) else data
    entities = _parse_entities(text)

    # ---- Resolve PRODUCT_DEFINITION → PRODUCT ---------------------------------
    pd_to_product: dict[int, dict[str, str]] = {}

    for ref, ent in entities.items():
        if ent.name != "PRODUCT_DEFINITION":
            continue
        # PRODUCT_DEFINITION(id, description, formation #ref, frame_of_reference #ref)
        if len(ent.args) < 3 or not isinstance(ent.args[2], int):
            continue
        formation_ref = ent.args[2]
        formation = entities.get(formation_ref)
        if not formation or not _is_pd_formation(formation.name):
            continue
        # PRODUCT_DEFINITION_FORMATION(id, description, of_product #ref)
        if len(formation.args) < 3 or not isinstance(formation.args[2], int):
            continue
        product_ref = formation.args[2]
        product = entities.get(product_ref)
        if not product or product.name != "PRODUCT":
            continue
        # PRODUCT(id, name, description, frame_of_reference (...))
        pid = product.args[0] if product.args and isinstance(product.args[0], str) else ""
        pname = (
            product.args[1]
            if len(product.args) > 1 and isinstance(product.args[1], str)
            else ""
        )
        pdesc = (
            product.args[2]
            if len(product.args) > 2 and isinstance(product.args[2], str)
            else ""
        )
        pd_to_product[ref] = {
            "part_number": pid.strip(),
            "part_name": (pname or pid or "Unnamed").strip(),
            "description": pdesc.strip() or None,
        }

    if not pd_to_product:
        # No products at all. Bail out with one synthetic node so the BOM
        # isn't empty — caller can decide how to surface this.
        return []

    # ---- Build NAUO edges ----------------------------------------------------
    # Edge: parent_pd_ref → child_pd_ref (count occurrences for quantity)
    edges: dict[tuple[int, int], int] = {}
    children_of: dict[int, list[int]] = {}
    children_seen: dict[int, set[int]] = {}

    for ref, ent in entities.items():
        if not _is_nauo(ent.name):
            continue
        # NAUO(id, name, description, relating_pd #ref, related_pd #ref, ...)
        ints = [a for a in ent.args if isinstance(a, int)]
        if len(ints) < 2:
            continue
        parent_ref, child_ref = ints[0], ints[1]
        if parent_ref not in pd_to_product or child_ref not in pd_to_product:
            continue
        edges[(parent_ref, child_ref)] = edges.get((parent_ref, child_ref), 0) + 1
        if child_ref not in children_seen.setdefault(parent_ref, set()):
            children_of.setdefault(parent_ref, []).append(child_ref)
            children_seen[parent_ref].add(child_ref)

    # ---- Find roots: PDs that never appear as child --------------------------
    all_pds = set(pd_to_product.keys())
    children_pds = {child for (_p, child) in edges.keys()}
    roots = sorted(all_pds - children_pds)
    if not roots:
        # Cyclic or self-referential — pick the one with the most descendants.
        roots = sorted(all_pds)[:1]

    # ---- DFS build flat list -------------------------------------------------
    out: list[StepNode] = []
    sort_counter = [0]

    def _walk(pd_ref: int, level: int, parent_index: int | None, qty: int) -> None:
        prod = pd_to_product[pd_ref]
        idx = len(out)
        sort_counter[0] += 1
        out.append(
            StepNode(
                part_number=prod["part_number"] or f"PD#{pd_ref}",
                part_name=prod["part_name"],
                description=prod["description"],
                quantity=qty,
                level=level,
                sort_order=sort_counter[0],
                parent_index=parent_index,
                pd_ref=pd_ref,
            )
        )
        # Cycle guard: don't recurse if pd_ref appears in ancestor chain.
        ancestors: set[int] = set()
        cur = parent_index
        while cur is not None:
            ancestors.add(out[cur].pd_ref)
            cur = out[cur].parent_index
        ancestors.add(pd_ref)

        for child_ref in children_of.get(pd_ref, []):
            if child_ref in ancestors:
                continue
            child_qty = edges.get((pd_ref, child_ref), 1)
            _walk(child_ref, level + 1, idx, child_qty)

    for root in roots:
        _walk(root, 0, None, 1)

    return out


def step_nodes_to_dicts(nodes: Iterable[StepNode]) -> list[dict]:
    """Normalize StepNode list → BOMNode-compatible dicts.

    Caller still needs to map parent_index → parent_id once DB rows exist.
    """
    return [
        {
            "level": n.level,
            "part_number": n.part_number,
            "part_name": n.part_name,
            "description": n.description,
            "quantity": float(n.quantity),
            "uom": "EA",
            "material": None,
            "weight": None,
            "supplier": None,
            "unit_cost": None,
            "notes": None,
            "confidence": 1.0,
            "source_ref": {"type": "step_pd", "ref": n.pd_ref},
            "sort_order": n.sort_order,
            "_parent_index": n.parent_index,
        }
        for n in nodes
    ]

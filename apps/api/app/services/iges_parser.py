"""Pure-Python IGES (Initial Graphics Exchange Specification) parser → BOM tree.

IGES is the old (1980s) ANSI standard for CAD interchange. Today it's still
common for surface / sheet-metal exchange, especially from legacy systems.

File structure
--------------
80-char fixed-width records, section identifier in column 73:
  S (Start)     – human description
  G (Global)    – param/record delimiters, file metadata
  D (Directory) – fixed 20-field directory entry, 2 lines per entity
  P (Parameter) – free-form param data starting with entity type
  T (Terminate) – 1 line with section line counts

Assembly hierarchy
------------------
IGES wasn't designed for BOMs and most files contain only geometry. When an
assembly tree IS present, it shows up via:
  Type 308 — Subfigure Definition: { depth, name, N, DE1, DE2, ..., DEN }
             (a named sub-assembly that groups N entities by DE pointer)
  Type 408 — Singular Subfigure Instance: { DE_pointer_to_308, x, y, z, scale }
             (a placement of a 308 inside another context)

So for each 308 we look at its contained DE pointers; any pointer that is a
408 → resolve to the 308 it instances → that's a child sub-assembly. A 308
that is *not* referenced by any 408 is a root.

If the file has no 308 entities at all, we return a single root node named
after the file (caller's job to set that name) — there's no useful tree.

Limitations
-----------
- Ignores geometry entities entirely (we only need the assembly graph).
- Doesn't handle Type 402 form 7 (Group Associativity) — much rarer than 308/408.
- Hollerith string parsing (`<n>H<chars>`) is the only quoting form in IGES;
  no escaping needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field  # noqa: F401  (field reserved for future)
from typing import Iterable

# ---- Type IDs we care about -------------------------------------------------
T_SUBFIGURE_DEF = 308
T_SUBFIGURE_INSTANCE = 408


# IGES is nominally 7-bit ASCII; Chinese / Japanese CAD tools sometimes
# embed platform-encoding bytes inside Hollerith strings. Try strict
# decoders in order of likelihood; fall back to latin-1 (lossless mojibake)
# so we never crash.
_DECODE_CANDIDATES = ("utf-8", "gb18030", "shift_jis", "cp1252")


def _decode_bytes(data: bytes) -> str:
    for enc in _DECODE_CANDIDATES:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


# ---- Parsing primitives ----------------------------------------------------

@dataclass
class _DirEntry:
    """One IGES directory entry (combined from the entity's 2 DE lines)."""
    de_pointer: int               # 1-based odd number; first line of the pair
    entity_type: int              # Type number, e.g. 308
    pd_pointer: int               # 1-based line number into P-section
    pd_line_count: int            # how many P lines belong to this entity
    label: str                    # cols 57-64 of line 2, trimmed (entity label)
    subscript: int                # cols 65-72 of line 2 (entity subscript)


def _split_sections(text: str) -> dict[str, list[str]]:
    """Bucket the 80-char records by section letter (col 73)."""
    sections: dict[str, list[str]] = {"S": [], "G": [], "D": [], "P": [], "T": []}
    for raw_line in text.splitlines():
        # IGES lines must be 80 chars; pad if a tool stripped trailing spaces.
        line = raw_line.rstrip("\r\n")
        if len(line) < 73:
            line = line.ljust(80)
        sec = line[72:73].upper()
        if sec in sections:
            sections[sec].append(line)
    return sections


def _parse_global_delimiters(g_lines: list[str]) -> tuple[str, str]:
    """Read parameter and record delimiter from the Global section.

    The Global section is a sequence of parameter values separated by the
    parameter delimiter and terminated by the record delimiter. The first two
    fields ARE those delimiters (encoded as Hollerith strings, e.g. `1H,`).

    Defaults per spec: param=',' record=';' (when first two fields are empty).
    """
    # Concatenate G section content (cols 1..72)
    body = "".join(line[:72] for line in g_lines)
    # Try to find the first two Hollerith params
    # Format: <count>H<chars> e.g. 1H,
    # If field is empty (just a delimiter encountered), defaults apply.
    param_delim = ","
    record_delim = ";"

    i = 0
    n = len(body)
    fields_seen = 0
    while i < n and fields_seen < 2:
        ch = body[i]
        if ch == ",":
            # empty field → use default for that slot
            fields_seen += 1
            i += 1
            continue
        if ch == ";":
            break
        if ch.isspace():
            i += 1
            continue
        # Expect <count>H<chars>
        j = i
        while j < n and body[j].isdigit():
            j += 1
        if j == i or j >= n or body[j] != "H":
            # malformed; bail with defaults
            break
        count = int(body[i:j])
        start = j + 1
        end = start + count
        if end > n:
            break
        value = body[start:end]
        if fields_seen == 0:
            param_delim = value
        else:
            record_delim = value
        fields_seen += 1
        i = end
        # consume one trailing param delimiter if present
        if i < n and body[i] == param_delim:
            i += 1

    return param_delim, record_delim


def _parse_directory(d_lines: list[str]) -> list[_DirEntry]:
    """Pair up D lines (2 per entity, 80 chars × 20 fixed 8-char fields)."""
    entries: list[_DirEntry] = []
    for k in range(0, len(d_lines) - 1, 2):
        l1 = d_lines[k]
        l2 = d_lines[k + 1]
        # DE pointer = the 1-based sequence number on line 1 (cols 73..80 carry "D######")
        # but the *DE pointer* used by other entities is the line number within the
        # D section: line 1 → 1, line 3 → 3, etc. (always odd).
        de_pointer = k + 1

        def _int(s: str, default: int = 0) -> int:
            s = s.strip()
            if not s:
                return default
            try:
                return int(s)
            except ValueError:
                return default

        entity_type = _int(l1[0:8])
        pd_pointer = _int(l1[8:16])
        # line 2:
        pd_line_count = _int(l2[24:32])
        label = l2[56:64].strip()
        subscript = _int(l2[64:72])

        entries.append(
            _DirEntry(
                de_pointer=de_pointer,
                entity_type=entity_type,
                pd_pointer=pd_pointer,
                pd_line_count=pd_line_count,
                label=label,
                subscript=subscript,
            )
        )
    return entries


def _build_pd_index(p_lines: list[str]) -> dict[int, str]:
    """Map P-section line number (1-based) → that line's content (cols 1..64)."""
    out: dict[int, str] = {}
    for idx, line in enumerate(p_lines, start=1):
        # PD content lives in cols 1..64; cols 65..72 hold the DE back-pointer,
        # 73 is 'P', 74..80 sequence number. We only need cols 1..64 to read params.
        out[idx] = line[:64]
    return out


def _read_pd_record(
    p_index: dict[int, str], start_line: int, line_count: int, record_delim: str
) -> str:
    """Concatenate `line_count` PD lines starting at `start_line` and trim
    everything from the record delimiter onward."""
    parts: list[str] = []
    for n in range(line_count):
        parts.append(p_index.get(start_line + n, ""))
    raw = "".join(parts)
    cut = raw.find(record_delim)
    if cut >= 0:
        raw = raw[:cut]
    return raw


def _tokenize_pd(body: str, param_delim: str) -> list[str]:
    """Split a PD record body into tokens, respecting Hollerith strings.

    Returns raw string tokens. Hollerith fields come back as just their <chars>
    portion (the `<count>H` prefix is stripped).
    """
    tokens: list[str] = []
    cur: list[str] = []
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch == param_delim:
            tokens.append("".join(cur).strip())
            cur = []
            i += 1
            continue
        # Hollerith?  <digits>H<chars>
        if ch.isdigit():
            j = i
            while j < n and body[j].isdigit():
                j += 1
            if j < n and body[j] == "H":
                count = int(body[i:j])
                start = j + 1
                end = start + count
                if end <= n:
                    # Flush any prefix accumulated, then take the Hollerith chars
                    prefix = "".join(cur).strip()
                    cur = []
                    if prefix:
                        # Shouldn't normally happen — but if so, treat prefix as its own token
                        tokens.append(prefix)
                    tokens.append(body[start:end])
                    i = end
                    # Hollerith forms a complete field by itself — consume the
                    # trailing param delimiter so we don't emit a spurious
                    # empty token after it.
                    if i < n and body[i] == param_delim:
                        i += 1
                    continue
        cur.append(ch)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        tokens.append(tail)
    return tokens


# ---- Public output shape ---------------------------------------------------

@dataclass
class IgesNode:
    part_number: str
    part_name: str
    description: str | None
    quantity: int
    level: int
    sort_order: int
    parent_index: int | None
    de_pointer: int  # for debug / source_ref


def parse_iges(data: bytes | str) -> list[IgesNode]:
    """Parse IGES file content → flat ordered list of IgesNode (DFS pre-order).

    Returns [] if the file has no SubfigureDefinition entities — caller should
    decide whether to fall back to a single placeholder node named after the file.
    """
    text = _decode_bytes(data) if isinstance(data, bytes) else data
    sections = _split_sections(text)
    if not sections["D"] or not sections["P"]:
        return []

    param_delim, record_delim = _parse_global_delimiters(sections["G"])
    dir_entries = _parse_directory(sections["D"])
    p_index = _build_pd_index(sections["P"])

    # Index DEs by pointer for cross-lookup
    de_by_pointer: dict[int, _DirEntry] = {de.de_pointer: de for de in dir_entries}

    # ---- Pass 1: parse Subfigure Definitions (308) -------------------------
    # subfigure_def[de_pointer] = { name, contained_de_pointers: list[int] }
    subfig_defs: dict[int, dict] = {}
    for de in dir_entries:
        if de.entity_type != T_SUBFIGURE_DEF:
            continue
        body = _read_pd_record(p_index, de.pd_pointer, de.pd_line_count, record_delim)
        tokens = _tokenize_pd(body, param_delim)
        # tokens[0] = entity type ("308"), then DEPTH, NAME, N, DE1, DE2, ..., DEN
        if len(tokens) < 4:
            continue
        try:
            # tokens[1] = depth
            name = tokens[2]
            n_entities = int(tokens[3])
        except (ValueError, IndexError):
            continue
        contained: list[int] = []
        for t in tokens[4 : 4 + n_entities]:
            try:
                contained.append(int(t))
            except ValueError:
                continue
        subfig_defs[de.de_pointer] = {
            "name": name or de.label or f"SUBFIG#{de.de_pointer}",
            "label": de.label,
            "contained": contained,
        }

    if not subfig_defs:
        return []

    # ---- Pass 2: parse Subfigure Instances (408) → map DE → target 308 -----
    instance_target: dict[int, int] = {}  # 408_de_pointer → 308_de_pointer
    for de in dir_entries:
        if de.entity_type != T_SUBFIGURE_INSTANCE:
            continue
        body = _read_pd_record(p_index, de.pd_pointer, de.pd_line_count, record_delim)
        tokens = _tokenize_pd(body, param_delim)
        # tokens[0] = "408", tokens[1] = DE pointer to a 308
        if len(tokens) < 2:
            continue
        try:
            target = int(tokens[1])
        except ValueError:
            continue
        if target in subfig_defs:
            instance_target[de.de_pointer] = target

    # ---- Pass 3: build parent → child edges with quantity ------------------
    edges: dict[tuple[int, int], int] = {}      # (parent_308, child_308) → qty
    children_order: dict[int, list[int]] = {}   # parent_308 → ordered children (preserve first-seen order)
    seen_child: dict[int, set[int]] = {}

    for parent_de, info in subfig_defs.items():
        for contained_ptr in info["contained"]:
            child_308: int | None = None
            target_de = de_by_pointer.get(contained_ptr)
            if target_de is None:
                continue
            if target_de.entity_type == T_SUBFIGURE_INSTANCE:
                child_308 = instance_target.get(contained_ptr)
            elif target_de.entity_type == T_SUBFIGURE_DEF:
                # 308 directly nested (rare but legal)
                child_308 = contained_ptr
            if child_308 is None or child_308 == parent_de:
                continue
            edges[(parent_de, child_308)] = edges.get((parent_de, child_308), 0) + 1
            if child_308 not in seen_child.setdefault(parent_de, set()):
                children_order.setdefault(parent_de, []).append(child_308)
                seen_child[parent_de].add(child_308)

    # ---- Pass 4: roots = 308s never appearing as child --------------------
    all_defs = set(subfig_defs.keys())
    children_set = {c for (_p, c) in edges.keys()}
    roots = sorted(all_defs - children_set)
    if not roots:
        roots = sorted(all_defs)[:1]  # cyclic; pick one to avoid empty BOM

    # ---- Pass 5: DFS flatten ----------------------------------------------
    out: list[IgesNode] = []
    sort_counter = [0]

    def _walk(de_ptr: int, level: int, parent_index: int | None, qty: int) -> None:
        info = subfig_defs[de_ptr]
        idx = len(out)
        sort_counter[0] += 1
        out.append(
            IgesNode(
                part_number=info["label"] or f"SUBFIG-{de_ptr}",
                part_name=info["name"],
                description=None,
                quantity=qty,
                level=level,
                sort_order=sort_counter[0],
                parent_index=parent_index,
                de_pointer=de_ptr,
            )
        )
        # Cycle guard
        ancestors: set[int] = set()
        cur = parent_index
        while cur is not None:
            ancestors.add(out[cur].de_pointer)
            cur = out[cur].parent_index
        ancestors.add(de_ptr)

        for child_de in children_order.get(de_ptr, []):
            if child_de in ancestors:
                continue
            child_qty = edges.get((de_ptr, child_de), 1)
            _walk(child_de, level + 1, idx, child_qty)

    for r in roots:
        _walk(r, 0, None, 1)

    return out


def iges_nodes_to_dicts(nodes: Iterable[IgesNode]) -> list[dict]:
    """Normalize IgesNode list → BOMNode-compatible dicts."""
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
            "source_ref": {"type": "iges_subfig", "de": n.de_pointer},
            "sort_order": n.sort_order,
            "_parent_index": n.parent_index,
        }
        for n in nodes
    ]

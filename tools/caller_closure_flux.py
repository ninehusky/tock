#!/usr/bin/env python3
"""
caller_closure_flux.py — same interface as caller_closure.py, but consumes
per-crate JSON callgraphs from `cargo flux -- -Femit-callgraph=...` instead
of doing ripgrep-based textual analysis.

The callgraph JSON format (per-crate) is:
    {
      "crate": "kernel",
      "edges": [
        {
          "caller": "<allocator::AppMemoryAllocator<R> as core::fmt::Display>::fmt",
          "callee": "core::fmt::Arguments::<'a>::from_str",
          "edge_kind": "direct" | "trait_dispatch_resolved" | ...,
          "span": "kernel/src/allocator.rs:86"
        },
        ...
      ]
    }

We load all JSONs given via --callgraphs (default: /tmp/*.cg.json), invert the
edges (callee → list of callers), and walk upward from each locally-proven row's
panic-bearing function.

The challenge is **matching panic_sites.md's `file:line` to the JSON's
`def_path_str`-style names**. panic_sites.md only gives us a file path + line.
We use:
    1. The "enclosing fn" extracted from the source (simple name).
    2. Suffix-match against every node in the inverse graph: a node matches
       if its def_path_str ends with `::<simple_name>` or `::<simple_name><'…`.
    3. If multiple nodes match, we emit a list (likely impl-trait variants).

Usage:
    tools/.venv/bin/python tools/caller_closure_flux.py
        [--panic-sites tools/panic_sites.md]
        [--callgraphs /tmp/*.cg.json]
        [--out tools/caller_closure_flux.json]
        [--max-depth 6]
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path


# Re-use the panic_sites.md parser from caller_closure.py
ROW_RE = re.compile(r"^\| `0x")
LOC_RE = re.compile(r"\[([^\]]+)\]")
FN_DECL_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]+\))?\s+)?"
    r"(?:unsafe\s+)?(?:const\s+)?(?:async\s+)?"
    r"fn\s+([A-Za-z_][A-Za-z0-9_]*)"
)


def parse_panic_sites(md_path: Path):
    for line in md_path.read_text().splitlines():
        if not ROW_RE.match(line):
            continue
        parts = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(parts) > 8:
            head, tail, middle = parts[:4], parts[-3:], parts[4:-3]
            parts = head + [" | ".join(middle)] + tail
        if len(parts) < 8:
            continue
        addr, flavor, location, source, notes, blockers, status, assignee = parts
        if status != "locally proven":
            continue
        m = LOC_RE.match(location)
        loc_str = m.group(1) if m else location
        if ":" in loc_str:
            file_path, _, line_str = loc_str.rpartition(":")
            try:
                line_no = int(line_str)
            except ValueError:
                file_path, line_no = loc_str, None
        else:
            file_path, line_no = loc_str, None
        yield {
            "addr": addr.strip("`"),
            "flavor": flavor.strip("`"),
            "file": file_path,
            "line": line_no,
            "notes": notes,
        }


def enclosing_fn(file_path: Path, line_no):
    info = enclosing_fn_extent(file_path, line_no)
    return info[0] if info else None


def enclosing_fn_extent(file_path: Path, line_no):
    """Return (name, decl_line_1indexed, end_line_1indexed) for the fn that
    contains `line_no`, or None if no enclosing fn can be located.

    Walks backward from `line_no` to find a `fn NAME(` declaration, then forward
    via brace tracking to find the matching closing `}`. Both lines are 1-indexed.
    """
    if not file_path.exists() or line_no is None:
        return None
    lines = file_path.read_text().splitlines()
    line_no = min(line_no, len(lines))
    decl = None
    fn_name = None
    for i in range(line_no - 1, -1, -1):
        m = FN_DECL_RE.match(lines[i])
        if m:
            decl = i + 1
            fn_name = m.group(1)
            break
    if decl is None:
        return None
    depth = 0
    seen_open = False
    end_line = len(lines)
    for k in range(decl - 1, len(lines)):
        for ch in lines[k]:
            if ch == "{":
                depth += 1
                seen_open = True
            elif ch == "}":
                depth -= 1
                if seen_open and depth == 0:
                    end_line = k + 1
                    break
        if seen_open and depth == 0:
            break
    return fn_name, decl, end_line


def simple_name(def_path: str) -> str:
    """Return the final segment of a def_path_str (after the last `::` outside
    of any angle brackets), with generics stripped."""
    depth = 0
    last_split = -1
    for i, ch in enumerate(def_path):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == ":" and depth == 0 and i + 1 < len(def_path) and def_path[i + 1] == ":":
            last_split = i
    tail = def_path if last_split == -1 else def_path[last_split + 2:]
    tail = re.sub(r"::<[^>]+>", "", tail)
    tail = re.sub(r"<[^>]+>", "", tail)
    return tail


# ---------------------------------------------------------------------------
# Callgraph loading & inversion
# ---------------------------------------------------------------------------

def load_callgraphs(paths):
    """Load multiple per-crate JSONs and merge. Returns:
        forward: callee → list of edges (caller, edge_kind, span, crate)
        reverse: callee → list of edges with caller flipped to "from"
        nodes:   set of all def_path_strs seen as caller or callee
        edges_by_crate: stats
    """
    forward = defaultdict(list)
    reverse = defaultdict(list)
    nodes = set()
    edges_by_crate = defaultdict(int)
    for p in paths:
        with open(p) as f:
            d = json.load(f)
        crate = d.get("crate", "?")
        for e in d.get("edges", []):
            caller, callee = e["caller"], e["callee"]
            forward[caller].append({**e, "crate": crate})
            reverse[callee].append({**e, "crate": crate})
            nodes.add(caller)
            nodes.add(callee)
            edges_by_crate[crate] += 1
    return forward, reverse, nodes, dict(edges_by_crate)


def match_panic_fn_disambiguated(simple_name: str, nodes, forward_graph, expected_file: str):
    """Like match_panic_fn but, when multiple matches exist, keep only those
    whose forward edges (where they are the caller) have spans in
    `expected_file`. This rules out unrelated `new`/`next`/etc. instances.
    """
    candidates = match_panic_fn(simple_name, nodes)
    if len(candidates) <= 1:
        return candidates
    filtered = []
    for c in candidates:
        for e in forward_graph.get(c, []):
            span = e.get("span", "")
            if span and ":" in span:
                fp = span.rsplit(":", 1)[0]
                # Match if the edge's span file matches the panic_sites file.
                if fp == expected_file or fp.endswith("/" + expected_file) or expected_file.endswith("/" + fp):
                    filtered.append(c)
                    break
    return filtered if filtered else candidates


def match_panic_fn(simple_name: str, nodes):
    """Find all def_path_strs whose last segment matches simple_name.

    A def_path looks like: `foo::bar::Baz::method` or
    `<foo::Bar as core::Trait>::method` or
    `foo::Bar::<'a>::method`. We strip generic params from each segment and
    compare the final segment (after the last `::` not inside `<>`).
    """
    matches = []
    for n in nodes:
        # Strip outer <Bar as Trait> wrappers — the final segment is what matters.
        # The last `::` outside of any angle brackets is the split point.
        depth = 0
        last_split = -1
        for i, ch in enumerate(n):
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
            elif ch == ":" and depth == 0 and i + 1 < len(n) and n[i + 1] == ":":
                last_split = i
        if last_split == -1:
            tail = n
        else:
            tail = n[last_split + 2:]
        # Strip generic params on the tail (`fn::<T>`)
        tail_stripped = re.sub(r"::<[^>]+>", "", tail)
        tail_stripped = re.sub(r"<[^>]+>", "", tail_stripped)
        if tail_stripped == simple_name:
            matches.append(n)
    return matches


# ---------------------------------------------------------------------------
# Flux include cross-reference (lifted from caller_closure.py)
# ---------------------------------------------------------------------------

def load_flux_includes(workspace: Path):
    out = []
    for cargo in workspace.rglob("Cargo.toml"):
        if "target" in cargo.parts:
            continue
        if any(p == "flux" for p in cargo.parts):
            continue
        try:
            txt = cargo.read_text()
        except Exception:
            continue
        if "[package.metadata.flux]" not in txt:
            continue
        sec_start = txt.find("[package.metadata.flux]")
        rest = txt[sec_start:]
        sec_end = rest.find("\n[", 1)
        section_raw = rest[:sec_end] if sec_end != -1 else rest
        # Strip lines starting with `#` (comments) so commented-out `include`
        # entries don't get picked up.
        section = "\n".join(
            ln for ln in section_raw.splitlines()
            if not ln.lstrip().startswith("#")
        )
        enabled = re.search(r"\benabled\s*=\s*true\b", section) is not None
        includes = []
        m = re.search(r"\binclude\s*=\s*\[([^\]]*)\]", section, re.DOTALL)
        if m:
            for s in re.findall(r'"([^"]+)"', m.group(1)):
                includes.append(s)
        out.append({"crate_root": cargo.parent, "enabled": enabled, "includes": includes})
    return out


def flux_status_for_path(def_path: str, span: str, flux_inventory):
    """Given a def_path_str + span, return a Flux-coverage classification."""
    if span and ":" in span:
        file_part = span.rsplit(":", 1)[0]
        file_abs = Path(file_part).resolve()
    else:
        file_abs = None
    best = None
    if file_abs:
        for entry in flux_inventory:
            try:
                file_abs.relative_to(entry["crate_root"].resolve())
            except ValueError:
                continue
            if best is None or len(str(entry["crate_root"])) > len(str(best["crate_root"])):
                best = entry
    if best is None:
        return "not_in_flux_crate"
    if not best["enabled"]:
        return "flux_disabled"
    # If the crate has no `include` filter, Flux checks every file by default.
    if not best["includes"]:
        return "whole_crate_default"
    rel = file_abs.relative_to(best["crate_root"].resolve())
    rel_str = str(rel)
    for pat in best["includes"]:
        if pat.startswith("def:"):
            substr = pat[len("def:"):]
            if substr in def_path:
                return "def_included"
        elif pat.startswith("glob:") or "/" in pat or "*" in pat:
            globpat = pat.removeprefix("glob:")
            if rel_str == globpat or rel_str.endswith("/" + globpat):
                return "whole_file_included"
            if globpat in rel_str:
                return "whole_file_included"
    return "not_included"


# ---------------------------------------------------------------------------
# Strict-rule preprocessing: trust markers and assume() calls
# ---------------------------------------------------------------------------

TRUSTED_ATTR_RE = re.compile(r"#\[flux_rs::trusted(?:_impl)?\b")
TRUST_REASON_RE = re.compile(r'reason\s*=\s*"((?:[^"\\]|\\.)*)"')
FLUX_ASSUME_RE = re.compile(r"flux_support::assume\s*\(")
BARE_ASSUME_RE = re.compile(r"(?:^|[^.\w])assume\s*\(")
USE_FLUX_ASSUME_RE = re.compile(r"use\s+flux_support::(?:\{[^}]*\b)?assume\b")

# Structured reason prefixes. `caller_audit_skip` is the workflow tag for
# "stopped the audit balloon here, not a permanent boundary" — see
# docs/panic_stats/caller_proven_handoff.md §6.
KNOWN_REASON_TAGS = (
    "blocked_cell",
    "blocked_dyn",
    "blocked_ice",
    "blocked_reentrancy",
    "blocked_stdlib",
    "blocked_hw_trust",
    "caller_audit_skip",
)


def classify_reason(reason: str) -> str:
    """Return the structured tag prefix (e.g. `blocked_cell`) from a trust
    reason string, or `unstructured` if no known prefix matches, or
    `no_reason` if the reason is empty."""
    if not reason:
        return "no_reason"
    head = reason.split(":", 1)[0].strip()
    if head in KNOWN_REASON_TAGS:
        return head
    return "unstructured"


def find_trusted_fns(workspace: Path):
    """Scan the workspace and return a dict mapping
        (resolved_file_str, fn_simple_name) -> reason_string
    for every fn marked `#[flux_rs::trusted(reason = ...)]`, or sitting inside
    a `#[flux_rs::trusted_impl(reason = ...)]` block. Reason is "" when the
    attribute didn't carry one. Approximate: brace-tracked impl propagation,
    no full parse."""
    trusted = {}
    for rs in workspace.rglob("*.rs"):
        if "target" in rs.parts:
            continue
        if any(p == "flux" for p in rs.parts):
            continue
        try:
            lines = rs.read_text().splitlines()
        except Exception:
            continue
        if not any("#[flux_rs::trusted" in ln for ln in lines):
            continue
        file_key = str(rs.resolve())
        n = len(lines)
        i = 0
        while i < n:
            if TRUSTED_ATTR_RE.search(lines[i]):
                is_impl_attr = "trusted_impl" in lines[i]
                m_reason = TRUST_REASON_RE.search(lines[i])
                reason = m_reason.group(1) if m_reason else ""
                j = i + 1
                while j < n:
                    s = lines[j].strip()
                    if not s or s.startswith("//") or s.startswith("#["):
                        j += 1
                        continue
                    break
                if j >= n:
                    i += 1
                    continue
                target = lines[j]
                m = FN_DECL_RE.match(target)
                if m and not is_impl_attr:
                    trusted[(file_key, m.group(1))] = reason
                    i = j + 1
                    continue
                head = target.split("{", 1)[0]
                if "impl " in head or head.lstrip().startswith("impl"):
                    depth = 0
                    seen_open = False
                    end_line = j
                    for k in range(j, n):
                        for ch in lines[k]:
                            if ch == "{":
                                depth += 1
                                seen_open = True
                            elif ch == "}":
                                depth -= 1
                                if seen_open and depth == 0:
                                    end_line = k
                                    break
                        if seen_open and depth == 0:
                            break
                    for k in range(j, end_line + 1):
                        mm = FN_DECL_RE.match(lines[k])
                        if mm:
                            trusted[(file_key, mm.group(1))] = reason
                    i = end_line + 1
                    continue
                i = j + 1
            else:
                i += 1
    return trusted


def find_assumes(workspace: Path):
    """Scan the workspace and return file_key (resolved str) → sorted list of
    1-indexed line numbers where a `flux_support::assume(...)` (or bare
    `assume(...)` in files that import it) is invoked."""
    by_file = {}
    for rs in workspace.rglob("*.rs"):
        if "target" in rs.parts:
            continue
        if any(p == "flux" for p in rs.parts):
            continue
        try:
            text = rs.read_text()
        except Exception:
            continue
        if "assume(" not in text:
            continue
        bare_ok = bool(USE_FLUX_ASSUME_RE.search(text))
        lines = text.splitlines()
        hits = []
        for i, ln in enumerate(lines, 1):
            stripped = ln.lstrip()
            if stripped.startswith("//"):
                continue
            if FLUX_ASSUME_RE.search(ln):
                hits.append(i)
            elif bare_ok and BARE_ASSUME_RE.search(ln) and "flux_support::assume" not in ln:
                hits.append(i)
        if hits:
            by_file[str(rs.resolve())] = hits
    return by_file


def resolve_span_file(workspace: Path, span: str):
    """Convert an edge span ("relative/path.rs:42") to (resolved_file_str, line_no)
    or (None, None) if it can't be resolved."""
    if not span or ":" not in span:
        return None, None
    file_part, _, line_str = span.rpartition(":")
    try:
        line_no = int(line_str)
    except ValueError:
        return None, None
    p = (workspace / file_part).resolve()
    if not p.exists():
        return None, None
    return str(p), line_no


# ---------------------------------------------------------------------------
# Closure traversal (Flux-graph version)
# ---------------------------------------------------------------------------

def compute_closure_flux(seed_def_paths, reverse_graph, flux_inventory, max_depth=6):
    """BFS upward through `reverse_graph` starting from one or more matches
    for the panic-bearing function."""
    nodes = {}
    edges = []
    unresolved = []
    terminated = []
    seen = set()
    queue = deque((n, 0) for n in seed_def_paths)
    for n in seed_def_paths:
        nodes[n] = {"depth": 0}
        seen.add(n)
    while queue:
        node, depth = queue.popleft()
        if depth >= max_depth:
            terminated.append({"fn": node, "reason": f"max_depth ({max_depth}) reached"})
            continue
        callers = reverse_graph.get(node, [])
        if not callers:
            terminated.append({"fn": node, "reason": "no callers in any loaded callgraph (entry point, or callgraph unavailable for caller's crate)"})
            continue
        for e in callers:
            edge = {
                "caller": e["caller"],
                "callee": node,
                "edge_kind": e["edge_kind"],
                "span": e.get("span"),
                "crate": e.get("crate"),
            }
            edges.append(edge)
            if e["caller"] not in seen:
                seen.add(e["caller"])
                nodes[e["caller"]] = {
                    "depth": depth + 1,
                    "flux_status": flux_status_for_path(e["caller"], e.get("span"), flux_inventory),
                }
                queue.append((e["caller"], depth + 1))
    return {"nodes": nodes, "edges": edges, "unresolved": unresolved, "terminated": terminated}


def coverage_verdict(closure):
    if not closure["edges"]:
        return "no_callers_in_loaded_graphs"
    edge_kinds = {e["edge_kind"] for e in closure["edges"]}
    unresolved_edges = [e for e in closure["edges"] if e["edge_kind"] not in ("direct", "trait_dispatch_resolved")]
    if not unresolved_edges:
        # Every edge is resolvable. Check if every NON-seed node has flux status.
        non_seed = [n for n, info in closure["nodes"].items() if info.get("depth", 0) > 0]
        all_flux = all(
            closure["nodes"][n].get("flux_status") in ("def_included", "whole_file_included", "whole_crate_default")
            for n in non_seed
        )
        return "caller_proven_candidate" if all_flux else "partial_no_flux"
    return "partial_unresolved_edges"


def apply_strict_rules(row, closure, workspace, forward, trusted_set, assume_map):
    """For a row whose candidate verdict is `caller_proven_candidate`, run the
    three strict rules:
      (1) The enclosing fn body contains no `flux_support::assume(...)` calls.
      (2) No function in the transitive caller closure is `#[flux_rs::trusted]`
          (or inside a `#[flux_rs::trusted_impl]` block).
      (3) No call site in the closure has a `flux_support::assume(...)` earlier
          in its enclosing fn (heuristic for "caller discharged the precondition
          via a runtime panic instead of a static proof").

    Returns (new_verdict, blockers_dict). If no rule fires, blockers is None and
    the verdict stays `caller_proven_candidate`. Otherwise the verdict downgrades
    to the highest-priority blocker (body > trust > caller-assume) and the
    blockers dict carries concrete evidence for each rule that fired."""
    blockers = {}

    panic_file = (workspace / row["file"]).resolve()
    extent = enclosing_fn_extent(panic_file, row["line"])
    if extent:
        _, decl, end = extent
        hits = assume_map.get(str(panic_file), [])
        body_assumes = [h for h in hits if decl <= h <= end]
        if body_assumes:
            blockers["body_assumes"] = [
                {"file": row["file"], "line": h} for h in body_assumes
            ]

    workspace_resolved = str(workspace.resolve())
    trusted_nodes = []
    for node, info in closure.get("nodes", {}).items():
        out_edges = forward.get(node, [])
        src_file = None
        for e in out_edges:
            f, _ = resolve_span_file(workspace, e.get("span"))
            if f:
                src_file = f
                break
        if not src_file:
            continue
        name = simple_name(node)
        if (src_file, name) in trusted_set:
            rel = src_file
            if src_file.startswith(workspace_resolved + "/"):
                rel = src_file[len(workspace_resolved) + 1:]
            reason = trusted_set[(src_file, name)]
            trusted_nodes.append({
                "def_path": node,
                "file": rel,
                "reason": reason,
                "reason_tag": classify_reason(reason),
            })
    if trusted_nodes:
        blockers["trusted_nodes"] = trusted_nodes

    caller_assumes = []
    for e in closure.get("edges", []):
        span = e.get("span")
        f, line_no = resolve_span_file(workspace, span)
        if not f or line_no is None:
            continue
        hits = assume_map.get(f, [])
        if not hits:
            continue
        ext = enclosing_fn_extent(Path(f), line_no)
        if not ext:
            continue
        _, decl, _ = ext
        before = [h for h in hits if decl <= h <= line_no]
        if before:
            caller_assumes.append({
                "caller": e["caller"],
                "callee": e["callee"],
                "call_site": span,
                "assume_lines": before,
            })
    if caller_assumes:
        blockers["caller_assumes"] = caller_assumes

    if not blockers:
        return "caller_proven_candidate", None
    if "body_assumes" in blockers:
        return "blocked_body_assume", blockers
    if "trusted_nodes" in blockers:
        return "blocked_trust_boundary", blockers
    return "blocked_caller_assume", blockers


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    here = Path(__file__).resolve().parent
    workspace_default = here.parent
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panic-sites", default=here / "panic_sites.md", type=Path)
    ap.add_argument("--callgraphs", nargs="+", default=None,
                    help="Per-crate callgraph JSONs (default: /tmp/*.cg.json)")
    ap.add_argument("--out", default=here / "caller_closure_flux.json", type=Path)
    ap.add_argument("--workspace", default=workspace_default, type=Path)
    ap.add_argument("--max-depth", default=6, type=int)
    args = ap.parse_args()

    callgraph_paths = args.callgraphs
    if not callgraph_paths:
        callgraph_paths = sorted(Path("/tmp").glob("*.cg.json"))
    callgraph_paths = [Path(p) for p in callgraph_paths]
    if not callgraph_paths:
        print("ERROR: no callgraph JSONs found", file=sys.stderr)
        return 1
    print(f"Loading {len(callgraph_paths)} callgraph(s):", file=sys.stderr)
    for p in callgraph_paths:
        print(f"  {p}", file=sys.stderr)

    forward, reverse, nodes, edges_by_crate = load_callgraphs(callgraph_paths)
    print(f"Loaded {sum(edges_by_crate.values())} edges across {len(nodes)} nodes, "
          f"from crates: {list(edges_by_crate)}", file=sys.stderr)

    flux_inventory = load_flux_includes(args.workspace.resolve())

    print("Scanning workspace for trust markers and assume() calls...", file=sys.stderr)
    trusted_set = find_trusted_fns(args.workspace.resolve())
    assume_map = find_assumes(args.workspace.resolve())
    print(f"  trusted fns: {len(trusted_set)}", file=sys.stderr)
    print(f"  files with assumes: {len(assume_map)} "
          f"(total assume calls: {sum(len(v) for v in assume_map.values())})", file=sys.stderr)

    rows = list(parse_panic_sites(args.panic_sites))
    print(f"Parsed {len(rows)} locally-proven rows", file=sys.stderr)

    results = []
    crates_covered = set(edges_by_crate)
    for i, row in enumerate(rows, 1):
        if row["line"] is None:
            results.append({**row, "panic_fn": None, "coverage": "no_line_in_panic_sites"})
            continue
        file_abs = args.workspace / row["file"]
        fn_name = enclosing_fn(file_abs, row["line"])
        if fn_name is None:
            results.append({**row, "panic_fn": None, "coverage": "no_enclosing_fn"})
            continue
        matches = match_panic_fn_disambiguated(fn_name, nodes, forward, row["file"])
        # Filter matches by spans to those that originate in row["file"]
        # (best effort — we don't have function-def-spans, only call-site spans).
        if not matches:
            # Try to figure out which crate the row is in; if it's a crate
            # we don't have a callgraph for, report that distinctly.
            row_top = "/".join(row["file"].split("/")[:2])
            row_crate_guess = row["file"].split("/")[0] + "_" + row["file"].split("/")[1] if "/" in row["file"] else row["file"]
            results.append({
                **row,
                "panic_fn": fn_name,
                "coverage": "no_def_path_match",
                "row_crate_guess": row_crate_guess,
                "missing_callgraph": row_crate_guess not in crates_covered,
            })
            continue
        closure = compute_closure_flux(matches, reverse, flux_inventory,
                                       max_depth=args.max_depth)
        verdict = coverage_verdict(closure)
        blockers = None
        if verdict == "caller_proven_candidate":
            verdict, blockers = apply_strict_rules(
                row, closure, args.workspace.resolve(),
                forward, trusted_set, assume_map,
            )
        results.append({
            **row,
            "panic_fn": fn_name,
            "matched_def_paths": matches,
            "coverage": verdict,
            "closure": closure,
            "blockers": blockers,
        })

    args.out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {len(results)} entries to {args.out}", file=sys.stderr)

    md_path = args.out.with_suffix(".md")
    write_summary_md(results, edges_by_crate, md_path)
    print(f"Wrote summary to {md_path}", file=sys.stderr)

    by_verdict = defaultdict(int)
    for r in results:
        by_verdict[r["coverage"]] += 1
    print("\n=== Coverage verdict distribution ===", file=sys.stderr)
    for v, n in sorted(by_verdict.items(), key=lambda kv: -kv[1]):
        print(f"  {n:4d}  {v}", file=sys.stderr)
    return 0


def write_summary_md(results, edges_by_crate, path):
    L = [
        "# Caller-closure report (Flux-callgraph driven)",
        "",
        "Generated by `tools/caller_closure_flux.py`. Consumes per-crate callgraph",
        "JSONs from `cargo flux -- -Femit-callgraph=/tmp/{crate}.cg.json`.",
        "",
        "## Coverage verdicts",
        "",
        "- `caller_proven_candidate`: every caller edge is `direct` or",
        "  `trait_dispatch_resolved`, every transitive caller is in a Flux",
        "  include list, AND the row passes all three strict rules below.",
        "  Strongest verdict the static analysis can produce.",
        "- `blocked_body_assume`: the enclosing fn body contains",
        "  `flux_support::assume(...)` — the proof is conditional on a runtime",
        "  panic. Discharge the assume(s) before upgrading the row.",
        "- `blocked_trust_boundary`: at least one function in the transitive",
        "  caller closure is `#[flux_rs::trusted]` (or sits inside a",
        "  `#[flux_rs::trusted_impl]` block). Un-trust the boundary (verify its",
        "  body) before upgrading.",
        "- `blocked_caller_assume`: at least one call site in the closure has a",
        "  `flux_support::assume(...)` earlier in its enclosing fn — the caller",
        "  discharges the precondition at runtime. Replace with a static proof.",
        "- `partial_no_flux`: edges resolve, but some caller is NOT Flux-included.",
        "  Concrete worklist: add those callers to the appropriate include list.",
        "- `partial_unresolved_edges`: some edge has an unresolvable kind",
        "  (typically `trait_dispatch_unresolved`).",
        "- `no_def_path_match`: panic-fn's simple name didn't match any node in",
        "  the loaded callgraphs — most likely a callgraph for that crate wasn't",
        "  loaded.",
        "- `no_callers_in_loaded_graphs`: function exists in graph but has no",
        "  inbound edges. Either an entry point, or its callers live in a crate",
        "  whose callgraph wasn't loaded.",
        "- `no_line_in_panic_sites` / `no_enclosing_fn`: parsing failures.",
        "",
        "## Loaded callgraphs",
        "",
        "| Crate | Edges |",
        "|---|---:|",
    ]
    for crate, n in sorted(edges_by_crate.items(), key=lambda kv: -kv[1]):
        L.append(f"| `{crate}` | {n} |")
    L += [
        "",
        "## Verdict distribution",
        "",
        "| Verdict | Count |",
        "|---|---:|",
    ]
    bv = defaultdict(int)
    for r in results:
        bv[r["coverage"]] += 1
    for v, n in sorted(bv.items(), key=lambda kv: -kv[1]):
        L.append(f"| `{v}` | {n} |")
    L += [
        "",
        "## Per-row",
        "",
        "| Addr | Location | panic_fn | Verdict | Closure |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        loc = f"{r['file']}:{r['line']}" if r.get("line") else r["file"]
        cl = r.get("closure") or {}
        n_nodes = len(cl.get("nodes") or {})
        n_edges = len(cl.get("edges") or [])
        L.append(
            f"| `{r['addr']}` | {loc} | `{r['panic_fn'] or '—'}` "
            f"| {r['coverage']} | {n_nodes}n / {n_edges}e |"
        )
    L += [
        "",
        "## Strict-rule blockers (per row)",
        "",
        "Each row below failed at least one of the three strict rules required for",
        "`caller_proven`. Discharging the listed evidence is the precondition for",
        "upgrading the row.",
        "",
    ]
    blocked_rows = [r for r in results if r["coverage"].startswith("blocked_")]
    if not blocked_rows:
        L.append("_None._")
    for r in blocked_rows:
        loc = f"{r['file']}:{r['line']}" if r.get("line") else r["file"]
        L.append(f"### `{r['addr']}` — {r.get('panic_fn') or '—'} ({loc})")
        L.append(f"- verdict: `{r['coverage']}`")
        bl = r.get("blockers") or {}
        if bl.get("body_assumes"):
            L.append("- body assumes:")
            for h in bl["body_assumes"]:
                L.append(f"  - `{h['file']}:{h['line']}`")
        if bl.get("trusted_nodes"):
            L.append("- trusted callers:")
            for t in bl["trusted_nodes"]:
                short = t["def_path"][:90] + "…" if len(t["def_path"]) > 90 else t["def_path"]
                tag = t.get("reason_tag", "no_reason")
                reason = t.get("reason") or "(no reason)"
                L.append(f"  - `{short}` (in `{t['file']}`)")
                L.append(f"    - tag: `{tag}` — {reason}")
        if bl.get("caller_assumes"):
            L.append("- caller-site assumes:")
            for ca in bl["caller_assumes"]:
                short = ca["caller"][:90] + "…" if len(ca["caller"]) > 90 else ca["caller"]
                L.append(f"  - `{short}` at `{ca['call_site']}` "
                         f"(assumes at lines {ca['assume_lines']})")
        L.append("")
    L += [
        "",
        "## Trust-marker leverage report",
        "",
        "For each `#[flux_rs::trusted(reason = \"<tag>: ...\")]` marker hit during",
        "audits, this section aggregates by tag and by individual marker. Sort key:",
        "how many `blocked_trust_boundary` rows would unblock if the marker were",
        "discharged. Highest-leverage infrastructure work surfaces at the top.",
        "",
    ]
    tag_counter = Counter()
    marker_rows = defaultdict(set)
    marker_tag = {}
    marker_reason = {}
    for r in results:
        if r["coverage"] != "blocked_trust_boundary":
            continue
        bl = r.get("blockers") or {}
        for t in bl.get("trusted_nodes", []):
            tag = t.get("reason_tag", "no_reason")
            tag_counter[tag] += 1
            key = (t.get("file", ""), t["def_path"])
            marker_rows[key].add(r["addr"])
            marker_tag[key] = tag
            marker_reason[key] = t.get("reason") or ""
    if not tag_counter:
        L.append("_No `blocked_trust_boundary` rows present._")
    else:
        L.append("### Rows blocked, grouped by reason tag")
        L.append("")
        L.append("| Tag | Blocked rows (count) |")
        L.append("|---|---:|")
        for tag, n in sorted(tag_counter.items(), key=lambda kv: -kv[1]):
            L.append(f"| `{tag}` | {n} |")
        L += ["", "### Individual markers, sorted by impact", ""]
        for key, addrs in sorted(marker_rows.items(), key=lambda kv: -len(kv[1])):
            file_path, def_path = key
            tag = marker_tag.get(key, "no_reason")
            reason = marker_reason.get(key, "") or "(no reason)"
            addr_list = sorted(addrs)
            shown = ", ".join(f"`{a}`" for a in addr_list[:8])
            if len(addr_list) > 8:
                shown += f", … (+{len(addr_list) - 8})"
            L.append(f"- **`{def_path}`** ({file_path})")
            L.append(f"  - tag: `{tag}` — {reason}")
            L.append(f"  - unblocks {len(addr_list)} row(s): {shown}")
    L += [
        "",
        "## Worklist: `partial_no_flux` rows — Flux-coverage gaps to fill",
        "",
    ]
    gap_callers = defaultdict(list)
    for r in results:
        if r["coverage"] != "partial_no_flux":
            continue
        for fn, info in (r.get("closure") or {}).get("nodes", {}).items():
            if info.get("flux_status") not in ("def_included", "whole_file_included") and info.get("depth", 0) > 0:
                gap_callers[fn].append(r["addr"])
    for fn, addrs in sorted(gap_callers.items(), key=lambda kv: -len(kv[1])):
        short_fn = fn[:90] + "…" if len(fn) > 90 else fn
        L.append(f"- `{short_fn}` blocks {len(addrs)} row(s): "
                 f"{', '.join('`'+a+'`' for a in addrs[:6])}"
                 f"{'…' if len(addrs) > 6 else ''}")
    path.write_text("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())

"""Tool-permission policy: pure functions, unit-tested.

Every tool call of every task (subagents included) goes through
Policy.evaluate() twice over: once from the PreToolUse hook the console
registers with Claude Code, and again from the permission prompt Claude Code
sends when it would ask a human. The order of precedence is fixed:

  1. hard blocks  : console's own port, forbidden paths, locked rules
  2. deny rules   : a refusal always beats an authorisation
  3. input constraints (e.g. Odoo model whitelist) and folder confinement
  4. ask rules    : human validation in the task window
  5. allow rules
  6. "validate writes" of the preset -> ask
  7. otherwise    : "default" -> the preset's policy for unlisted tools
"""
from __future__ import annotations

import fnmatch
import ipaddress
import json
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .config import InputConstraint, Preset, ToolRule

LOCKED_RULES = [
    ToolRule(pattern="mcp__*__delete_record", decision="deny", locked=True,
             note="Suppression Odoo : refusée en permanence"),
]

FILE_TOOLS = {
    "Read": "file_path", "Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path", "NotebookRead": "notebook_path",
    "Glob": "path", "Grep": "path", "LS": "path",
}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
INTERACTIVE_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
READ_VERBS = {"get", "list", "search", "read", "fetch", "query", "aggregate", "describe",
              "find", "count", "lookup", "view", "show", "check", "status", "info",
              "explore", "browse", "resolve", "whoami", "current"}
# A verb from this set ANYWHERE in an MCP tool name makes it a write
# ("list_and_delete_messages" is a delete, whatever its first word says).
WRITE_VERBS = {"create", "update", "delete", "remove", "purge", "destroy", "drop", "wipe", "erase",
               "trash", "send", "post", "publish", "write", "upload", "move", "rename", "archive",
               "confirm", "cancel", "modify", "edit", "patch", "put", "reply", "forward", "insert",
               "add", "set", "approve", "merge", "revoke", "reset", "clear", "transfer", "pay",
               "submit", "invite", "share", "unsubscribe", "kill", "deploy", "restore", "import",
               "execute", "overwrite", "replace", "assign"}
PS_ALIASES = {
    "rm": "remove-item", "del": "remove-item", "erase": "remove-item", "rd": "remove-item",
    "ri": "remove-item", "rmdir": "remove-item", "iwr": "invoke-webrequest", "curl": "invoke-webrequest",
    "wget": "invoke-webrequest", "irm": "invoke-restmethod", "iex": "invoke-expression",
    "gc": "get-content", "cat": "get-content", "type": "get-content", "sc": "set-content",
    "ac": "add-content", "mv": "move-item", "move": "move-item", "mi": "move-item", "cp": "copy-item",
    "copy": "copy-item", "cpi": "copy-item", "ls": "get-childitem", "dir": "get-childitem",
    "gci": "get-childitem", "saps": "start-process", "start": "start-process", "kill": "stop-process",
    "spps": "stop-process", "ni": "new-item", "md": "new-item", "mkdir": "new-item",
}
_WRAPPER = re.compile(r"^(?:cmd(?:\.exe)?\s+/[ck]\s+|(?:powershell|pwsh)(?:\.exe)?\s+(?:-\S+\s+)*?-c(?:ommand)?\s+"
                      r"|(?:ba|z|da)?sh(?:\.exe)?\s+-c\s+)(.+)$", re.I | re.S)
_OPAQUE = re.compile(r"(?i)-enc(?:odedcommand)?\b|invoke-expression|\biex\b|frombase64string")

_RULE_RX = re.compile(r"^\s*([^()]+?)\s*(?:\((.*)\))?\s*$")
_CLI_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


# ---------------------------------------------------------------- helpers

def parse_rule(rule: str) -> tuple[str, str | None]:
    m = _RULE_RX.match(rule or "")
    if not m:
        return rule.strip(), None
    name, spec = m.group(1).strip(), m.group(2)
    # Claude Code syntax: "mcp__server" means every tool of that server.
    if name.startswith("mcp__") and name.count("__") == 1:
        name += "__*"
    return name, spec


def is_mcp(tool: str) -> bool:
    return tool.startswith("mcp__")


def mcp_parts(tool: str) -> tuple[str, str]:
    rest = tool[5:]
    server, _, name = rest.partition("__")
    return server, name


def name_words(tool: str) -> list[str]:
    """Lower-case words of a tool's own name (the part after the MCP server)."""
    name = mcp_parts(tool)[1] if is_mcp(tool) else tool
    return [w.lower() for w in re.split(r"[_\-.\s]+|(?<=[a-z0-9])(?=[A-Z])", name) if w]


def write_words(tool: str) -> set[str]:
    return {w for w in name_words(tool) if w in WRITE_VERBS} if is_mcp(tool) else set()


def is_write(tool: str) -> bool:
    """Built-in writers and shells; MCP tools with a write verb anywhere or no read verb first."""
    if tool in WRITE_TOOLS or tool in SHELL_TOOLS:
        return True
    if is_mcp(tool):
        words = name_words(tool)
        if any(w in WRITE_VERBS for w in words):
            return True
        return not (words and words[0] in READ_VERBS)
    return False


def iter_strings(value, depth: int = 0):
    if depth > 12:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from iter_strings(v, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from iter_strings(v, depth + 1)


def norm(p: str) -> str:
    """Canonical, case-folded, forward-slash path for comparisons.

    realpath resolves 8.3 short names (DOCUME~1), symlinks and junctions, so a
    path cannot dodge a comparison by being spelled differently.
    """
    p = os.path.expandvars(os.path.expanduser(p))
    try:
        p = os.path.realpath(p)
    except (OSError, ValueError):
        p = os.path.abspath(p)
    return os.path.normcase(p).replace("\\", "/").rstrip("/") or "/"


def resolve(p: str, workdir: str) -> str:
    p = os.path.expandvars(os.path.expanduser(p))
    if re.match(r"^/[A-Za-z]/", p):  # //c/Users style (Claude Code absolute rules)
        p = f"{p[1]}:{p[2:]}"
    if not os.path.isabs(p):
        p = os.path.join(workdir, p)
    return norm(p)


def within(path: str, roots: list[str]) -> bool:
    p = norm(path)
    for r in roots:
        r = norm(r)
        if p == r or p.startswith(r + "/"):
            return True
    return False


def glob_to_regex(pattern: str) -> re.Pattern:
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?"); i += 3; continue
        if pattern.startswith("**", i):
            out.append(".*"); i += 2; continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    # Case-insensitive everywhere: Windows and macOS file systems ignore case, so a
    # rule must not be dodged by spelling a path differently.
    return re.compile("".join(out) + r"\Z", re.IGNORECASE)


def split_commands(cmd: str) -> list[str] | None:
    """Split a shell line into simple commands outside quotes.

    Returns None when the line holds command substitution, which no allow rule
    may vouch for.
    """
    if "$(" in cmd or "`" in cmd:
        return None
    parts, buf, quote = [], [], None
    for c in cmd:
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
            buf.append(c)
        elif c in ";|&\n\r":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _has_redirect(sub: str) -> bool:
    s = re.sub(r"\d?>&\d", "", sub)
    s = re.sub(r"(?i)>\s*(nul|/dev/null)\b", "", s)
    return bool(re.search(r"[<>]", s))


def _clean(cmd: str) -> str:
    return " ".join(cmd.split())


def _unquote(s: str) -> str:
    s = s.strip()
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"" else s


def _expand_alias(cmd: str) -> str:
    head, _, rest = cmd.partition(" ")
    alias = PS_ALIASES.get(head.lower())
    return f"{alias} {rest}".strip() if alias else cmd


def shell_variants(cmd: str) -> list[str]:
    """Every simple command a shell line may run: split, whitespace-normalised,
    PowerShell aliases expanded, cmd /c / powershell -Command / bash -c opened."""
    out, todo, depth = [], [cmd], 0
    while todo and depth < 6:
        depth += 1
        line = todo.pop()
        for sub in split_commands(line) or [line]:
            sub = _clean(sub)
            out.append(sub)
            alias = _expand_alias(sub)
            if alias != sub:
                out.append(alias)
            m = _WRAPPER.match(sub)
            if m:
                todo.append(_unquote(m.group(1)))
    return list(dict.fromkeys(out))


def match_command(spec: str, command: str, ci: bool = False) -> bool:
    spec, cmd = _clean(spec), _clean(command)
    if ci:
        spec, cmd = spec.lower(), cmd.lower()
    if spec.endswith(":*"):
        prefix = spec[:-2].strip()
        return cmd == prefix or cmd.startswith(prefix + " ")
    if "*" in spec or "?" in spec:
        return fnmatch.fnmatchcase(cmd, spec)
    return cmd == spec


def _tool_matches(name: str, tool: str, broad: bool = False) -> bool:
    """Tool-name match. `broad` (deny and ask rules): an MCP pattern such as
    mcp__*__send* also catches the verb in the middle of a name (gmail_send_draft)."""
    if fnmatch.fnmatchcase(tool, name):
        return True
    if broad and is_mcp(tool) and name.startswith("mcp__"):
        srv_pat, _, act_pat = name[5:].partition("__")
        m = re.fullmatch(r"\*?([a-z]+)\*", act_pat.lower())
        return bool(m) and fnmatch.fnmatchcase(mcp_parts(tool)[0], srv_pat) and m.group(1) in name_words(tool)
    return False


def _vouches(name: str, tool: str) -> bool:
    """A generic allow pattern (with *) never vouches for an MCP write tool whose
    write verbs it does not spell out: mcp__*__list* cannot allow list_and_delete."""
    if not is_mcp(tool) or not any(c in name for c in "*?["):
        return True
    verbs = write_words(tool)
    if not verbs:
        return True
    spelled = set(re.findall(r"[a-z]+", name.lower()))
    return verbs <= spelled


def get_path(data, dotted: str):
    cur = data
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def summarize_target(tool: str, inp: dict) -> str:
    """One-line human label of what a tool call touches."""
    inp = inp or {}
    if tool in FILE_TOOLS and inp.get(FILE_TOOLS[tool]):
        s = inp[FILE_TOOLS[tool]]
        if tool in ("Glob", "Grep") and inp.get("pattern"):
            s = f"{inp['pattern']}  dans {s}"
        return str(s)
    if tool in ("Glob", "Grep"):
        return str(inp.get("pattern", ""))
    if tool in SHELL_TOOLS:
        return str(inp.get("command", ""))
    if tool == "WebFetch":
        return str(inp.get("url", ""))
    if tool == "WebSearch":
        return str(inp.get("query", ""))
    if tool in ("Task", "Agent"):
        return " · ".join(str(x) for x in (inp.get("subagent_type"), inp.get("description")) if x)
    if tool == "Skill":
        return str(inp.get("skill") or inp.get("command") or "")
    if tool == "TodoWrite":
        return f"{len(inp.get('todos') or [])} élément(s)"
    if is_mcp(tool):
        bits = [f"{k}={inp[k]}" for k in ("model", "record_id", "query", "q", "to", "subject", "name")
                if k in inp and not isinstance(inp[k], (dict, list))]
        return ", ".join(bits)[:200]
    for v in iter_strings(inp):
        return v[:200]
    return ""


# ---------------------------------------------------------------- forbidden paths

def _compile_forbidden(pattern: str):
    p = pattern.strip().replace("\\", "/")
    if not p:
        return None
    if re.match(r"^[A-Za-z]:/", p) or p.startswith("/") or p.startswith("%") or p.startswith("$"):
        return ("abs", norm(p).lower())  # compared lower-case: macOS is case-insensitive too
    if p.startswith("~/"):
        p = p[2:]
    while p.startswith("**/"):
        p = p[3:]
    p = p.rstrip("/")
    if p.endswith("/**"):
        p = p[:-3]
    segs = [s.lower() for s in p.split("/") if s]
    return ("segs", segs) if segs else None


_TOKEN_SPLIT = re.compile(r"[\s'\"`=,;|&<>()\[\]{}]+")
_PATH_KEY = re.compile(r"path|file|dir|folder|uri|url|root|cwd|location|source|target|dest", re.I)
_PATHLIKE = re.compile(r"^(?:[a-z]:|[~.%$])|/")
_HOST_PORT = re.compile(r"(\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.\-]+)\s*:\s*(\d{1,5})(?!\d)")
_HOST_TOKEN = re.compile(r"\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.\-]+")


def _loose_ipv4(h: str):
    """BSD inet_aton forms: 127.1, 0x7f000001, 2130706433, 0177.0.0.1."""
    parts = h.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    nums = []
    for p in parts:
        try:
            if p.startswith("0x"):
                nums.append(int(p[2:], 16))
            elif len(p) > 1 and p.startswith("0"):
                nums.append(int(p, 8))
            else:
                nums.append(int(p, 10))
        except ValueError:
            return None
    *head, last = nums
    if any(n > 255 for n in head) or last >= 256 ** (4 - len(head)):
        return None
    val = 0
    for n in head:
        val = val * 256 + n
    return ipaddress.IPv4Address(val * 256 ** (4 - len(head)) + last)


def is_loopback(host: str) -> bool:
    h = host.strip().strip("[]").rstrip(".").lower()
    if not h:
        return False
    if h in ("localhost", "ip6-localhost", "ip6-loopback") or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
        mapped = getattr(ip, "ipv4_mapped", None)
        return (mapped or ip).is_loopback or ip.is_unspecified
    except ValueError:
        pass
    if re.fullmatch(r"(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+)){0,3}", h):
        ip = _loose_ipv4(h)
        return bool(ip and ip.is_loopback)
    return False


def _tokens(s: str) -> list[str]:
    low = s.replace("\\", "/").lower()
    return [t for t in _TOKEN_SPLIT.split(low) if t]


def _contains_segments(segs: list[str], pat: list[str]) -> bool:
    n = len(pat)
    for i in range(len(segs) - n + 1):
        if all(fnmatch.fnmatchcase(segs[i + j], pat[j]) for j in range(n)):
            return True
    return False


# ---------------------------------------------------------------- policy

@dataclass
class Verdict:
    decision: str            # allow | ask | deny | default
    reason: str = ""
    rule: str = ""
    locked: bool = False


@dataclass
class PolicyContext:
    workdir: str
    add_dirs: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    ports: list[int] = field(default_factory=list)


class Policy:
    def __init__(self, preset: Preset, rules: list[ToolRule], constraints: list[InputConstraint],
                 ctx: PolicyContext):
        self.preset = preset
        self.ctx = ctx
        self.constraints = constraints
        locked = LOCKED_RULES + [r for r in rules if r.locked and r.decision == "deny"]
        self.locked = [r.pattern for r in locked]
        free = [r for r in rules if not r.locked]
        self.deny = preset.deny + [r.pattern for r in free if r.decision == "deny"]
        self.ask = preset.approval + [r.pattern for r in free if r.decision == "ask"]
        self.allow = preset.allow + [r.pattern for r in free if r.decision == "allow"]
        self._forbidden = [(pat, c) for pat in ctx.forbidden if (c := _compile_forbidden(pat))]
        self._port_rx = [re.compile(rf"(?<!\d){p}(?!\d)") for p in ctx.ports]

    # -- rule matching
    def _spec_matches(self, tool: str, spec: str, inp: dict, sub: str | None = None) -> bool:
        spec = spec.strip()
        if spec in ("", "*"):
            return True
        if tool in SHELL_TOOLS:
            return match_command(spec, sub if sub is not None else str(inp.get("command", "")),
                                 ci=(tool == "PowerShell"))
        if tool == "WebFetch" and spec.startswith("domain:"):
            host = (urlparse(str(inp.get("url", ""))).hostname or "").lower()
            dom = spec[7:].strip().lower()
            return host == dom or host.endswith("." + dom)
        if tool in FILE_TOOLS:
            val = inp.get(FILE_TOOLS[tool]) or self.ctx.workdir
            target = resolve(str(val), self.ctx.workdir)
            if spec.startswith("//"):
                spec = spec[1:]
            pat = resolve(spec, self.ctx.workdir) if not spec.startswith("**") else spec
            return bool(glob_to_regex(pat.replace("\\", "/")).match(target))
        return any(fnmatch.fnmatchcase(s, spec) for s in iter_strings(inp))

    def match_any(self, rules: list[str], tool: str, inp: dict, broad: bool = True) -> str | None:
        """First rule matching the call.

        broad (deny / ask rules): for shells, any command the line may run
        matches — aliases, extra spaces and cmd /c / powershell -Command / bash -c
        wrappers included, and Bash and PowerShell rules apply to both shells.
        Not broad (allow rules): a generic MCP pattern must spell out the write
        verbs of the tool it would allow.
        """
        variants = shell_variants(str(inp.get("command", ""))) if tool in SHELL_TOOLS else None
        for r in rules:
            name, spec = parse_rule(r)
            cross_shell = broad and variants is not None and name in SHELL_TOOLS
            if not (_tool_matches(name, tool, broad) or cross_shell):
                continue
            if not broad and not _vouches(name, tool):
                continue
            if spec is None:
                if cross_shell and name != tool:
                    continue  # a bare "PowerShell" rule is about that tool, not every shell
                return r
            if variants is not None:
                ci = "PowerShell" in (name, tool)
                if spec.strip() in ("", "*") or any(match_command(spec, v, ci=ci) for v in variants):
                    return r
            elif self._spec_matches(tool, spec, inp):
                return r
        return None

    def match_allow(self, rules: list[str], tool: str, inp: dict) -> str | None:
        """Allow rules must vouch for EVERY simple command of a shell line, and
        nothing opaque (wrappers, encoded commands, substitutions, redirections)."""
        if tool not in SHELL_TOOLS:
            return self.match_any(rules, tool, inp, broad=False)
        for r in rules:
            name, spec = parse_rule(r)
            if _tool_matches(name, tool) and (spec is None or spec.strip() in ("", "*")):
                return r
        cmd = str(inp.get("command", ""))
        if _OPAQUE.search(cmd):
            return None
        subs = split_commands(cmd)
        if not subs:
            return None
        first = None
        for s in (_clean(x) for x in subs):
            if _has_redirect(s) or _WRAPPER.match(s):
                return None
            hit = None
            for r in rules:
                name, spec = parse_rule(r)
                if spec and _tool_matches(name, tool) and match_command(spec, s, ci=(tool == "PowerShell")):
                    hit = r
                    break
            if not hit:
                return None
            first = first or hit
        return first

    # -- checks
    def self_access(self, inp: dict) -> bool:
        """Any loopback spelling with the console's port, together or apart."""
        if not self._port_rx:
            return False
        for s in iter_strings(inp):
            for m in _HOST_PORT.finditer(s):
                if any(rx.fullmatch(m.group(2)) for rx in self._port_rx) and is_loopback(m.group(1)):
                    return True
            if any(rx.search(s) for rx in self._port_rx) and any(is_loopback(t) for t in _HOST_TOKEN.findall(s)):
                return True
        return False

    @staticmethod
    def _path_strings(tool: str, inp: dict) -> list[tuple[str, bool]]:
        """(text, check_every_token) pairs to inspect for forbidden paths.

        Built-in tools: the fields that designate a location (never file
        contents). Every other tool (MCP): every string leaf, whatever its key;
        free text with spaces is only inspected through its path-like tokens.
        """
        if tool in FILE_TOOLS:
            keys = [FILE_TOOLS[tool], "path", "glob"] + (["pattern"] if tool == "Glob" else [])
            return [(str(inp[k]), True) for k in dict.fromkeys(keys) if isinstance(inp.get(k), str)]
        if tool in SHELL_TOOLS:
            return [(str(inp.get("command", "")), True)]
        if tool == "WebFetch":
            return [(str(inp.get("url", "")), True)]
        if tool in ("TodoWrite", "WebSearch", "Task", "Agent", "Skill", "AskUserQuestion", "ExitPlanMode"):
            return []  # prose for Claude; the tool calls it leads to are checked themselves
        out = []

        def walk(v, key="", depth=0):
            if depth > 12:
                return
            if isinstance(v, dict):
                for k, x in v.items():
                    walk(x, str(k), depth + 1)
            elif isinstance(v, list):
                for x in v:
                    walk(x, key, depth + 1)
            elif isinstance(v, str):
                out.append((v, bool(_PATH_KEY.search(key)) or not re.search(r"\s", v)))
        walk(inp)
        return out

    def forbidden_hit(self, inp: dict, tool: str = "") -> str | None:
        strings = self._path_strings(tool, inp)
        if not strings:
            return None
        canon: dict[str, str] = {}
        for pattern, (kind, val) in self._forbidden:
            for s, every in strings:
                if kind == "abs" and val in s.replace("\\", "/").lower():
                    return pattern
                for t in _tokens(s):
                    if t.startswith(("-", "http:", "https:", "mailto:")):
                        continue
                    pathlike = bool(_PATHLIKE.search(t))
                    if not (every or pathlike):
                        continue
                    if kind == "segs" and _contains_segments([x for x in t.split("/") if x], val):
                        return pattern
                    if pathlike or tool in FILE_TOOLS:
                        # canonical form: 8.3 short names, links and junctions resolved
                        cand = canon.get(t) or canon.setdefault(t, resolve(t, self.ctx.workdir).lower())
                        if kind == "abs" and (cand == val or cand.startswith(val + "/")):
                            return pattern
                        if kind == "segs" and _contains_segments([x for x in cand.split("/") if x], val):
                            return pattern
        return None

    def constraint_violation(self, tool: str, inp: dict) -> str | None:
        for c in self.constraints:
            if not _tool_matches(c.tool, tool):
                continue
            v = get_path(inp, c.path)
            sval = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else (None if v is None else str(v))
            if c.allowed is not None and (sval is None or sval not in c.allowed):
                return f"{c.path} = {sval!s} non autorisé (permis : {', '.join(c.allowed)})" + (f" — {c.note}" if c.note else "")
            if c.forbidden and sval is not None and sval in c.forbidden:
                return f"{c.path} = {sval} interdit" + (f" — {c.note}" if c.note else "")
        return None

    def confine_violation(self, tool: str, inp: dict) -> str | None:
        mode = self.preset.confine
        key = FILE_TOOLS.get(tool)
        if mode == "none" or not key or (mode == "writes" and tool not in WRITE_TOOLS):
            return None
        val = inp.get(key)
        if not val:
            return None
        if not within(resolve(str(val), self.ctx.workdir), [self.ctx.workdir, *self.ctx.add_dirs]):
            return f"{val} est hors du dossier de travail"
        return None

    # -- verdict
    def evaluate(self, tool: str, inp: dict | None) -> Verdict:
        inp = inp if isinstance(inp, dict) else {}
        if self.self_access(inp):
            return Verdict("deny", "Les agents n'ont pas accès à la console elle-même.", locked=True)
        hit = self.forbidden_hit(inp, tool)
        if hit:
            return Verdict("deny", f"Chemin interdit par la configuration ({hit}).", rule=hit, locked=True)
        r = self.match_any(self.locked, tool, inp)
        if r:
            return Verdict("deny", f"Refusé en permanence ({r}).", rule=r, locked=True)
        r = self.match_any(self.deny, tool, inp)
        if r:
            return Verdict("deny", f"Refusé par la règle {r}.", rule=r)
        why = self.constraint_violation(tool, inp)
        if why:
            return Verdict("deny", f"Contrainte non respectée : {why}.")
        why = self.confine_violation(tool, inp)
        if why:
            return Verdict("deny", f"Dossier non autorisé : {why}.")
        r = self.match_any(self.ask, tool, inp)
        if r:
            return Verdict("ask", f"Validation requise ({r}).", rule=r)
        r = self.match_allow(self.allow, tool, inp)
        if r:
            return Verdict("allow", f"Autorisé par la règle {r}.", rule=r)
        if self.preset.validate_writes and is_write(tool):
            return Verdict("ask", "Écriture ou commande : validation humaine du preset.")
        return Verdict("default", "Non listé.")


# ---------------------------------------------------------------- CLI flags

def cli_rule(rule: str) -> str | None:
    """The subset of rules Claude Code itself understands, passed as flags.

    Globs on tool names and path rules stay in the console's own hook: the CLI
    flags are only defence in depth.
    """
    name, spec = parse_rule(rule)
    if not _CLI_NAME.fullmatch(name):
        return None
    if spec is None:
        return name
    spec = spec.strip()
    # Only plain prefixes ("git status:*", "npm run *") go to the CLI; other globs stay in the hook.
    if name == "Bash" and re.fullmatch(r"[^*?,]+(:\*| \*)?", spec):
        return f"Bash({spec})"
    return None


def _dedupe(items):
    seen, out = set(), []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def cli_permission_args(preset: Preset, rules: list[ToolRule]) -> list[str]:
    args = ["--permission-mode", preset.mode]
    if preset.tools:
        args += ["--tools", ",".join(preset.tools)]
    free = [r for r in rules if not r.locked]
    allow = _dedupe(map(cli_rule, preset.allow + [r.pattern for r in free if r.decision == "allow"]))
    deny = _dedupe(map(cli_rule, preset.deny + [r.pattern for r in rules if r.decision == "deny"]
                       + [r.pattern for r in LOCKED_RULES]))
    if allow:
        args += ["--allowedTools", ",".join(allow)]
    if deny:
        args += ["--disallowedTools", ",".join(deny)]
    return args


def policy_context(workdir: str, add_dirs: list[str], forbidden: list[str], data_dir: str,
                   ports: list[int]) -> PolicyContext:
    return PolicyContext(workdir=workdir, add_dirs=list(add_dirs),
                         forbidden=[*forbidden, str(Path(data_dir).resolve())], ports=ports)

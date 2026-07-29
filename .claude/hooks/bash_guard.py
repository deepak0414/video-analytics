#!/usr/bin/env python3
"""PreToolUse guard for Bash (workflow-trust-plan.md WT.3).

Blocks gate-bypass, self-approval, and audit-tampering commands inside Claude
Code sessions. Exit 2 = block (stderr is shown to the agent as the reason);
exit 0 = allow.

DESIGN (learned the hard way across the PR 3 review rounds): decisions are made
by TOKENIZING the command the way a shell does, never by matching flag positions
or by splitting on separator characters with a regex. Every regex attempt leaked
— flag-position anchors missed `commit --amend -n` and `commit -m x -n`, and
`[^|;&\\n]*` / `re.split(r"[|;&]")` broke on an ampersand inside a quoted commit
message, disabling the guard with no intent to evade. Tokenization handles
quoting, clustering, ordering, and value-consuming flags uniformly.

Bash is still a full shell — this layer is hardening against realistic evasion,
not a proof; the un-bypassable layer is CI (WT.5).
"""
import json
import os
import re
import shlex
import sys

ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Every write-redirect spelling shlex can emit as one token: >, >>, 1>, 2>>,
# >| (noclobber override), >& / &> / &>> (fd duplication + merge). Round-11
# major: a hardcoded 5-item set missed >| and >&.
REDIRECT = re.compile(r"^(?:\d*(?:>>?\|?|>&\d*)|&>>?)$")

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # malformed payload: never brick the session
cmd = (payload.get("tool_input") or {}).get("command", "") or ""

ROOT = os.environ.get("CLAUDE_PROJECT_DIR", ".")
# Human maintenance window (`touch .guard-override`, honored by path_guard too).
# Scope is NARROW: it relaxes only the gate-machinery write rules — sentinels,
# waivers, approvals, ledgers, PR self-approval and push protection stay live.
OVERRIDE = os.path.exists(os.path.join(ROOT, ".guard-override"))


def block(reason):
    print(f"BLOCKED: {reason}\nCommand was: {cmd}", file=sys.stderr)
    sys.exit(2)


def segments(command):
    """Split into command segments on shell operators WITHOUT cutting inside
    quoted arguments (the round-10 critical). Returns lists of tokens.

    NEWLINES ARE COMMAND SEPARATORS: shlex's whitespace_split swallows them, so
    a multi-line command collapsed into one segment and every rule keyed on the
    first command name skipped lines 2+ (round-15 critical). Lines are split
    first — but only OUTSIDE quotes, so a quoted multi-line message stays one
    token."""
    toks = []
    for line in _split_lines_outside_quotes(command):
        toks += _tokenize(line) + ["\n"]
    return _group(toks)


def _split_lines_outside_quotes(command):
    """Logical command lines, following POSIX quoting rules:
    - inside single quotes nothing escapes and newlines are literal data;
    - a backslash-newline is a LINE CONTINUATION and is removed (round-16
      critical: keeping it prefixed a newline onto the next token, so `git
      commit \\<nl>-n` no longer matched any flag rule);
    - a newline outside quotes separates commands.
    """
    lines, cur, quote, i, n = [], [], None, 0, len(command)
    while i < n:
        ch = command[i]
        if quote == "'":                      # single quotes: literal, no escapes
            cur.append(ch)
            if ch == "'":
                quote = None
            i += 1
            continue
        if ch == "\\" and i + 1 < n:          # escape (unquoted or double-quoted)
            if command[i + 1] == "\n":
                i += 2                        # line continuation: join lines
                continue
            cur.append(ch)
            cur.append(command[i + 1])
            i += 2
            continue
        if quote == '"':
            cur.append(ch)
            if ch == '"':
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            cur.append(ch)
            i += 1
            continue
        if ch == "\n":
            lines.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    lines.append("".join(cur))
    return lines


def _tokenize(command):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:  # unbalanced quotes: fall back, still quote-naive
        return command.split()


def _group(toks):
    # Operators AND grouping tokens end a segment, so a write verb wrapped in a
    # subshell or brace group is still analyzed as its own command (round-11
    # major: `( touch .commit-approved )` had the group token read as the verb).
    segs, cur = [], []
    for t in toks:
        if t and (all(ch in "|;&\n()" for ch in t) or t in ("{", "}")):
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    return segs


def norm(p):
    p = p.strip()
    while p.startswith("./"):
        p = p[2:]
    return p if p in ("/", "~", "~/") else p.rstrip("/")


def parts(p):
    return [c for c in norm(p).split("/") if c and c != "."]


# --- protected artifacts -----------------------------------------------------
# Never writable by the agent, maintenance window or not: human authority
# (sentinels), the approval/cache state the gates read, the audit trail, and the
# test runner the gates depend on.
SENTINELS = {".commit-approved", ".guard-override", ".review-approved",
             ".stop-gate-green"}


def is_always_protected(path):
    ps = parts(path)
    if not ps:
        return False
    if ps[-1] in SENTINELS:
        return True
    if "reviews" in ps:                                    # the ledger trail
        return True
    if ps[-3:] == [".venv", "bin", "pytest"] or ps[-1] == ".venv":
        return True  # the test runner IS the gates — and so is its venv
    if ps[-2:] == [".git", "config"] or (".git" in ps and "hooks" in ps):
        return True                                        # git-side gate config
    return False


def is_machinery(path):
    ps = parts(path)
    if not ps:
        return False
    if {".githooks", ".claude", ".github"} & set(ps):
        return True
    if ps[-2:-1] == ["scripts"] and ps[-1] in (
            "agent-review.sh", "review_scope_hash.sh", "setup-hooks.sh",
            # The WT.7 gate IS these two files, and CI runs the PR's own copy —
            # unprotected, a PR could neuter the gate it must pass, and the same
            # PR would then sail through it (PR 4 backstop major).
            "check_critical_paths.sh", "critical_paths.txt"):
        return True
    return False


# Commands that write to the files they are given. (Readers like `cat` are NOT
# here — reading protected files stays allowed; their redirection targets are
# caught separately.)
WRITE_CMDS = {"tee", "cp", "mv", "rm", "ln", "truncate", "dd", "chmod", "chown",
              "touch", "install", "sed"}
# Flags whose VALUE is a separate token (so the token after them is data, not a
# flag). Only genuinely arg-taking ones belong here: -S/--gpg-sign takes an
# ATTACHED optional keyid and -o/--only takes nothing, so listing them swallowed
# the next token and let `git commit -S -n` through (round-12 major).
VALUE_FLAGS = {"-m", "--message", "-F", "--file", "-c", "--reedit-message",
               "-C", "--reuse-message", "--author", "--date",
               "-t", "--template", "--cleanup", "--fixup", "--squash",
               "--trailer"}


def command_name(argv):
    """(basename of the command, its remaining args), skipping leading ENV=value
    assignments. ONE definition, used by every rule — a rule that read seg[0]
    directly was disabled by an env prefix (`LC_ALL=C rm -rf .git`, round 13)."""
    for i, t in enumerate(argv):
        if ENV_ASSIGN.match(t):
            continue
        return os.path.basename(t), argv[i + 1:]
    return None, []


# git global flags that consume the NEXT token, so the subcommand is found by
# position rather than by searching for a known word anywhere in the segment
# (a branch literally named `commit` redirected the scan away from push).
GIT_VALUE_GLOBALS = {"-c", "-C", "--git-dir", "--work-tree", "--namespace",
                     "--exec-path", "--config-env"}


def git_subcommand(seg):
    """(subcommand, its args) for a git invocation, else (None, [])."""
    name, rest = command_name(seg)
    if name != "git":
        return None, []
    i = 0
    while i < len(rest):
        t = rest[i]
        if t in GIT_VALUE_GLOBALS:
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        return t, rest[i + 1:]
    return None, []


def write_targets(toks):
    """Paths this segment could write to: redirection targets, plus the operands
    of a write command (sed only counts with an in-place flag)."""
    targets, redirect_next = [], False
    for t in toks:
        if redirect_next:
            targets.append(t)
            redirect_next = False
            continue
        if REDIRECT.match(t):
            redirect_next = True
    # Drop redirection operators and their targets (already collected above).
    argv, skip_next = [], False
    for t in toks:
        if skip_next:
            skip_next = False
            continue
        if REDIRECT.match(t):
            skip_next = True
            continue
        argv.append(t)
    name, rest = command_name(argv)
    if name in WRITE_CMDS:
        if name == "sed" and not any(
                f == "--in-place" or f.startswith("--in-place=")
                or (f.startswith("-") and not f.startswith("--") and "i" in f)
                for f in rest if f.startswith("-")):
            return targets
        if name == "dd":  # operand syntax: of=<file> is the write target
            targets += [t.split("=", 1)[1] for t in rest if t.startswith("of=")]
            return targets
        targets += [t for t in rest if not t.startswith("-")]
    return targets


for seg in segments(cmd):
    if not seg:
        continue
    lowered = [t.lower() for t in seg]

    # 1. Human-only override tokens (quoting is already stripped by the lexer).
    for t in seg:
        if t.startswith("AGENT_REVIEW=") and t.split("=", 1)[1] == "skip":
            block("the review waiver is human-only (P6).")
        # The hooks honor ANY non-empty value ([ -z ] tests), so `=yes` waives
        # just as `=1` does — match the invariant, not the literal (round 14).
        if "=" in t and t.split("=", 1)[0] in (
                "ALLOW_TEST_REMOVAL", "ALLOW_MAIN_COMMIT", "ALLOW_LEDGER_EDIT",
        ) and t.split("=", 1)[1] != "":
            block("ALLOW_* overrides are human-only; explain the need and ask.")
        if "core.hookspath" in t.lower() and not OVERRIDE:
            block("changing hooksPath disables the trust gates; ask the user.")

    # 2. Writes to protected artifacts / gate machinery.
    for target in write_targets(seg):
        if is_always_protected(target):
            block(f"{target} is a human-only artifact or gate state (sentinels, "
                  "approval/cache files, reviews/ ledgers, the test runner, git "
                  "config) — the hooks and the user write these, not the agent.")
        if is_machinery(target) and not OVERRIDE:
            block(f"{target} is trust-gate machinery — writable only in a human-"
                  "opened maintenance session (.guard-override).")

    # 3. Destructive deletes of the repo/system.
    vname, vargs = command_name(seg)
    if vname == "rm" and any(
            f == "--recursive" or (not f.startswith("--") and ("r" in f or "R" in f))
            for f in vargs if f.startswith("-")):
        for t in vargs:
            if t.startswith("-"):
                continue
            ps, n = parts(t), norm(t)
            if n in ("/", "~") or n.startswith("~/") or ".git" in ps or \
                    ".githooks" in ps or ".claude" in ps:
                block("destructive delete of repo/system paths.")

    # 4. gh self-approval / self-merge / self-labeling (env-prefix safe; attached
    # and short flag spellings included — round-14 major).
    # GitHub treats a PR as an issue, so `gh issue edit <PR#> --add-label` and the
    # REST route both apply REVIEW labels to a PR: gating only `gh pr` left the
    # WT.7 label gate self-servable (PR 4 backstop major).
    gname, gargs = command_name(seg)
    REVIEW_LABELS = ("human-reviewed", "golden-verified")

    # Any HTTP client can hit the same endpoints with the same token
    # (`gh auth token` + curl was demonstrated by the PR 4 backstop). Blocking
    # them is a SPEED BUMP against accident, not a guarantee: with a shared
    # credential, anything the human can do the agent can do. See D9 in
    # workflow-trust-plan.md — the real fix is a separate, label-incapable
    # credential for agent sessions.
    if gname in ("curl", "wget", "http", "https", "httpie", "xh"):
        joined_http = " ".join(gargs)
        if "api.github.com" in joined_http or "/repos/" in joined_http:
            if any(lbl in joined_http for lbl in REVIEW_LABELS) or re.search(
                    r"/(labels|reviews|merge)\b", joined_http) or any(
                    m in joined_http for m in ("addLabelsToLabelable", "addLabels",
                                               "mergePullRequest",
                                               "addPullRequestReview")):
                block("applying review labels / approvals / merges over HTTP is "
                      "human-only (P5). NB: this is defense-in-depth, not a "
                      "guarantee — see D9.")
    # Every spelling that PRINTS the credential, not just `gh auth token`:
    # `gh auth status --show-token` / `-t` emits the same secret.
    if gname == "gh" and gargs[:1] == ["auth"]:
        if gargs[1:2] == ["token"] or any(
                t in ("--show-token", "-t") or t.startswith("--show-token=")
                for t in gargs[1:]):
            block("extracting the human's credential is forbidden — it is the key "
                  "to every human-only action (labels, approvals, merges).")
    # Persistent flags may sit between the noun and the action
    # (`gh issue -R owner/repo edit …`), so positions are parsed, not assumed —
    # the same leak class git_subcommand() exists to avoid (PR 4 backstop major).
    GH_VALUE_FLAGS = {"-R", "--repo", "-H", "--hostname", "-F", "--field",
                      "-f", "--raw-field", "-X", "--method", "-H", "--header",
                      "-q", "--jq", "-t", "--template", "-i", "--input"}

    def gh_words(args):
        out, i = [], 0
        while i < len(args):
            t = args[i]
            if t in GH_VALUE_FLAGS:
                i += 2
                continue
            if t.startswith("-"):
                i += 1
                continue
            out.append(t)
            i += 1
        return out

    if gname == "gh":
        words = gh_words(gargs)
        # `gh alias set m 'pr merge'` then `gh m 14` renames the action past every
        # noun/action rule. Aliases are opaque to a static guard, so block their
        # CREATION rather than pretend to resolve them (finalize-round minor).
        if words[:1] == ["alias"] and words[1:2] in (["set"], ["import"]):
            block("installing gh aliases hides the real subcommand from the "
                  "guards (`alias set`, and `alias import` from a file); ask "
                  "the user instead.")
        noun = words[0] if words else ""
        action = words[1] if len(words) > 1 else ""
        # Any mention of a review label anywhere in a gh command is decisive:
        # -f 'labels[]=human-reviewed', a GraphQL mutation body, --add-label=…
        if noun == "pr" and action == "merge":
            block("merging PRs is a human action in this repo.")
        # Reading label state is legitimate (checking whether the human has
        # applied one); only APPLYING is human-only (PR 4 backstop minor).
        # Applying a label is signalled by a FLAG (--add-label/--approve) or an
        # API field — never by prose. Blocking any command whose text mentions a
        # label name false-blocked `gh pr create/edit --body "<filled template>"`,
        # because the repo's own template names both labels: the guard was
        # breaking the lifecycle steps that open and fix a PR. Prose is not an
        # action; the explicit-flag rule below is what actually gates this.
        if noun in ("pr", "issue") and action in ("edit", "review") and any(
                t in ("--approve", "-a", "--add-label")
                or t.startswith(("--add-label=", "--approve="))
                for t in gargs):
            block("review labels/approvals are human-only (P3/P5).")
        # REST/GraphQL: routes with an explicit verb, PATCH/POST on an issue or
        # pull (PRs ARE issues), and label/review/merge GraphQL mutations.
        if noun == "api":
            joined = " ".join(gargs)
            # A route alone is not an action: `gh api …/issues/N/labels` with no
            # method is a GET, and reading label state is legitimate (row 89).
            # Only an explicit mutating method — or a GraphQL mutation — counts.
            explicit_method = re.search(
                r"(^|\s)(-X\s*|--method[= ])([A-Z]+)", joined)
            # gh api sends POST automatically when fields are supplied, so
            # `gh api …/labels -f 'labels[]=x'` is a mutation with no -X at all.
            has_fields = any(
                t in ("-f", "-F", "--field", "--raw-field", "--input")
                or t.startswith(("-f", "-F", "--field=", "--raw-field=", "--input="))
                for t in gargs)
            verb = (explicit_method.group(3) if explicit_method
                    else ("POST" if has_fields else "GET"))
            if verb != "GET" and any(
                    s in joined for s in ("/labels", "/reviews", "/merge")) or any(
                    s in joined for s in ("addLabelsToLabelable", "addLabels",
                                          "mergePullRequest", "addPullRequestReview")):
                block("applying labels / approving / merging via the API is "
                      "human-only (P3/P5).")
            # Attached spellings (-XPATCH, --method=PATCH) and body-from-file
            # forms (--input f, -F q=@f) keep the method/label out of argv, so
            # match the method textually and treat any file-fed body on an
            # issue/pull route as mutating (PR 4 backstop major).
            mutating = bool(re.search(r"(^|\s)(-X\s*|--method[= ])(PATCH|POST|PUT|DELETE)\b",
                                      joined))
            body_from_file = bool(re.search(r"(--input|[-][fF]\s*\S*@)", joined))
            if (mutating or body_from_file) and (
                    re.search(r"/(issues|pulls)/\d+", joined) or "graphql" in gargs):
                block("mutating a PR/issue (or a GraphQL mutation) via the API is "
                      "human-only (P3/P5) — labels and approvals come from the user.")

    # 5. git: hook-skipping flags, in any position/spelling.
    sub, sub_args = git_subcommand(seg)
    if sub in ("commit", "push"):
        skip = False
        for t in sub_args:
            if skip:
                skip = False
                continue
            if t in VALUE_FLAGS:
                skip = True
                continue
            if not t.startswith("-") or t == "--":
                continue
            if t == "--no-verify":
                block(f"git {sub} --no-verify skips this repo's verification "
                      "hooks (P6: gates have no agent override).")
            # Attached-value forms carry DATA after the value flag: -m'no
            # changes' tokenizes as -mno changes, whose 'n' is message text,
            # not a flag (round-16 minor). Cut at the value flag.
            letters = t[1:]
            for vf in ("m", "F", "c", "C", "t"):
                if vf in letters:
                    letters = letters[:letters.index(vf)]
            # short -n is --no-verify for commit, --dry-run for push
            if sub == "commit" and not t.startswith("--") and "n" in letters:
                block("git commit -n (any position, clustered, or after "
                      "-m/-F values) is --no-verify; the hooks must run.")

    # 6. Pushes that rewrite or delete main, in every git-native spelling.
    # Only a real `git push` — `git stash push -f` is a different command.
    if sub == "push":
        args = sub_args
        flags = [t for t in args if t.startswith("-")]
        nonflags = [t for t in args if not t.startswith("-")]

        def dst(refspec):
            r = refspec.lstrip("+")
            d = r.split(":")[-1] if ":" in r else r
            return d[len("refs/heads/"):] if d.startswith("refs/heads/") else d

        force = any(
            f in ("--force", "--force-if-includes") or f.startswith("--force-with-lease")
            or (not f.startswith("--") and "f" in f[1:])
            for f in flags
        )
        deleting = "--delete" in flags or any(
            not f.startswith("--") and "d" in f[1:] for f in flags)
        if "--mirror" in flags:
            block("git push --mirror force-updates and prunes every remote ref, "
                  "including main.")
        if force and ("--all" in flags or "--branches" in flags):
            block("force-push with --all/--branches rewrites remote main without "
                  "naming it.")
        refspecs = nonflags[1:] if len(nonflags) > 1 else [
            t for t in nonflags if t.startswith("+")]
        dsts = [dst(r) for r in refspecs]
        if any(d in ("main", "master") for d in dsts):
            if force or any(r.startswith("+") for r in refspecs):
                block("force-push to main is blocked (flag, +refspec, refs/heads "
                      "and reordered forms included).")
            if deleting or any(r.startswith(":") for r in refspecs):
                block("deleting remote main is blocked.")
        if (force or any(r.startswith("+") for r in refspecs)) and (
                not refspecs or any(d in ("HEAD", "@", "") for d in dsts)):
            try:
                import subprocess
                branch = subprocess.run(
                    ["git", "-C", ROOT, "symbolic-ref", "--short", "-q", "HEAD"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
            except Exception:
                branch = ""
            if branch in ("main", "master"):
                block("force-push of the checked-out main rewrites remote main.")

sys.exit(0)

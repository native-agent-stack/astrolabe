"""Print oh-my-codex's keyword matches and every dollar mention in dispatch text.

Ported from meridian@014f706d:.agents/skills/lead/scripts/trigger_words.py; changed: the
temporary folder's prefix names astrolabe.

Usage: trigger_words.py DISPATCH.txt [--package /prefix/node_modules/oh-my-codex]
Use '-' for stdin. Starts no workflow. The installed detector, not a copied keyword list, is
authoritative for keyword matches; it skips text in backticks, Codex does not.
The text reaches node by path, never on standard input: the detector's import leaves fd 0 in
O_NONBLOCK mode (`/proc/self/fdinfo/0` reads 0200000 before it and 02004000 after, node
v24.22.3), where `readFileSync(0, 'utf8')` races the caller's write and throws
`EAGAIN: resource temporarily unavailable, read`.
Dollar mentions: Codex 0.169.0 loads a skill for `$name`, and for a link `[$name](path)` by its
path, where name is one or more of A-Z, a-z, 0-9, `_`, `-`, `:` (white space allowed before
the `(`) and not a common environment variable such as HOME (`codex-rs/skills/src/mentions.rs`,
`extract_tool_mentions_with_sigil` and `parse_linked_tool_mention`). The rule here is broader
than Codex on purpose: text sent to a worker holds no `$` directly followed by a name
character, and no Markdown link whose label begins with `$` at all. It refuses a superset of
Codex's mentions, so it never has to track Codex's grammar, and it has no exceptions.
`${...}`, `$(...)` and `$` before other punctuation stay allowed outside a link label.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DETECT = """
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';
const { detectKeywords } = await import(pathToFileURL(process.argv[1]).href);
const matches = detectKeywords(readFileSync(process.argv[2], 'utf8'));
console.log(JSON.stringify({ selected: matches[0] ?? null, matches }));
"""
MENTION = re.compile(r"\$[A-Za-z0-9_:-]+")
# Broader than Codex's link grammar on purpose: any label that begins with `$`, then `]`,
# white space (newlines too) and `(`.
LINK = re.compile(r"\[\$[^\]]*\]\s*\(")
INSTEAD = (
    "write a shell variable or positional parameter in the brace form (${NAME}, ${1}) "
    "and a skill by its plain name"
)


def mentions(text: str) -> list[dict]:
    """Each bare token and each `[$...](` link, in text order, with its 1-based line and text."""
    lines = text.split("\n")  # The same lines as the count below.
    found = [*MENTION.finditer(text), *LINK.finditer(text)]
    found.sort(key=lambda m: m.start())
    result = []
    for match in found:
        number = text.count("\n", 0, match.start()) + 1
        token = " ".join(match.group().split())
        line = lines[number - 1].removesuffix("\r")
        result.append({"token": token, "line": number, "text": line})
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dispatch")
    parser.add_argument("--package", type=Path)
    args = parser.parse_args(argv)
    try:
        package = args.package
        if package is None:
            root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, check=True)
            package = Path(root.stdout.strip()) / "oh-my-codex"
        detector = package / "dist/hooks/keyword-detector.js"
        if not detector.is_file():
            raise FileNotFoundError(f"oh-my-codex is absent at {package}; cannot check triggers")
        dispatch = sys.stdin.read() if args.dispatch == "-" else Path(args.dispatch).read_text()
        temporary = tempfile.TemporaryDirectory(prefix="astrolabe-trigger-")
        try:
            text = Path(temporary.name) / "dispatch.txt"
            text.write_text(dispatch, encoding="utf-8")
            run = subprocess.run(
                ["node", "--input-type=module", "-e", DETECT, str(detector.resolve()), str(text)],
                capture_output=True,
                text=True,
                check=True,
                stdin=subprocess.DEVNULL,
            )
        finally:
            temporary.cleanup()
        reply = json.loads(run.stdout)
        reply["mentions"] = mentions(dispatch)
        reply["instead"] = INSTEAD
        print(json.dumps(reply, indent=2))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

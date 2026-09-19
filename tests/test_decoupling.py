"""The decoupling proof (issue #186): the product is the attavo/ package, and
nothing under it runs `gh` or names github.com. The forge-specific text lives
outside the package, in templates/ and action.yml, which are the forge's own
files and are not read; a grep over the package is the proof.

`gh` is matched as a word, not as the two characters "gh " -- "through " and
"though " both end that way.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "attavo"

# The modules issue #186 names, one per section heading of the old single
# script; the package is not the package until every one exists.
MODULES = ("records", "stops", "doctor", "spec", "approval", "build", "events", "verify",
           "counsel", "drive", "filing", "record_only", "attest", "ledger", "cli")

GH_COMMAND = re.compile(r"\bgh\b")
GITHUB_HOST = re.compile(r"github\.com", re.IGNORECASE)


def modules() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def test_the_package_has_every_module_the_issue_names():
    missing = [name for name in MODULES if not (PACKAGE / f"{name}.py").is_file()]
    assert not missing, f"attavo/ lacks: {', '.join(missing)}"


def test_no_module_runs_gh_or_names_github():
    files = modules()
    assert files, "attavo/ holds no modules; there is nothing for the grep to read"
    offences = []
    for path in files:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if GH_COMMAND.search(line) or GITHUB_HOST.search(line):
                offences.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not offences, "forge-specific text under attavo/:\n" + "\n".join(offences)


def test_the_entry_is_thin_and_imports_the_package():
    text = (ROOT / "bin" / "attavo").read_text()
    assert len(text.splitlines()) < 60, "bin/attavo grew past a thin entry"
    assert re.search(r"^from attavo\.cli import main\b", text, re.MULTILINE), (
        "bin/attavo does not import main from attavo.cli")


def test_the_grep_finds_what_it_is_meant_to_find():
    """The pattern rejects a gh command and a github.com URL, and passes the
    ordinary words that merely end in the same letters."""
    for offending in ('subprocess.run(["gh", "pr", "view"])', "gh api repos/x/y",
                      "https://github.com/attavo/attavo", "see GitHub.com for it"):
        assert GH_COMMAND.search(offending) or GITHUB_HOST.search(offending), offending
    for innocent in ("read through the record", "though the forge", "a high bound", "right now"):
        assert not (GH_COMMAND.search(innocent) or GITHUB_HOST.search(innocent)), innocent

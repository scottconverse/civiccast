"""Assemble docs/USER-MANUAL.md from docs/manual/src/*.md (documentation tool, not product code).

  python ops/docs-sprint/tools/build_manual.py [--no-diagrams]

Steps: concatenate chapters in order under four Part headings, demote chapter headings one level, paste the generated
inventory tables at <!-- INCLUDE: path --> markers, swap in Appendix H, render every ```mermaid block to a PNG with
mermaid-cli (cached by content hash in docs/manual/diagrams/), and write the single-file manual that
scripts/render_user_manual.py turns into PDF and DOCX.
"""

import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "docs" / "manual" / "src"
DIAG = ROOT / "docs" / "manual" / "diagrams"
OUT = ROOT / "docs" / "USER-MANUAL.md"
MMDC_DIR = Path(r"C:\Users\scott\Documents\Codex\2026-09-16\re\mmd-tool")


def read_version() -> str:
    """Read the canonical product version without importing application code."""
    version_file = ROOT / "civiccast" / "_version.py"
    tree = ast.parse(version_file.read_text(encoding="utf-8"), filename=str(version_file))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return f"v{node.value.value}"
            break
    raise RuntimeError(f"{version_file} must assign a string literal to __version__")


VERSION = read_version()

PARTS = [
    ("Part I. Using CivicCast (for station staff and volunteers)", "1"),
    ("Part II. Installing and running CivicCast (for IT staff)", "2"),
    ("Part III. How CivicCast is built", "3"),
    ("Part IV. Reference", "4"),
]
PART_INTRO = {
    "1": "These chapters are for people who run meetings, operate cameras, edit video, keep records and "
    "answer residents. You do not need to be technical. Each chapter lists what you need before you "
    "start, then walks through each task step by step.",
    "2": "These chapters are for the one or two people who install and look after CivicCast at a small "
    "city or station. They cover planning, installation, configuration, daily operation, security, "
    "troubleshooting and connecting to cable and streaming.",
    "3": "This part explains how the system is put together, with diagrams, for IT staff, integrators and "
    "reviewers.",
    "4": "Lookup material: commands, web interface, settings, files and ports, status words, roles, "
    "checklists, measured evidence, a glossary, licenses and release history.",
}
HEADING = re.compile(r"^(#{1,6})(\s+)(.*)$")
FENCE = re.compile(r"^\s*(```|~~~)")


def demote(text: str, by: int = 1) -> str:
    out, in_code = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            in_code = not in_code
        m = None if in_code else HEADING.match(line)
        if m:
            level = min(len(m.group(1)) + by, 6)
            out.append("#" * level + m.group(2) + m.group(3))
        else:
            out.append(line)
    return "\n".join(out)


def read(name: str) -> str:
    return wrap_paths((SRC / name).read_text(encoding="utf-8").strip()) + "\n"


WIN_PATH = re.compile(
    r"((?:[A-Za-z]:|%[A-Za-z]+%|HKLM|HKCU|HKEY_LOCAL_MACHINE|HKEY_CURRENT_USER)"
    r"(?:\\[\w.\-]+)+)"
)


def wrap_paths(body: str) -> str:
    """Generated tables hold bare Windows paths; LaTeX reads a backslash as a command, so put them in code spans."""
    out = []
    in_code = False
    for line in body.splitlines():
        if FENCE.match(line):
            in_code = not in_code
            out.append(line)
            continue
        if in_code:
            out.append(line)
            continue
        parts = re.split(r"(`[^`]*`)", line)
        out.append("".join(p if p.startswith("`") else WIN_PATH.sub(r"`\1`", p) for p in parts))
    return "\n".join(out)


def includes(text: str) -> str:
    def repl(m):
        body = wrap_paths((ROOT / m.group(1).strip()).read_text(encoding="utf-8"))
        lines = body.splitlines()
        if lines and lines[0].startswith("# "):
            lines = lines[1:]
        return demote("\n".join(lines), 3)  # appendix is level 2; its subsections start at level 3

    return re.sub(r"<!--\s*INCLUDE:\s*(.*?)\s*-->", repl, text)


def swap_appendix_h(appendices: str) -> str:
    """Insert the coordinator's evidence appendix at the APPENDIX-H marker."""
    h = read("40h-evidence.md")
    marker = "<!-- APPENDIX-H: build_manual.py replaces this marker with 40h-evidence.md -->"
    if marker not in appendices:
        raise SystemExit("40-appendices.md is missing the APPENDIX-H marker")
    return appendices.replace(marker, h, 1)


def render_mermaid(text: str, diagrams: bool) -> str:
    DIAG.mkdir(parents=True, exist_ok=True)
    (DIAG / "src").mkdir(exist_ok=True)
    count = {"n": 0}

    def repl(m):
        code = m.group(1).strip() + "\n"
        h = hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]
        count["n"] += 1
        src = DIAG / "src" / f"{h}.mmd"
        png = DIAG / f"{h}.png"
        src.write_text(code, encoding="utf-8", newline="\n")
        if diagrams and not png.exists():
            cfg = MMDC_DIR / "mermaid-config.json"
            cmd = [
                "node",
                str(MMDC_DIR / "node_modules" / "@mermaid-js" / "mermaid-cli" / "src" / "cli.js"),
                "-i",
                str(src),
                "-o",
                str(png),
                "-p",
                str(MMDC_DIR / "pp.json"),
                "-c",
                str(cfg),
                "-s",
                "3",
                "-b",
                "white",
            ]
            r = subprocess.run(cmd, cwd=MMDC_DIR, capture_output=True, text=True)  # noqa: S603 - fixed argv, no shell
            if r.returncode != 0 or not png.exists():
                print(f"MERMAID FAILED for {h}:\n{r.stderr[-600:]}\n{code[:300]}", file=sys.stderr)
                return f"```\n{code}```\n"
        cap = (m.group(2) or "").strip().replace("]", "\\]")
        alt = cap or f"Diagram {count['n']}"
        width = 100
        if png.exists():
            from PIL import Image

            w_px, h_px = Image.open(png).size
            ratio = h_px / w_px
            if ratio > 1.0:  # tall diagrams must leave room on the page for the caption
                width = max(40, int(100 * 1.0 / ratio))
        return f"![{alt}](manual/diagrams/{h}.png){{width={width}%}}\n"

    pat = r"```mermaid\s*\n(.*?)```(?:[ \t]*\n+\*(Figure[^\n]*)\*[ \t]*\n)?"
    return re.sub(pat, repl, text, flags=re.S)


def main():
    diagrams = "--no-diagrams" not in sys.argv
    files = sorted(p.name for p in SRC.glob("*.md"))
    parts = {"1": [], "2": [], "3": [], "4": []}
    for name in files:
        if name.startswith("00-") or name == "40h-evidence.md":
            continue
        text = read(name)
        if name.startswith("40-"):
            text = swap_appendix_h(text)
        parts[name[0] if name[0] in parts else "4"].append((name, text))
    # file prefixes 1x -> Part I, 2x -> Part II, 3x -> Part III, 4x -> Part IV
    body = [demote(read("00-front.md"), 0)]
    for title, key in PARTS:
        chunk = parts[key]
        if not chunk:
            continue
        body.append(f"\n\\newpage\n\n# {title} {{#part-{key}}}\n\n{PART_INTRO[key]}\n")
        for _, t in chunk:
            brk = "\\newpage\n\n" if key != "4" else ""
            body.append(brk + demote(t, 1))
    text = "\n\n".join(body)
    text = includes(text)
    text = render_mermaid(text, diagrams)
    # Source chapters live in docs/manual/src/; the assembled manual lives in docs/.
    # Rewrite chapter-relative image links so they resolve from docs/USER-MANUAL.md.
    text = text.replace("](../images/", "](manual/images/")
    # Barlow has no arrow or check-mark glyphs; use plain text for the few places they appear.
    text = text.replace("→", ">").replace("✓", "yes")
    text = re.sub(r"\n{3,}", "\n\n", text)
    front = (
        "---\n"
        "title: CivicCast User Manual\n"
        f"subtitle: For station staff, volunteers and city IT staff - {VERSION} (native Windows line)\n"
        "author: The CivicCast Authors\n"
        "date: 2026-11-15\n"
        "urlcolor: blue\n"
        "toccolor: black\n"
        "---\n\n"
    )
    OUT.write_text(front + text, encoding="utf-8", newline="\n")
    print(
        "wrote",
        OUT,
        len(text.split()),
        "words;",
        "diagrams rendered" if diagrams else "diagrams skipped",
    )


if __name__ == "__main__":
    main()

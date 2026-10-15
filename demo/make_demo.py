"""Render the 60s PatchPath demo video from REAL captured outputs.

Terminal-style frames (PIL) -> ffmpeg concat -> mp4. No stock footage, no
fake output: every terminal line below is copied from an actual run
(data/packets/pack_pypa__packaging__1449.md, generated 2026-10-15).
"""
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
BG = (13, 17, 23)
FG = (201, 209, 217)
DIM = (139, 148, 158)
GREEN = (63, 185, 80)
YELLOW = (210, 153, 34)
BLUE = (88, 166, 255)

MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
F = ImageFont.truetype(MONO, 22)
FB = ImageFont.truetype(MONO_B, 26)
F_BIG = ImageFont.truetype(MONO_B, 44)

OUT = Path("/tmp/demo_frames")
OUT.mkdir(exist_ok=True)

PACK_MD = Path(__file__).resolve().parent.parent / "data/packets/pack_pypa__packaging__1449.md"


def frame(name: str, lines: list[tuple[str, tuple]], title: str | None = None):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    y = 60
    if title:
        d.text((60, y), title, font=F_BIG, fill=FG)
        y += 90
    for text, color in lines:
        d.text((60, y), text, font=F, fill=color)
        y += 34
    img.save(OUT / f"{name}.png")


def terminal(name: str, lines: list[str], header: str = "sanskar@localhost: ~/patchpath"):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, W - 20, 64], fill=(22, 27, 34))
    d.text((36, 30), header, font=F, fill=DIM)
    y = 84
    for ln in lines:
        color = FG
        if ln.startswith("$"):
            color = GREEN
        elif "EXACT" in ln or "READY" in ln or "evidence assembled" in ln:
            color = GREEN
        elif "UNRESOLVED" in ln or "unverified" in ln.lower() or "competing" in ln.lower():
            color = YELLOW
        elif ln.startswith("MATCH") or ln.startswith("RETURN"):
            color = BLUE
        d.text((36, y), ln[:95], font=F, fill=color)
        y += 30
    img.save(OUT / f"{name}.png")


# --- slide 1: title (4s)
frame("01", [
    ("", FG),
    ("before you claim an issue, see the evidence", DIM),
    ("", FG),
    ("Graph Hacks - Track 01: Agents That Act on Connected Data", BLUE),
    ("FalkorDB as the agent's context layer", BLUE),
], title="PatchPath")

# --- slide 2: problem (4s)
frame("02", [
    ("Claiming an open-source issue blind wastes weekends:", FG),
    ("", FG),
    ("  - which code does it actually touch?", DIM),
    ("  - who calls that code? which tests exercise it?", DIM),
    ("  - what do the repo's contribution rules demand?", DIM),
    ("  - is someone else already working on it?", DIM),
    ("", FG),
    ("PatchPath assembles that evidence pack, pinned to a commit.", GREEN),
], title="The problem")

# --- slides 3-5: real pack run (terminal, progressive)
t1 = [
    "$ python -m patchpath.cli pack pypa/packaging 1449",
    "",
    "[pack] using snapshot pp_snap_21d27086d98f9685 (packaging @ ef837aad)",
    "[issue] #1449 state=open comments=0 linked_prs=1",
    "[pack] status: evidence assembled",
]
t2 = t1 + [
    "",
    "Candidate code (lexical match, review before trusting):",
    "  src.packaging.markers.InvalidMarker      <- issue title",
    "  src.packaging.requirements.InvalidRequirement <- issue title",
    "  src.packaging.markers.Marker.__init__    <- dotted name in body",
]
t3 = t2 + [
    "",
    "Open PRs referencing this issue (competing work):",
    "  #1450 fix(markers): raise InvalidMarker for deeply nested markers",
    "Unknowns: 50% of call sites statically unresolved - stated, not hidden.",
    "Citations: 5 hash-validated against pinned source.",
]
terminal("03", t1)
terminal("04", t2)
terminal("05", t3)

# --- slide 6: the graph does the work (8s)
terminal("06", [
    "$ redis-cli GRAPH.QUERY pp_snap_21d27086d98f9685 \\",
    '    "MATCH (t:Symbol {is_test: true})-[c:CALLS]->(s:Symbol)',
    '     WHERE s.qualname = \'src.packaging.markers.Marker.__init__\'',
    '     RETURN t.qualname, c.tag LIMIT 3"',
    "",
    "tests.test_markers.TestOperatorEvaluation.test_prefers_pep440 [EXACT]",
    "tests.test_markers.TestOperatorEvaluation.test_new_string_rules [EXACT]",
    "tests.test_markers.TestOperatorEvaluation.test_allows_prerelease [EXACT]",
    "... 54 test-candidate edges into Marker.__init__ (real query result)",
    "",
    "Take FalkorDB out and these multi-hop paths do not exist.",
    "The graph is the product, not decoration.",
], header="FalkorDB - the agent's context layer")

# --- slide 7: honesty (6s)
frame("07", [
    ("Every call edge is tagged:", FG),
    ("  EXACT       resolved to a definition in this snapshot", GREEN),
    ("  NAME_ONLY   unique-name suggestion, not proof", YELLOW),
    ("  UNRESOLVED  dynamic dispatch etc. - a visible gap", YELLOW),
    ("", FG),
    ("No auto-claims. No comments. No PRs. No generated patches.", FG),
    ("Issue state unverifiable? The pack says so, and claims nothing.", FG),
    ("Eval: 5/6 curated, 7/11 fresh-sample files found (misses shown).", DIM),
], title="Honesty model")

# --- slide 8: close (5s)
frame("08", [
    ("local-first - read-only - zero spend", DIM),
    ("", FG),
    ("github.com/MaybeSomeone-arc18/patchpath", BLUE),
    ("", FG),
    ("Built by Sanskar Kharya with AI assistance (disclosed).", DIM),
    ("FalkorDB 6.0.2 - Python AST - FastAPI - SQLite", DIM),
], title="PatchPath")

# --- assemble
frames = [("01", 4), ("02", 4), ("03", 6), ("04", 8), ("05", 8),
          ("06", 9), ("07", 8), ("08", 5)]
concat = OUT / "concat.txt"
concat.write_text("".join(
    f"file '{n}.png'\nduration {d}\n" for n, d in frames) + f"file '{frames[-1][0]}.png'\n")
subprocess.run([
    "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
    "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
    "-r", "24", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
    "/tmp/patchpath_demo.mp4"], check=True, capture_output=True)
total = sum(d for _, d in frames)
print(f"rendered /tmp/patchpath_demo.mp4 ({total}s)")

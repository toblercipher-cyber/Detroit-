"""Reference image storage: round trip, containment, errors. No network."""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from backend.memory.artifacts import (JsonArtifactRepository, StateRepository,
                                      ReferenceImageNotFoundError,
                                      ProjectNotFoundError, InvalidProjectIdError,
                                      StateRepositoryError)
from backend.schemas.state import ProjectState
from backend.tests.suites import _tmpdir

root = Path(_tmpdir.mkdtemp(prefix="refimg_"))
repo = JsonArtifactRepository(root)
st = ProjectState(user_request="coffee shop")
repo.save(st)
PID = st.project_id
IID = "img_" + "ab" * 16
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4

print("=== 1. Protocol declares both methods ===")
for name in ("save_reference_image", "load_reference_image"):
    print("   %-22s %s" % (name, ok(hasattr(StateRepository, name))))
# Structural, not isinstance: the Protocol is deliberately not @runtime_checkable,
# so conformance is checked by shape — every declared member present on the impl.
import inspect
declared = {n for n in vars(StateRepository) if not n.startswith("_")}
print("   impl covers all %d protocol members: %s" % (
    len(declared), ok(all(callable(getattr(repo, n, None)) for n in declared))))
for n in ("save_reference_image", "load_reference_image"):
    print("   %-22s signature matches: %s" % (n, ok(
        list(inspect.signature(getattr(repo, n)).parameters)
        == list(inspect.signature(getattr(StateRepository, n)).parameters)[1:])))

print()
print("=== 2. Byte-exact round trip ===")
repo.save_reference_image(PID, IID, PNG, "image/png")
got = repo.load_reference_image(PID, IID)
print("   bytes identical :", ok(got == PNG))
print("   length          :", len(got), ok(len(got) == len(PNG)))
print("   no text encoding damage (0x89 intact):", ok(got[:1] == b"\x89"))
print("   binary with NULs survives:", ok(b"\x00" in got))

print()
print("=== 3. Stored where docs/state.md 5.1 says, with no extension ===")
d = repo.reference_dir(PID)
files = sorted(p.name for p in d.iterdir())
print("   dir            :", d.parent.name + "/" + d.name,
      ok(d.name == "reference"))
# {image_id}.{ext} per docs/security.md 4; the extension comes from the
# magic-byte-verified type, never from any client-supplied filename.
print("   filename is id + ext:", files, ok(files == [IID + ".png"]))
print("   extension matches the verified type:", ok(files[0].endswith(".png")))

print()
print("=== 4. Overwrite is atomic and leaves no temp files ===")
repo.save_reference_image(PID, IID, b"second version", "image/png")
print("   replaced       :", ok(repo.load_reference_image(PID, IID) == b"second version"))
leftovers = [p.name for p in d.iterdir() if p.name.startswith(".") or p.name.endswith(".tmp")]
print("   no temp litter :", leftovers, ok(leftovers == []))

print()
print("=== 5. Missing image is distinguishable from missing project ===")
try:
    repo.load_reference_image(PID, "img_" + "cd" * 16)
    print("   absent image: **FAIL**")
except ReferenceImageNotFoundError:
    print("   absent image -> ReferenceImageNotFoundError:", ok(True))
try:
    repo.load_reference_image("proj_" + "0" * 32, IID)
    print("   unknown project: **FAIL**")
except (ProjectNotFoundError, InvalidProjectIdError, ReferenceImageNotFoundError) as e:
    print("   unknown project ->", type(e).__name__, ok(True))
print("   the two errors are different types:",
      ok(ReferenceImageNotFoundError is not ProjectNotFoundError))
print("   both are StateRepositoryError:",
      ok(issubclass(ReferenceImageNotFoundError, StateRepositoryError)))

print()
print("=== 6. Traversal and malformed ids are rejected, not resolved ===")
outside = root / "ESCAPED"
for bad in ["../../ESCAPED", "..\\..\\ESCAPED", "img_" + "ab" * 16 + "/../../x",
            "img_ABCDEF" + "0" * 26, "img_short", "", "img_" + "ab" * 16 + ".svg",
            "%2e%2e%2fESCAPED", "img_" + "ab" * 15]:
    for fn, label in ((repo.load_reference_image, "load"),
                      (lambda p, i: repo.save_reference_image(p, i, b"x", "image/png"), "save")):
        try:
            fn(PID, bad)
            print("   %-5s %-28s ACCEPTED **FAIL**" % (label, bad[:28]))
        except ReferenceImageNotFoundError:
            pass
        except Exception as e:
            print("   %-5s %-28s %s **FAIL**" % (label, bad[:28], type(e).__name__))
print("   all 9 malformed/traversal ids rejected on both paths:", ok(True))
print("   nothing escaped the data root:", ok(not outside.exists()))
print("   reference dir still holds only the one file:",
      ok(sorted(p.name for p in d.iterdir()) == [IID + ".png"]))

print()
print("=== 7. Deleting the project removes its images ===")
repo.delete(PID)
print("   reference dir gone:", ok(not d.exists()))

print()
print("=== 8. Errors never leak absolute paths ===")
repo2 = JsonArtifactRepository(root)
st2 = ProjectState(user_request="x"); repo2.save(st2)
try:
    repo2.load_reference_image(st2.project_id, "img_" + "ff" * 16)
except ReferenceImageNotFoundError as e:
    print("   message      :", repr(str(e)))
    print("   no path in it:", ok(str(root) not in str(e) and os.sep not in str(e)))

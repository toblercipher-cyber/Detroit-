"""attach_reference_image: validation order, spoofing, state+storage. No network."""
import os, sys, hashlib, tempfile
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from backend.services.projects import (ProjectService, UploadTooLargeError,
                                       UnsupportedMediaTypeError, _sniff_image_type)
from backend.memory.artifacts import (JsonArtifactRepository,
                                      ReferenceImageNotFoundError,
                                      InvalidProjectIdError, ProjectNotFoundError)
from backend.config.settings import get_settings
from backend.schemas.api import ErrorCode

PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 20
JPEG = b"\xff\xd8\xff\xe0" + b"jpegdata" * 20
WEBP = b"RIFF" + (100).to_bytes(4, "little") + b"WEBP" + b"vp8data" * 10
GIF = b"GIF89a" + b"gifdata" * 20
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
HTML = b"<!doctype html><script>fetch('/steal')</script>"


def fresh():
    repo = JsonArtifactRepository(Path(tempfile.mkdtemp(prefix="upl_")))
    svc = ProjectService(repository=repo, workflow=object(), settings=get_settings())
    return svc, svc.create(prompt="coffee shop").project_id


print("=== 1. Sniffer recognises exactly the three allowlisted formats ===")
for data, want in [(PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp"),
                   (GIF, None), (SVG, None), (HTML, None), (b"", None), (b"RIFF", None)]:
    got = _sniff_image_type(data)
    print("   %-28s -> %-12s %s" % (repr(data[:14]), got, ok(got == want)))

print()
print("=== 2. Happy path: stored, hashed, recorded ===")
svc, pid = fresh()
ref = svc.attach_reference_image(pid, PNG, "image/png")
print("   image_id shape :", ref.image_id[:4], ok(ref.image_id.startswith("img_")))
print("   content_type   :", ref.content_type, ok(ref.content_type == "image/png"))
print("   size_bytes     :", ref.size_bytes, ok(ref.size_bytes == len(PNG)))
print("   sha256 correct :", ok(ref.sha256 == hashlib.sha256(PNG).hexdigest()))
print("   bytes on disk  :", ok(svc._repository.load_reference_image(pid, ref.image_id) == PNG))
st = svc.get(pid)
print("   recorded in state:", len(st.reference_images), ok(len(st.reference_images) == 1))
print("   survives reload  :", ok(st.reference_images[0].image_id == ref.image_id))

print()
print("=== 3. Appends, never replaces ===")
r2 = svc.attach_reference_image(pid, JPEG, "image/jpeg")
st = svc.get(pid)
print("   two images     :", len(st.reference_images), ok(len(st.reference_images) == 2))
print("   distinct ids   :", ok(r2.image_id != ref.image_id))
print("   both readable  :", ok(svc._repository.load_reference_image(pid, r2.image_id) == JPEG))
print("   order preserved:", ok([i.image_id for i in st.reference_images]
                                == [ref.image_id, r2.image_id]))

print()
print("=== 4. Spoofed Content-Type is rejected (content wins) ===")
svc, pid = fresh()
for label, data, declared in [
        ("SVG declared as PNG", SVG, "image/png"),
        ("HTML declared as PNG", HTML, "image/png"),
        ("GIF declared as JPEG", GIF, "image/jpeg"),
        ("real PNG declared JPEG", PNG, "image/jpeg"),
        ("real JPEG declared WebP", JPEG, "image/webp"),
        ("empty declared PNG", b"", "image/png")]:
    try:
        svc.attach_reference_image(pid, data, declared)
        print("   %-24s ACCEPTED **FAIL**" % label)
    except UnsupportedMediaTypeError as e:
        print("   %-24s rejected 415 %s" % (label, ok(e.code is ErrorCode.UNSUPPORTED_MEDIA_TYPE)))

print()
print("=== 5. Non-allowlisted declared type rejected even if content is valid ===")
for declared in ["image/gif", "image/svg+xml", "text/html", "application/octet-stream", ""]:
    try:
        svc.attach_reference_image(pid, PNG, declared)
        print("   %-26s ACCEPTED **FAIL**" % declared)
    except UnsupportedMediaTypeError:
        print("   %-26s rejected %s" % (declared or "(empty)", ok(True)))

print()
print("=== 6. Size limit ===")
limit = get_settings().max_upload_bytes
big = b"\x89PNG\r\n\x1a\n" + b"x" * limit
try:
    svc.attach_reference_image(pid, big, "image/png")
    print("   oversized ACCEPTED **FAIL**")
except UploadTooLargeError as e:
    print("   oversized rejected 413 :", ok(e.code is ErrorCode.UPLOAD_TOO_LARGE))
    print("   message states the limit:", ok(str(limit) in str(e)))
exact = b"\x89PNG\r\n\x1a\n" + b"x" * (limit - 8)
r = svc.attach_reference_image(pid, exact, "image/png")
print("   exactly at the limit accepted:", ok(r.size_bytes == limit))

print()
print("=== 7. Nothing is written when validation fails ===")
svc, pid = fresh()
before = list(svc._repository.reference_dir(pid).iterdir()) if \
    svc._repository.reference_dir(pid).exists() else []
for data, declared in [(SVG, "image/png"), (PNG, "image/gif"), (big, "image/png")]:
    try:
        svc.attach_reference_image(pid, data, declared)
    except Exception:
        pass
d = svc._repository.reference_dir(pid)
after = list(d.iterdir()) if d.exists() else []
print("   no files written:", len(after), ok(len(after) == len(before)))
print("   state untouched :", ok(len(svc.get(pid).reference_images) == 0))

print()
print("=== 8. Unknown project rejected before any work ===")
for bad in ["proj_" + "0" * 32, "../../etc", "nope"]:
    try:
        svc.attach_reference_image(bad, PNG, "image/png")
        print("   %-18s ACCEPTED **FAIL**" % bad[:18])
    except (ProjectNotFoundError, InvalidProjectIdError):
        print("   %-18s rejected %s" % (bad[:18], ok(True)))

print()
print("=== 9. Error messages leak nothing ===")
try:
    svc.attach_reference_image(pid, HTML, "image/png")
except UnsupportedMediaTypeError as e:
    print("   message:", repr(str(e)))
    print("   no content echoed:", ok("script" not in str(e) and "fetch" not in str(e)))
    print("   no path echoed   :", ok(os.sep not in str(e)))

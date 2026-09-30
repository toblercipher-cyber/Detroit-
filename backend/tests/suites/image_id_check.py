"""new_image_id: format, uniqueness, and agreement with the storage guard."""
import os, sys, re
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from backend.schemas.state import (new_image_id, new_project_id,
                                   new_session_id, new_checkpoint_id)
from backend.memory.artifacts import _IMAGE_ID_RE, JsonArtifactRepository

print("=== 1. Format matches the house style ===")
i = new_image_id()
print("   sample        :", i)
print("   img_ prefix   :", ok(i.startswith("img_")))
print("   32 hex chars  :", ok(re.fullmatch(r"img_[0-9a-f]{32}", i) is not None))
print("   length 36     :", len(i), ok(len(i) == 36))

print()
print("=== 2. Accepted by the repository guard written last step ===")
print("   1000 generated ids all match _IMAGE_ID_RE:",
      ok(all(_IMAGE_ID_RE.match(new_image_id()) for _ in range(1000))))

print()
print("=== 3. Unique ===")
ids = {new_image_id() for _ in range(10000)}
print("   10000 distinct:", len(ids), ok(len(ids) == 10000))

print()
print("=== 4. Cannot be confused with the other id kinds ===")
others = [new_project_id(), new_session_id(), new_checkpoint_id()]
print("   no other id matches _IMAGE_ID_RE:",
      ok(not any(_IMAGE_ID_RE.match(o) for o in others)))
print("   prefixes all distinct:",
      ok(len({x.split("_")[0] for x in others + [new_image_id()]}) == 4))

print()
print("=== 5. Round trip through storage with a generated id ===")
import tempfile
from pathlib import Path
from backend.schemas.state import ProjectState
repo = JsonArtifactRepository(Path(tempfile.mkdtemp(prefix="imgid_")))
st = ProjectState(user_request="x"); repo.save(st)
iid = new_image_id()
repo.save_reference_image(st.project_id, iid, b"\x89PNG\r\n\x1a\nbytes", "image/png")
print("   saved + loaded:", ok(repo.load_reference_image(st.project_id, iid)
                               == b"\x89PNG\r\n\x1a\nbytes"))
# Stored as {image_id}.{ext}, extension from the verified type (security.md 4).
print("   filename is id + verified ext:",
      ok([p.name for p in repo.reference_dir(st.project_id).iterdir()]
         == [iid + ".png"]))

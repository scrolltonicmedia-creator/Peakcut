"""Selecting the best clips and turning them into a long-form library item."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-longform-")
os.environ.update(DATABASE_URL="", PEAKCUT_ACCOUNTS="0")
sys.path.insert(0, ".")

from studio import auth, ctx, db, jobs, render  # noqa: E402

db.init()

fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra and not ok else ""))
    ok or fails.append(name)


with ctx.as_user(auth.local_user().id):
    pairs, videos = [], []
    for group in range(3):
        job = jobs.create({"type": "file", "path": "", "name": f"source {group}.mp4"}, {}, enqueue=False)
        source = Path(job["dir"]) / "source.mp4"
        source.write_bytes(b"original video")
        job["source_path"] = str(source)
        videos.append(job["id"])
        clips = Path(job["dir"]) / "clips"
        clips.mkdir(parents=True, exist_ok=True)
        for n in range(4):
            cid = f"clip{n + 1:02d}"
            score = group * 4 + n + 1
            job["clips"].append({"id": cid, "status": "done", "file": f"{cid}.mp4", "start": n * 60, "end": (n + 1) * 60,
                                 "score": score, "title": f"Moment {score}"})
            pairs.append([job["id"], cid])
        job["status"] = "done"
        jobs.save(job)

    plan = "I will explain the challenge and add my own analysis between every selected moment."
    made = jobs.create_compilation(videos, "The Best Moments", 10, plan, enqueue_now=False)
    check("the ten highest-scored original minutes are selected", len(made["compilation_items"]) == 10 and
          made["compilation_items"] == pairs[2:], str(made["compilation_items"]))
    check("the chosen moments keep their original viewing order", made["compilation_items"] == sorted(
          made["compilation_items"], key=lambda x: pairs.index(x)))
    state = (made["kind"], made["duration"], made["clips"][0]["status"])
    check("the long video is a normal queued library job", state == ("compilation", 600.0, "queued"), str(state))
    check("internal source moment ids are not sent to the browser", "compilation_items" not in jobs.public(made))

    old_render = render.render_compilation

    def fake_render(items, out_dir, name, target, progress):
        check("original source sections reach the renderer", len(items) == 10 and target == 600.0 and
              all(Path(x["path"]).name == "source.mp4" and "start" in x for x in items))
        progress(10, 10, 600)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{name}.mp4").write_bytes(b"long video")
        (out_dir / f"{name}.jpg").write_bytes(b"cover")
        return {"file": f"{name}.mp4", "cover": f"{name}.jpg", "duration": 600.0, "layout": "longform"}

    render.render_compilation = fake_render
    try:
        jobs._run_compilation(made)
    finally:
        render.render_compilation = old_render
    check("the finished compilation is downloadable", made["status"] == "done" and
          made["clips"][0]["status"] == "done" and (Path(made["dir"]) / "clips" / "longform.mp4").is_file())
    check("the narration plan stays with the project", made["narration_plan"] == plan)

    try:
        jobs.create_compilation(videos, "No commentary", 10, "too short", enqueue_now=False)
        refused = False
    except ValueError:
        refused = True
    check("a long-form build requires an original commentary plan", refused)

print("ALL PASSED" if not fails else f"{len(fails)} failed")
shutil.rmtree(os.environ["PEAKCUT_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)

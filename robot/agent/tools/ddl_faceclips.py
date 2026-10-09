#!/usr/bin/env python3
"""Pack DDL procedural-face keyframes (eye shapes, lids, face position) from
selected stock animations for the agent's face clip player.

Source: github.com/digital-dream-labs/vector-animations-build assets/animations/
(ProceduralFaceKeyFrame tracks only; sound, head, lift and body tracks are not
used). Used under the Digital Dream Labs Software Asset License 1.0 (personal
use with a Vector robot).

Output: gzip JSON {clip: {"src": anim, "len": ms, "kf": [[t_ms, faceCx, faceCy,
faceSx, faceSy, L0..L18, R0..R18], ...]}}. Eye params 0..18 follow
ProceduralFaceParams (scripts/exampleScripts/modifyEyes.py): centre, scale,
angle, 8 corner radii, upper lid Y/angle/bend, lower lid Y/angle/bend.

usage: ddl_faceclips.py <vector-animations-build checkout> <out.json.gz>
"""
import glob
import gzip
import json
import os
import sys

# clip name -> (animation directory, clip key inside the JSON)
CLIPS = {
    "petting_lvl1": ("anim_petting_01", "anim_petting_lvl1_01"),
    "petting_lvl2": ("anim_petting_01", "anim_petting_lvl2_01"),
    "petting_lvl3": ("anim_petting_01", "anim_petting_lvl3_01"),
    "petting_lvl4": ("anim_petting_01", "anim_petting_lvl4_01"),
    "petting_bliss": ("anim_petting_01", "anim_petting_blissloop_02"),
    "petting_getout": ("anim_petting_01", "anim_petting_bliss_getout_01"),
    "fistbump_request": ("anim_fistbump_requests_01", "anim_fistbump_requestoncelong_01"),
    "fistbump_success": ("anim_fistbump_success_01", "anim_fistbump_success_01"),
    "fistbump_fail": ("anim_fistbump_fail_01", "anim_fistbump_fail_01"),
    "iloveyou": ("anim_feedback_iloveyou_01", "anim_feedback_iloveyou_01"),
    "goodrobot": ("anim_feedback_goodrobot_01", "anim_feedback_goodrobot_01"),
    "badrobot": ("anim_feedback_badrobot_01", "anim_feedback_badrobot_01"),
    "apology": ("anim_feedback_apology_01", "anim_feedback_apology_01"),
    "shutup": ("anim_feedback_shutup_01", "anim_feedback_shutup_01"),
    "bequiet": ("anim_feedback_bequiet_01", "anim_feedback_bequiet_01"),
    "hello": ("anim_greeting_hello", "anim_greeting_hello_01"),
    "goodmorning": ("anim_greeting_goodmorning", "anim_greeting_goodmorning_01"),
    "goodnight": ("anim_greeting_goodnight", "anim_greeting_goodnight_01"),
    "goodbye": ("anim_greeting_goodbye", "anim_greeting_goodbye_01"),
    "sleep_getin": ("anim_gotosleep_getin_01", "anim_gotosleep_getin_01"),
    "sleeping": ("anim_gotoSleep", "anim_gotosleep_sleeploop_01"),
    "wakeup": ("anim_gotosleep_getout_01", "anim_gotosleep_getout_01"),
    "dance": ("anim_dancebeat_01", "anim_dancebeat_idle_01"),
    "comehere": ("anim_movement_comehere_01", "anim_movement_comehere_01"),
    "lookatme": ("anim_attention_lookatdevice", "anim_attention_lookatdevice_01"),
    "photo": ("anim_photo_shutter_01", "anim_photo_shutter_01"),
    "volume": ("anim_volume_stage", "anim_volume_stage_03"),
    "volume_max": ("anim_volume_stage", "anim_volume_stage_max"),
    "volume_min": ("anim_volume_stage", "anim_volume_stage_min"),
    "cant_help": ("anim_chargerdocking_searchforcharger_canthelp", "anim_chargerdocking_searchforcharger_canthelp"),
    "comeoff": ("anim_chargerdocking_comeoff_01", "anim_chargerdocking_comeoff_straight_01"),
    "howold": ("anim_howold_01", "anim_howold_getout_01"),
}


def find(root, folder, key):
    for f in sorted(glob.glob(os.path.join(root, "assets/animations", folder, "*.json"))):
        d = json.load(open(f))
        if key in d:
            return d[key]
    raise SystemExit(f"missing {folder}/{key}")


def r(x):
    return round(float(x), 3)


def main():
    root, out = sys.argv[1], sys.argv[2]
    pack = {}
    for name, (folder, key) in CLIPS.items():
        track = find(root, folder, key)
        end = max(k["triggerTime_ms"] + k.get("durationTime_ms", 0) for k in track)
        kfs = []
        for k in sorted((k for k in track if k["Name"] == "ProceduralFaceKeyFrame"), key=lambda k: k["triggerTime_ms"]):
            row = [int(k["triggerTime_ms"]), r(k.get("faceCenterX", 0)), r(k.get("faceCenterY", 0)),
                   r(k.get("faceScaleX", 1)), r(k.get("faceScaleY", 1))]
            row += [r(v) for v in k["leftEye"][:19]] + [r(v) for v in k["rightEye"][:19]]
            kfs.append(row)
        if not kfs:
            raise SystemExit(f"{key}: no face keyframes")
        pack[name] = {"src": key, "len": int(max(end, kfs[-1][0])), "kf": kfs}
    raw = json.dumps(pack, separators=(",", ":")).encode()
    with gzip.open(out, "wb", 9) as f:
        f.write(raw)
    print(f"{len(pack)} clips, {len(raw)} bytes json -> {os.path.getsize(out)} bytes gz")


if __name__ == "__main__":
    main()

#!/usr/bin/env bash
# restart_and_verify.sh — port test2 prod-zone edits onto the pulled
# camera_mapping.json, restart the stack, and capture state for review.
#
# Run from the repo root:
#     bash poc/scripts/restart_and_verify.sh > /tmp/post_pull_verify.txt 2>&1
#     cat /tmp/post_pull_verify.txt
#
# Then paste the output back. Safe to re-run.

set -u

# Always run from ~/rlcc (the repo root) regardless of where invoked.
cd ~/rlcc || { echo "ERROR: ~/rlcc not found"; exit 1; }

# Pick the most recent backup dir (or honor an env-provided $BACKUP).
if [[ -z "${BACKUP:-}" || ! -d "${BACKUP}" ]]; then
    BACKUP="$(ls -td ~/rlcc_backup_* 2>/dev/null | head -1)"
fi

hr() { printf '\n========== %s ==========\n' "$*"; }

hr "ENVIRONMENT"
echo "host:    $(hostname)"
echo "date:    $(date -u +'%F %T %Z') / $(TZ=Asia/Kolkata date +'%F %T %Z')"
echo "pwd:     $(pwd)"
echo "BACKUP:  ${BACKUP:-<NONE FOUND>}"
echo "branch:  $(git rev-parse --abbrev-ref HEAD)"
echo "head:    $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s)"

if [[ -z "${BACKUP:-}" || ! -f "${BACKUP}/camera_mapping.json" ]]; then
    echo
    echo "ERROR: no usable backup at ${BACKUP}/camera_mapping.json — refusing to continue."
    echo "       (re-run step 1 backup, or export BACKUP=/path/to/dir before invoking)"
    exit 2
fi

hr "STEP 7 — port prod zones onto pulled camera_mapping.json"
python3 - <<PYEOF
import json, pathlib
backup = json.loads(pathlib.Path("${BACKUP}/camera_mapping.json").read_text())
pulled_path = pathlib.Path("poc/config/camera_mapping.json")
pulled = json.loads(pulled_path.read_text())

backup_by_cam = {c.get("camera_id"): c for c in backup}
merged_count = 0
for cam in pulled:
    backup_cam = backup_by_cam.get(cam.get("camera_id"))
    if not backup_cam:
        continue
    pz = (backup_cam.get("zones") or {}).get("pos_zones") or []
    if any(any(zone.get(k) for k in ("seller_zone","customer_zone","pos_zone","pos_screen_zone","bill_zone")) for zone in pz):
        cam["zones"] = backup_cam["zones"]
        merged_count += 1
    if "enabled" in backup_cam:
        cam["enabled"] = backup_cam["enabled"]
    if backup_cam.get("rtsp_url") and backup_cam["rtsp_url"] != cam.get("rtsp_url"):
        cam["rtsp_url"] = backup_cam["rtsp_url"]

pulled_path.write_text(json.dumps(pulled, indent=2) + "\n")
print(f"merged prod zones into {merged_count} of {len(pulled)} cameras")
print(f"all carry nukkad_pos_aliases: {all('nukkad_pos_aliases' in c for c in pulled)}")
PYEOF

echo
echo "camera_id + alias check:"
python3 -c "
import json
data = json.loads(open('poc/config/camera_mapping.json').read())
for c in data:
    aliases = c.get('nukkad_pos_aliases', [])
    has_zones = any(z.get('seller_zone') for z in (c.get('zones') or {}).get('pos_zones') or [])
    print(f\"  {c['camera_id']:<25}  pos={c['pos_terminal_no']:<6}  aliases={aliases}  zones={'YES' if has_zones else 'no '}\")
"

hr "STEP 8 — restart backend stack"
./start.sh stop
sleep 2
./start.sh
echo "(start.sh launched — waiting 8s for backend to bind)"
sleep 8

hr "STEP 9 — /health"
curl -sS --max-time 5 http://localhost:8001/health | python3 -m json.tool 2>/dev/null \
    || echo "(health endpoint unreachable — check logs/backend.log)"

hr "STEP 9b — backend log tail (startup messages, any errors)"
tail -60 ~/rlcc/poc/logs/backend.log 2>/dev/null || echo "(no backend.log yet)"

hr "STEP 10 — run snapshot_pos_data.sh"
cd ~/rlcc/poc
bash scripts/snapshot_pos_data.sh

hr "DONE — paste everything above this line back to chat"

#!/bin/bash
set -e
TS=$(date +%Y%m%d_%H%M%S)
# NAS 上的实际项目目录。改名后可用 ./_nas_backup.sh /vol1/docker/https_ssl 覆盖。
SRC=${1:-/vol1/docker/qilin_SSL}
DST=/vol1/docker/$(basename "$SRC")_backup_${TS}
echo "SRC=$SRC"
echo "DST=$DST"
if [ ! -d "$SRC" ]; then echo "SRC_MISSING"; exit 1; fi
mkdir -p "$DST"
cd "$SRC"
for f in docker-compose.yml Dockerfile entrypoint.sh nginx.conf app.py requirements.txt users.json .env; do
  if [ -f "$f" ]; then cp -a "$f" "$DST/"; echo "COPIED $f"; fi
done
if [ -d data ]; then
  tar czf "$DST/data.tar.gz" data
  echo "DATA_TAR_OK"
fi
ls -la > "$DST/root_ls.txt" 2>&1 || true
echo "BACKUP_DONE $DST"
ls -la "$DST"

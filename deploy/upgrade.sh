#!/bin/sh
set -eu
release="$(date -u +%Y%m%dT%H%M%SZ)"
source=/opt/polka/releases/$release
mkdir -p "$source"
tar -xzf /opt/polka/polka-source.tar.gz -C "$source"
id polka >/dev/null 2>&1 || useradd --system --home /opt/polka --shell /usr/sbin/nologin polka
install -d -o polka -g polka -m 700 /opt/polka/data /opt/polka/backups
if ! test -x /opt/polka/venv/bin/python; then python3 -m venv /opt/polka/venv; fi
/opt/polka/venv/bin/pip install -q -r "$source/requirements.txt"
ln -s "$source" /opt/polka/current.next
mv -Tf /opt/polka/current.next /opt/polka/current
cp "$source/deploy/polka-api.service" /etc/systemd/system/
cp "$source/deploy/polka-backup.service" /etc/systemd/system/
cp "$source/deploy/polka-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now polka-api.service
systemctl restart polka-api.service
systemctl enable --now polka-backup.timer
for attempt in 1 2 3 4 5; do curl -fsS http://127.0.0.1:8195/api/healthz && break || sleep 1; done
curl -fsS http://127.0.0.1:8195/api/healthz
base=/opt/after-rain/site/polka
mkdir -p "$base/releases/$release"
cp -a "$source/public/." "$base/releases/$release/"
cp /opt/after-rain/Caddyfile "/opt/polka/Caddyfile.before-$release"
python3 - "$source/deploy/polka.caddy" <<'PY'
import sys
from pathlib import Path
p=Path('/opt/after-rain/Caddyfile');s=p.read_text();start=s.index('polka.maark.ru {');depth=0;end=None
for i in range(s.index('{',start),len(s)):
    if s[i]=='{':depth+=1
    elif s[i]=='}':
        depth-=1
        if depth==0:end=i+1;break
if end is None:raise RuntimeError('Cannot locate Polka block')
p.write_text(s[:start]+Path(sys.argv[1]).read_text().strip()+s[end:])
PY
if ! docker exec after-rain-web caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; then
    cat "/opt/polka/Caddyfile.before-$release" > /opt/after-rain/Caddyfile
    exit 1
fi
ln -s "releases/$release" "$base/current.next"
mv -Tf "$base/current.next" "$base/current"
docker exec after-rain-web caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl start polka-backup.service
echo "Published $release"

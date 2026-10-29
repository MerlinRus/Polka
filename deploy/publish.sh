#!/bin/sh
set -eu
release="$(date -u +%Y%m%dT%H%M%SZ)"
base=/opt/after-rain/site/polka
mkdir -p "$base/releases/$release"
tar -xzf /opt/polka/polka-release.tar.gz -C "$base/releases/$release"
test -s "$base/releases/$release/index.html"
ln -s "releases/$release" "$base/current.next"
mv -Tf "$base/current.next" "$base/current"
cp /opt/after-rain/Caddyfile "/opt/polka/Caddyfile.before-$release"
if ! grep -q '^polka.maark.ru {' /opt/after-rain/Caddyfile; then
    cat /opt/polka/polka.caddy >> /opt/after-rain/Caddyfile
fi
if ! docker exec after-rain-web caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; then
    cat "/opt/polka/Caddyfile.before-$release" > /opt/after-rain/Caddyfile
    exit 1
fi
docker exec after-rain-web caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
printf 'Published release %s\n' "$release"

#!/bin/sh
# Что из возможностей веб-панели может работать на этой колонке. Только чтение: ничего не пишет и не запускает.
#   adb push device/probe.sh /tmp/webpanel_probe.sh && adb shell sh /tmp/webpanel_probe.sh; adb shell rm /tmp/webpanel_probe.sh
# В выводе есть версия прошивки и список файлов панели; серийных номеров, токенов и адресов нет.
Q=/system/vendor/quasar
s() { echo; echo "== $1"; }
l() { ls "$@" 2>&1 | cat; }
s system; uname -m; uname -r; cat $Q/version_full 2>&1; echo; tr '\0' ' ' < /proc/device-tree/compatible 2>/dev/null; echo
grep -o '"deviceType" *: *"[^"]*"' $Q/quasar.cfg 2>&1 | head -1
s storage; grep -E ' / ' /proc/mounts; l -ld /data; df -k /data/ /tmp | tail -n +2
s tools; for b in mount start-stop-daemon amixer aplay pidof killall wget md5sum; do printf '%s=%s ' $b "$(command -v $b || echo -)"; done; echo
s autostart; grep -Hn '/data/[^ ]*\.sh' /etc/init.d/* $Q/*.sh 2>/dev/null | head -5; l -l /data/quasar/rootkeeper.sh
s display; ls /sys/class/leds 2>/dev/null | sed 's/[0-9]*$//' | sort | uniq -c | tr -s ' ' | tr '\n' ';'; echo
for f in virtual_size bits_per_pixel; do printf 'fb0 %s=%s\n' $f "$(cat /sys/class/graphics/fb0/$f 2>&1)"; done
l $Q/ledpatterns; l -l $Q/ledpatterns/clock/boot.binscled.zst
s sound; cat /proc/asound/cards 2>&1; amixer scontrols 2>&1 | head -4; l -l $Q/sounds/common/boot.wav
s alice; l -l $Q/maind
s yam; l -ld /data/quazar/yam/bin/yamd /data/local/yam/service.sh; pidof yamd || echo 'yamd: not running'
s analytics; l -l /data/quasar/appmetrica/persistedBatch; ps | grep -c '[m]aind --telemetry'
s panel; l /data/quasar/webpanel | tr '\n' ' '; echo; grep -c webpanel-boot-hook /data/quasar/rootkeeper.sh 2>&1

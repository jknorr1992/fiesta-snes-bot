#!/bin/bash
# Headless RetroArch 1.22.2 + snes9x for testing FFV-Bot. Usage: start.sh [rom.sfc]
HERE="$(cd "$(dirname "$0")" && pwd)"
ROM="${1:-$HERE/../FFV/Final Fantasy V (Japan) (patched).sfc}"
mkdir -p "$HERE/saves" "$HERE/shots"
sed "s#@HERE@#$HERE#g" "$HERE/ra.cfg.template" > /tmp/ffv-ra.cfg
chmod +x "$HERE/retroarch"
# `retroarch` is a plain executable file; copying it out of the (non-exec) shared folder may be needed:
cp -f "$HERE/retroarch" /tmp/ffv-retroarch && chmod +x /tmp/ffv-retroarch
LIBGL_ALWAYS_SOFTWARE=1 exec xvfb-run -a -s "-screen 0 1024x768x24" /tmp/ffv-retroarch \
  --config /tmp/ffv-ra.cfg -L "$HERE/snes9x_libretro.so" "$ROM"

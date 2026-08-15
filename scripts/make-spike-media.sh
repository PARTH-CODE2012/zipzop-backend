#!/usr/bin/env bash
#
# Test media for the M1 compositor spike.
#
# Two 480p H.264 faststart files with audio — deliberately the same shape the
# ingest worker will produce in M2 (docs/03-backend-architecture.md 6.2), so
# that when the spike is wired to real proxies nothing about the playback path
# changes. H.264 specifically because Safari is reliable with it.
#
# The two clips are visually unmistakable from one another (colour test pattern
# vs SMPTE bars) and carry a burnt-in timecode. That is the whole point: a cut
# with a black flash, a frame repeated, or a frame skipped is visible without
# instrumentation.
#
# Output is gitignored. Run `make spike-media` to (re)generate.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/frontend/public/spike"
FONT="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

DURATION=8
WIDTH=854
HEIGHT=480
FPS=30

command -v ffmpeg >/dev/null 2>&1 || {
  echo "ffmpeg not found — sudo apt install ffmpeg" >&2
  exit 1
}
command -v python3 >/dev/null 2>&1 || {
  echo "python3 not found" >&2
  exit 1
}

mkdir -p "$OUT/media" "$OUT/luts"

# drawtext needs a real font file. Fall back to fontconfig's answer if the
# DejaVu path is not where Debian puts it.
if [ ! -f "$FONT" ]; then
  FONT="$(fc-match -f '%{file}' 'sans:bold' 2>/dev/null || true)"
fi
[ -f "$FONT" ] || {
  echo "no usable TTF font found for drawtext" >&2
  exit 1
}

label() { # $1 = text drawn top-left
  printf "drawtext=fontfile='%s':text='%s':fontcolor=white:fontsize=54:box=1:boxcolor=black@0.65:boxborderw=14:x=32:y=28" "$FONT" "$1"
}

timecode() {
  printf "drawtext=fontfile='%s':text='%%{pts\\\\:hms}  f%%{n}':fontcolor=white:fontsize=34:box=1:boxcolor=black@0.65:boxborderw=10:x=32:y=h-th-28" "$FONT"
}

encode() { # $1 = lavfi video source, $2 = extra video filters, $3 = sine freq, $4 = output
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "$1" \
    -f lavfi -i "sine=frequency=$3:sample_rate=48000:duration=$DURATION" \
    -filter_complex "[0:v]$2[v]" \
    -map "[v]" -map 1:a \
    -c:v libx264 -profile:v high -level 4.0 -pix_fmt yuv420p \
    -preset veryfast -crf 22 -g "$FPS" -keyint_min "$FPS" -sc_threshold 0 \
    -c:a aac -b:a 96k -ac 2 \
    -movflags +faststart -shortest \
    "$4"
  echo "  wrote $(basename "$4")"
}

echo "generating spike media in $OUT/media"

# Clip A — colour test pattern, cool cast, 220 Hz tone.
encode \
  "testsrc2=size=${WIDTH}x${HEIGHT}:rate=${FPS}:duration=${DURATION}" \
  "hue=h=190:s=1.15,$(label 'CLIP A'),$(timecode)" \
  220 \
  "$OUT/media/clip-a.mp4"

# Clip B — SMPTE bars with a moving box so motion is visible, 440 Hz tone.
encode \
  "smptehdbars=size=${WIDTH}x${HEIGHT}:rate=${FPS}:duration=${DURATION}" \
  "drawbox=x='mod(t*260\,${WIDTH}+120)-120':y=${HEIGHT}-150:w=110:h=110:color=black@0.85:t=fill,$(label 'CLIP B'),$(timecode)" \
  440 \
  "$OUT/media/clip-b.mp4"

echo "generating LUT in $OUT/luts"
python3 "$ROOT/scripts/make_spike_lut.py" "$OUT/luts/cinematic_warm.cube"

echo
echo "done — open http://localhost:3000/spike/compositor (make dev-frontend)"

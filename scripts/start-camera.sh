#!/usr/bin/env bash
set -Eeuo pipefail

CAMERA_NAME="${CAMERA_NAME:-USB Camera}"
VIDEO_DEVICE="${VIDEO_DEVICE:-/dev/video0}"
STREAM_NAME="${STREAM_NAME:-usb}"
WIDTH="${WIDTH:-854}"
HEIGHT="${HEIGHT:-480}"
FRAMERATE="${FRAMERATE:-20}"
BITRATE_KBPS="${BITRATE_KBPS:-1200}"
FFMPEG_BIN="${FFMPEG_BIN:-ffmpeg}"
RTSP_HOST="${RTSP_HOST:-127.0.0.1}"
RTSP_PORT="${RTSP_PORT:-8556}"

if ! command -v "$FFMPEG_BIN" >/dev/null 2>&1; then
    echo "FFmpeg introuvable: $FFMPEG_BIN" >&2
    echo "Installez-le avec: sudo apt update && sudo apt install -y ffmpeg" >&2
    exit 1
fi

BITRATE="${BITRATE_KBPS}k"
BUFFER_SIZE="$((BITRATE_KBPS * 2))k"
OUTPUT_URL="rtsp://${RTSP_HOST}:${RTSP_PORT}/${STREAM_NAME}"
OS_NAME="$(uname -s)"

if [[ "$OS_NAME" =~ ^(MINGW|MSYS|CYGWIN) ]]; then
    input="video=${CAMERA_NAME}"
    input_args=(
        -f dshow
        -video_size 1280x720
        -framerate 30
        -i "$input"
    )
elif [[ "$OS_NAME" == "Linux" ]]; then
    input_args=(
        -f v4l2
        -video_size 1280x720
        -framerate 30
        -i "$VIDEO_DEVICE"
    )
else
    echo "Systeme non supporte: $OS_NAME. Utilisez Windows Git Bash ou Linux." >&2
    exit 1
fi

"$FFMPEG_BIN" \
    "${input_args[@]}" \
    -vf "scale=${WIDTH}:${HEIGHT}:flags=fast_bilinear" \
    -r "$FRAMERATE" \
    -pix_fmt yuv420p \
    -c:v libx264 \
    -preset veryfast \
    -tune zerolatency \
    -profile:v main \
    -b:v "$BITRATE" \
    -maxrate "$BITRATE" \
    -bufsize "$BUFFER_SIZE" \
    -g "$((FRAMERATE * 2))" \
    -f rtsp \
    -rtsp_transport tcp \
    "$OUTPUT_URL"

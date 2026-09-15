#!/usr/bin/env bash
# Generic subtitle-burn renderer for the "video factory".
# Burns an .ass subtitle file onto a source video using ffmpeg/libass,
# then uploads the result to a storage bucket (Supabase Storage).
#
# NO production data is stored in this repository. All content-specific
# values (source video, subtitle file, output name) are passed at runtime
# as inputs; the storage credential is injected from an encrypted secret.
#
# Required environment variables:
#   VIDEO_URL     Public/temporary URL of the source (already-assembled) MP4
#   ASS_URL       URL of the .ass subtitle file to burn in
#   OUTPUT_NAME   Target object name in the bucket, e.g. acc_a_..._v3_2360.mp4
#   SUPABASE_URL  Base URL, e.g. https://xxxx.supabase.co
#   SUPABASE_BUCKET  Bucket name, e.g. Videos
#   SUPABASE_SERVICE_KEY  Service role key (from repo secret; never logged)
#
# Optional:
#   FFMPEG_PRESET  x264 preset (default: medium)
#   FFMPEG_CRF     x264 CRF quality (default: 18)
#   FFMPEG_THREADS Thread cap (default: 2). MUST stay low: during local
#                  validation libx264 defaulting to many threads was killed
#                  by the OOM killer (exit 137). Runners have 2 vCPU anyway.

set -euo pipefail

: "${VIDEO_URL:?VIDEO_URL is required}"
: "${ASS_URL:?ASS_URL is required}"
: "${OUTPUT_NAME:?OUTPUT_NAME is required}"
: "${SUPABASE_URL:?SUPABASE_URL is required}"
: "${SUPABASE_BUCKET:?SUPABASE_BUCKET is required}"
: "${SUPABASE_SERVICE_KEY:?SUPABASE_SERVICE_KEY is required}"

PRESET="${FFMPEG_PRESET:-medium}"
CRF="${FFMPEG_CRF:-18}"
THREADS="${FFMPEG_THREADS:-2}"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
cd "$WORKDIR"

echo "::group::Environment"
ffmpeg -hide_banner -version | head -n 1
echo "Fonts dir: ${GITHUB_WORKSPACE:-.}/fonts"
echo "::endgroup::"

echo "::group::Download inputs"
# -f fail on HTTP errors, -sSL silent+show-errors+follow redirects
curl -fSL --retry 3 --retry-delay 2 -o source.mp4 "$VIDEO_URL"
curl -fSL --retry 3 --retry-delay 2 -o subs.ass "$ASS_URL"
ls -la source.mp4 subs.ass
echo "::endgroup::"

echo "::group::Validate source is a real media file"
# Fail early with a clear message if the source is not a valid video.
if ! ffprobe -v error -select_streams v:0 -show_entries stream=codec_type -of csv=p=0 source.mp4 | grep -q video; then
  echo "ERROR: source.mp4 is not a valid video file (no video stream)."
  exit 3
fi
echo "::endgroup::"

FONTS_DIR="${GITHUB_WORKSPACE:-..}/fonts"

echo "::group::Render (burn subtitles)"
# libass reads fonts from FONTS_DIR; Barlow Semi Condensed Bold ships with the repo.
# Audio is stream-copied (no re-encode) to preserve quality and speed.
# -threads / x264 thread caps prevent the exit-137 OOM kill seen locally.
ffmpeg -hide_banner -y \
  -threads "$THREADS" \
  -i source.mp4 \
  -vf "ass=subs.ass:fontsdir=${FONTS_DIR}" \
  -c:v libx264 -preset "$PRESET" -crf "$CRF" -pix_fmt yuv420p \
  -x264-params "threads=${THREADS}:lookahead_threads=1" \
  -c:a copy \
  -movflags +faststart \
  output.mp4
ls -la output.mp4
echo "::endgroup::"

echo "::group::Upload to Supabase Storage"
UPLOAD_URL="${SUPABASE_URL}/storage/v1/object/${SUPABASE_BUCKET}/${OUTPUT_NAME}"
# x-upsert:true overwrites if the object already exists. Service key is never echoed.
HTTP_CODE=$(curl -fsS -o upload_resp.json -w '%{http_code}' -X POST "$UPLOAD_URL" \
  -H "Authorization: Bearer ${SUPABASE_SERVICE_KEY}" \
  -H "apikey: ${SUPABASE_SERVICE_KEY}" \
  -H "Content-Type: video/mp4" \
  -H "x-upsert: true" \
  --data-binary @output.mp4) || {
    echo "ERROR: upload failed (HTTP ${HTTP_CODE:-?})"
    cat upload_resp.json 2>/dev/null || true
    exit 4
  }
echo "Upload HTTP status: $HTTP_CODE"
echo "Public URL: ${SUPABASE_URL}/storage/v1/object/public/${SUPABASE_BUCKET}/${OUTPUT_NAME}"
echo "::endgroup::"

echo "Done."

#!/usr/bin/env python3
"""Assemble a silent 9:16 MP4 from the shotstack_body produced by W6 Build Timeline.

Usage: python scripts/assemble.py timeline.json output.mp4 [--target-duration SECONDS]
No service credentials are required; image URLs must be accessible.
"""
import argparse
import copy
import json
import math
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

FPS = 15
WIDTH, HEIGHT = 1080, 1920


def run(args):
    subprocess.run(args, check=True)


def download(url, path):
    if not url.startswith(('https://', 'http://')):
        raise ValueError('Invalid image URL')
    req = urllib.request.Request(url, headers={'User-Agent': 'Austalys-visual-render/1.0'})
    with urllib.request.urlopen(req, timeout=45) as response, path.open('wb') as out:
        import shutil
        shutil.copyfileobj(response, out)
    if path.stat().st_size < 100:
        raise ValueError('Empty image download')


def render_segment(image, clip, target):
    duration = float(clip['length'])
    frames = max(1, round(duration * FPS))
    effect = clip.get('effect', 'zoomIn')
    if effect == 'zoomOut':
        zoom = f"max(1.0,1.12-0.12*on/{max(1, frames-1)})"
        panx, pany = '(iw-iw/zoom)/2', '(ih-ih/zoom)/2'
    elif effect in ('slideLeft', 'slideRight', 'slideUp'):
        zoom = '1.12'
        p = f'on/{max(1, frames-1)}'
        if effect == 'slideLeft':
            panx, pany = f'(iw-iw/zoom)*{p}', '(ih-ih/zoom)/2'
        elif effect == 'slideRight':
            panx, pany = f'(iw-iw/zoom)*(1-{p})', '(ih-ih/zoom)/2'
        else:
            panx, pany = '(iw-iw/zoom)/2', f'(ih-ih/zoom)*{p}'
    else:
        zoom = f"min(1.12,1.0+0.12*on/{max(1, frames-1)})"
        panx, pany = '(iw-iw/zoom)/2', '(ih-ih/zoom)/2'
    # Panning is deliberately subtle, avoiding the jarring full-frame slide that
    # would require reconstructing image content outside its original bounds.
    fade = min(0.35, duration / 5)
    in_fade = clip.get('transition', {}).get('in') == 'fade'
    out_fade = clip.get('transition', {}).get('out') == 'fade'
    vf = (f'scale=1296:2304:force_original_aspect_ratio=increase,'
          f'crop=1296:2304,setsar=1,'
          f"zoompan=z='{zoom}':x='{panx}':y='{pany}':d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
          f'format=yuv420p')
    if in_fade:
        vf += f',fade=t=in:st=0:d={fade:.3f}'
    if out_fade:
        vf += f',fade=t=out:st={max(0, duration-fade):.3f}:d={fade:.3f}'
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(image),
         '-vf', vf, '-frames:v', str(frames), '-an', '-c:v', 'libx264',
         '-preset', 'veryfast', '-crf', '22', '-pix_fmt', 'yuv420p',
         '-threads', '2', str(target)])


def prepare_clips(clips, target_duration=None):
    """Validate source timing and distribute target frames across animated clips.

    The target includes the final fade; no extra second is appended. Round the
    total upward so the visual cannot end before narration (at most one frame).
    Cumulative boundaries avoid per-segment rounding drift.
    """
    if not clips:
        raise ValueError('No clips')
    previous_end = 0.0
    lengths = []
    for clip in clips:
        if clip['asset']['type'] != 'image':
            raise ValueError('Unsupported asset type')
        start, length = float(clip['start']), float(clip['length'])
        if not math.isfinite(start) or not math.isfinite(length) or length <= 0:
            raise ValueError('Clip times must be finite and lengths positive')
        if abs(start - previous_end) > 0.003:
            raise ValueError('Gap/overlap in timeline; refusing truncated render')
        previous_end = start + length
        lengths.append(length)
    result = copy.deepcopy(clips)
    if target_duration is None:
        if previous_end > 70:
            print('WARNING: source timeline exceeds 70 seconds; correct upstream timing.', file=sys.stderr)
        return result
    target = float(target_duration)
    if not math.isfinite(target) or target <= 0:
        raise ValueError('Target duration must be finite and positive')
    if target > 70:
        raise ValueError('Narration/target exceeds 70 seconds: shorten the script and regenerate narration upstream; do not truncate or change audio speed')
    total_frames = math.ceil(target * FPS)
    if total_frames < len(clips):
        raise ValueError('Target too short: each image needs at least one frame')
    total_length = math.fsum(lengths)
    cumulative, boundary = 0.0, 0
    for idx, (clip, length) in enumerate(zip(result, lengths)):
        cumulative += length
        end = round(cumulative / total_length * total_frames)
        # Reserve one frame per remaining clip, even for tiny source segments.
        end = min(total_frames - (len(clips) - idx - 1), max(boundary + 1, end))
        clip['start'] = boundary / FPS
        clip['length'] = (end - boundary) / FPS
        boundary = end
    print(f'Timeline: {total_length:.3f}s -> {total_frames / FPS:.3f}s '
          f'({total_frames} frames; target {target:.3f}s includes final fade)')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('timeline')
    parser.add_argument('output')
    parser.add_argument('--target-duration', type=float,
                        help='Measured narration duration in seconds, including final fade (maximum 70)')
    args = parser.parse_args()
    data = json.loads(Path(args.timeline).read_text(encoding='utf-8'))
    body = data.get('shotstack_body', data)
    clips = prepare_clips(body['timeline']['tracks'][0]['clips'], args.target_duration)
    if body['output']['size'] != {'width': WIDTH, 'height': HEIGHT}:
        raise ValueError('Expected 1080 x 1920 output')
    if float(body['output']['fps']) != FPS:
        raise ValueError('Expected 15 FPS')
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        paths = []
        for idx, clip in enumerate(clips):
            image = folder / f'input_{idx}.img'
            segment = folder / f'segment_{idx}.mp4'
            download(clip['asset']['src'], image)
            render_segment(image, clip, segment)
            paths.append(segment)
        listfile = folder / 'concat.txt'
        listfile.write_text(''.join(f"file '{p}'\n" for p in paths), encoding='utf-8')
        run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat',
             '-safe', '0', '-i', str(listfile), '-an', '-c', 'copy',
             '-movflags', '+faststart', str(Path(args.output).resolve())])
    print('Rendered:', args.output)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Assemble a silent 9:16 MP4 from the shotstack_body produced by W6 Build Timeline.

Usage: python scripts/assemble.py timeline.json output.mp4
No service credentials are required; image URLs must be accessible.
"""
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


def main():
    if len(sys.argv) != 3:
        sys.exit('Usage: assemble.py timeline.json output.mp4')
    data = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    if isinstance(data, list):
    if len(data) != 1 or not isinstance(data[0], dict):
        raise ValueError("Expected one timeline object")
    data = data[0]
    body = data.get('shotstack_body', data)
    clips = body['timeline']['tracks'][0]['clips']
    if not clips:
        raise ValueError('No clips')
    assert body['output']['size'] == {'width': WIDTH, 'height': HEIGHT}
    if int(body['output']['fps']) != FPS:
        raise ValueError('Expected 15 FPS')
    previous_end = 0.0
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        paths = []
        for idx, clip in enumerate(clips):
            if clip['asset']['type'] != 'image':
                raise ValueError('Unsupported asset type')
            if abs(float(clip['start']) - previous_end) > 0.003:
                raise ValueError('Gap/overlap in timeline; refusing truncated render')
            previous_end = float(clip['start']) + float(clip['length'])
            image = folder / f'input_{idx}.img'
            segment = folder / f'segment_{idx}.mp4'
            download(clip['asset']['src'], image)
            render_segment(image, clip, segment)
            paths.append(segment)
        listfile = folder / 'concat.txt'
        listfile.write_text(''.join(f"file '{p}'\n" for p in paths), encoding='utf-8')
        run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat',
             '-safe', '0', '-i', str(listfile), '-an', '-c', 'copy',
             '-movflags', '+faststart', str(Path(sys.argv[2]).resolve())])
    print('Rendered:', sys.argv[2])


if __name__ == '__main__':
    main()

import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('assemble', Path(__file__).resolve().parents[1] / 'scripts/assemble.py')
assemble = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assemble)


def clips():
    result = []
    start = 0
    for length, effect in zip([12, 13, 14, 15, 10.33], ['zoomIn', 'zoomOut', 'slideLeft', 'slideRight', 'slideUp']):
        result.append({'start': start, 'length': length, 'effect': effect,
                       'asset': {'type': 'image', 'src': 'https://example.invalid/image'},
                       'transition': {'in': 'fade', 'out': 'fade'}})
        start += length
    return result


class TimingTests(unittest.TestCase):
    def test_real_duration_proportions_and_contiguity(self):
        source = clips()
        original = copy.deepcopy(source)
        result = assemble.prepare_clips(source, 69.85)
        frames = [round(c['length'] * 15) for c in result]
        self.assertEqual(sum(frames), 1048)
        self.assertGreaterEqual(sum(frames) / 15, 69.85)
        self.assertLess(sum(frames) / 15 - 69.85, 1 / 15)
        boundary = 0
        for old, new, count in zip(source, result, frames):
            self.assertAlmostEqual(new['start'], boundary / 15)
            self.assertLessEqual(abs(count - old['length'] / 64.33 * 1048), 1)
            for field in ['effect', 'asset', 'transition']:
                self.assertEqual(new[field], old[field])
            boundary += count
        self.assertEqual(source, original)

    def test_legacy_unchanged(self):
        self.assertEqual(assemble.prepare_clips(clips()), clips())

    def test_shortening_and_long_many_clip_timeline(self):
        for target in (60, 64.33, 69.85, 70):
            source = [{'asset': {'type': 'image'}, 'start': i * .1, 'length': .1} for i in range(643)]
            result = assemble.prepare_clips(source, target)
            self.assertEqual(sum(round(c['length'] * 15) for c in result), math.ceil(target * 15))
            for left, right in zip(result, result[1:]):
                self.assertAlmostEqual(left['start'] + left['length'], right['start'])

    def test_invalid_targets_and_upstream_limit(self):
        for target in (0, -1, float('nan'), float('inf'), .01):
            with self.subTest(target=target), self.assertRaises(ValueError):
                assemble.prepare_clips(clips(), target)
        for target in (70.01, 70.87):
            with self.assertRaisesRegex(ValueError, 'upstream'):
                assemble.prepare_clips(clips(), target)

    def test_invalid_source(self):
        for change in ({'start': .1}, {'start': -.1}, {'length': 0}, {'length': -1}, {'length': float('nan')}, {'start': float('inf')}, {'asset': {'type': 'video'}}):
            source = clips()
            source[0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                assemble.prepare_clips(source, 69.85)
        with self.assertRaises(ValueError):
            assemble.prepare_clips([], 69.85)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class RenderTests(unittest.TestCase):
    def test_full_length_animated_visual(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            image = folder / 'pattern.ppm'
            image.write_bytes(b'P6\n180 320\n255\n' + bytes(v for y in range(320) for x in range(180)
                             for v in ((x * 7 + y * 3) % 256, (x * 3 + y * 5) % 256, (y * 11) % 256)))
            body = {'timeline': {'tracks': [{'clips': clips()}]},
                    'output': {'size': {'width': 1080, 'height': 1920}, 'fps': 15}}
            timeline, output = folder / 'timeline.json', folder / 'visual.mp4'
            timeline.write_text(json.dumps({'shotstack_body': body}))
            with patch.object(sys, 'argv', ['assemble.py', str(timeline), str(output), '--target-duration', '69.85']), \
                 patch.object(assemble, 'download', side_effect=lambda url, path: shutil.copyfile(image, path)) as download:
                assemble.main()
            self.assertEqual(download.call_count, 5)
            data = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(output)]))
            self.assertEqual(len(data['streams']), 1)
            video = data['streams'][0]
            self.assertEqual((video['width'], video['height'], video['r_frame_rate']), (1080, 1920, '15/1'))
            self.assertEqual(int(video['nb_frames']), 1048)
            self.assertAlmostEqual(float(video['duration']), 1048 / 15, places=5)
            self.assertLess(output.stat().st_size, 50_000_000)
            # Check the final six seconds for a prolonged hold. Integer pixel
            # panning can legitimately repeat adjacent frames at this frame rate.
            raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', '63.85', '-i', str(output),
                                           '-vf', 'scale=90:160', '-pix_fmt', 'gray', '-threads', '1', '-f', 'rawvideo', '-'])
            frames = [raw[i:i + 14400] for i in range(0, len(raw), 14400)]
            self.assertGreaterEqual(len(frames), 89)
            hashes = [hashlib.sha256(frame).digest() for frame in frames]
            longest_hold = run = 1
            for previous, current in zip(hashes, hashes[1:]):
                run = run + 1 if previous == current else 1
                longest_hold = max(longest_hold, run)
            self.assertGreater(len(set(hashes)), len(frames) * .8)
            self.assertLessEqual(longest_hold, 3)  # <= 0.2 s, never a multi-second freeze
            self.assertGreater(sum(frames[0]) / len(frames[0]), 30)
            self.assertLess(sum(frames[-1]) / len(frames[-1]), 35)
            print(f'Integration: {video["duration"]}s, 1048 frames, {output.stat().st_size} bytes; final {len(frames)} frames: {len(set(hashes))} distinct, longest hold {longest_hold}/15 s')


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Download public VinUni cubemap tiles from scene metadata or HAR; stitch faces.

No guessing scene IDs, no decryption, no authentication headers from HAR.
Requires Python 3.10+, Pillow and the system curl executable.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

from PIL import Image

FACES = 'fblrud'
TILE_RE = re.compile(r'/panos/([^/]+)/([fblrud])/l(\d+)/(\d+)/l(\d+)_([fblrud])_(\d+)_(\d+)\.(?:jpg|jpeg|png)$', re.I)


def safe(s):
    clean = re.sub(r'[^a-zA-Z0-9_.-]', '_', s)[:70]
    return (clean or 'scene') + ('_' + hashlib.sha256(s.encode()).hexdigest()[:8] if clean != s else '')


def public_url(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in {'vinuni.edu.vn', 'www.vinuni.edu.vn'} or p.port not in (None, 443):
        raise ValueError('Only HTTPS image URLs on vinuni.edu.vn are supported.')
    if p.username or p.password:
        raise ValueError('URLs with credentials are not supported.')
    return url


@dataclass
class Face:
    scene: str
    level: int
    face: str
    base: int = 1
    width: int | None = None
    height: int | None = None
    tile_size: int | None = None
    rows: int | None = None
    cols: int | None = None
    urls: dict = field(default_factory=dict)
    embedded: dict = field(default_factory=dict)

    def directory(self, out):
        return out / 'tiles' / safe(self.scene) / f'l{self.level}' / self.face

    def expected(self):
        if self.rows is None or self.cols is None:
            raise ValueError('Grid size unknown; supply metadata or --grid.')
        return {(r, c) for r in range(self.base, self.base + self.rows)
                       for c in range(self.base, self.base + self.cols)}


def expand(template, face, level, row, col):
    vals = {'s': face, 'l': level, 'v': row, 'y': row, 'r': row,
            'h': col, 'x': col, 'u': col, 'c': col}
    def replace(m):
        value = str(vals[m[2]])
        return value.zfill(len(m[1]) + 1) if m[1] else value
    result = re.sub(r'%(0*)([slvyrhxuc])', replace, template)
    if re.search(r'%(?![0-9a-fA-F]{2})', result):
        raise ValueError('Unresolved URL placeholder; this scene needs HAR fallback.')
    return result


def manifest_faces(path, choice, select):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('format') != 'campusar-krpano-scenes-v1':
        raise ValueError('Unknown manifest format. Use export_scenes.js.')
    base_url = public_url(data['base_url'])
    page_url = data['source_url']
    groups, skipped = [], []
    for scene in data['scenes']:
        name = scene['name']
        if select and name not in select:
            continue
        try:
            raw = scene.get('content', '').strip()
            root = ET.fromstring('<root>' + raw + '</root>')
            # Prefer desktop image when a scene defines separate mobile images.
            images = root.findall('.//image')
            image = next((i for i in images if 'mobile' not in i.get('devices', '').split('.')), None)
            if image is None:
                raise ValueError('No supported desktop <image>.')
            if int(image.get('tileoverlap', '0')) != 0:
                raise ValueError('Overlapping tile borders are not supported.')
            if image.get('stereo', 'false').lower() not in ('false', '0'):
                raise ValueError('Stereo image is not supported.')
            if image.get('cubelabels', 'l|f|r|b|u|d') != 'l|f|r|b|u|d':
                raise ValueError('Custom cube labels are not supported.')
            origin = int(image.get('baseindex', '1'))
            levels = []
            for i, lev in enumerate(image.findall('level')):
                cube = lev.find('cube')
                if cube is None:
                    continue
                w = int(lev.get('tiledimagewidth', '0'))
                h = int(lev.get('tiledimageheight', str(w)))
                ts = int(lev.get('tilesize', image.get('tilesize', '0')))
                if min(w, h, ts) <= 0:
                    raise ValueError('Missing level dimensions/tile size.')
                template = cube.get('url', '')
                # Older tours often list levels largest-first but use literal
                # l2/l1 in URLs. Preserve those labels instead of array order.
                literal = re.search(r'/l(\d+)(?:/|_)', template)
                number = int(literal[1]) if literal else i + origin
                levels.append((number, w, h, ts, template))
            if not levels:
                raise ValueError('Expected long-syntax <level><cube>; use HAR for other formats.')
            picked = max(levels, key=lambda x: x[1] * x[2]) if choice == 'highest' else next((x for x in levels if x[0] == int(choice)), None)
            if picked is None:
                raise ValueError(f'Level {choice} not found.')
            number, w, h, ts, template = picked
            if not template:
                raise ValueError('Empty cube URL template.')
            template = template.replace('%HTMLPATH%', urljoin(page_url, './').rstrip('/'))
            for token in ('%SWFPATH%', '%VIEWER%', '%ROOT%'):
                template = template.replace(token, urljoin(page_url, './').rstrip('/'))
            for token in ('%CURRENTXML%', '%FIRSTXML%'):
                template = template.replace(token, urljoin(base_url, './').rstrip('/'))
            created = []
            for face in FACES:
                g = Face(name, number, face, origin, w, h, ts, math.ceil(h / ts), math.ceil(w / ts))
                for r, c in sorted(g.expected()):
                    g.urls[(r, c)] = public_url(urljoin(base_url, expand(template, face, number, r, c)))
                created.append(g)
            groups.extend(created)
        except (ET.ParseError, ValueError, KeyError, TypeError) as exc:
            skipped.append({'scene': name, 'reason': str(exc)})
    return groups, skipped


def har_faces(paths, choice, select, grid):
    found = {}
    for path in paths:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        for entry in data.get('log', {}).get('entries', []):
            url = entry['request']['url']
            m = TILE_RE.search(urlsplit(url).path)
            if not m:
                continue
            scene, face, level, folder, file_level, file_face, row, col = m.groups()
            if select and scene not in select:
                continue
            if level != file_level or face != file_face or folder != row:
                continue
            public_url(url)
            # Separate scenes with identical names hosted under different source folders.
            prefix = url.split('/panos/')[0]
            key = (prefix, scene, int(level), face)
            g = found.setdefault(key, Face(scene, int(level), face))
            rc = (int(row), int(col))
            g.urls[rc] = url
            content = entry.get('response', {}).get('content', {})
            if content.get('encoding') == 'base64' and content.get('text'):
                g.embedded[rc] = content['text']
    owners = {}
    for prefix, scene, _, _ in found:
        owners.setdefault(scene, set()).add(prefix)
    levels = {}
    for prefix, scene, level, _ in found:
        levels.setdefault((prefix, scene), set()).add(level)
    selected = []
    for (prefix, scene, level, face), g in found.items():
        wanted = max(levels[(prefix, scene)]) if choice == 'highest' else int(choice)
        if level != wanted:
            continue
        if len(owners[scene]) > 1:
            g.scene += '_' + hashlib.sha256(prefix.encode()).hexdigest()[:8]
        if grid:
            g.rows, g.cols = grid
            if min(grid) < 1:
                raise ValueError('--grid must be positive.')
            if any(r < 1 or c < 1 or r > g.rows or c > g.cols for r, c in g.urls):
                raise ValueError(f'Grid too small for {g.scene}/{face}.')
        selected.append(g)
    return selected, []


def valid_image(path):
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except (OSError, ValueError):
        return False


def download_one(g, rc, out):
    path = g.directory(out) / f'{rc[0]}_{rc[1]}.jpg'
    if valid_image(path):
        return None
    tmp = path.with_suffix('.part')
    try:
        if rc in g.embedded:
            tmp.write_bytes(base64.b64decode(g.embedded[rc], validate=True))
        else:
            # Public download only. Never copy cookies/auth headers from HAR.
            run = subprocess.run(['curl', '--location', '--fail', '--silent', '--show-error',
                '--proto', '=https', '--proto-redir', '=https', '--max-redirs', '3',
                '--connect-timeout', '15', '--max-time', '60', '--retry', '1',
                '--retry-delay', '2', '--write-out', '%{http_code}', '--output', str(tmp), g.urls[rc]],
                capture_output=True, timeout=140)
            if run.returncode:
                detail = run.stderr.decode('utf-8', errors='replace').strip()
                detail = re.sub(r'https?://\S+', '[URL]', detail)
                http = run.stdout.decode('ascii', errors='replace').strip()
                return f'curl exit={run.returncode}; HTTP={http or "unknown"}; {detail[:700]}'
        if not valid_image(tmp):
            return 'Response is not a valid image.'
        tmp.replace(path)
        return None
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return f'{type(exc).__name__}: {str(exc)[:500]}'
    finally:
        tmp.unlink(missing_ok=True)


def merge_face(g, out):
    expected = g.expected()
    missing = sorted(rc for rc in expected if not valid_image(g.directory(out) / f'{rc[0]}_{rc[1]}.jpg'))
    if missing:
        return {'status': 'incomplete', 'missing': missing}
    images = {}
    try:
        for r, c in expected:
            with Image.open(g.directory(out) / f'{r}_{c}.jpg') as im:
                images[r, c] = im.convert('RGB')
        rows = range(g.base, g.base + g.rows)
        cols = range(g.base, g.base + g.cols)
        widths = [images[g.base, c].width for c in cols]
        heights = [images[r, g.base].height for r in rows]
        if g.tile_size:
            widths = [min(g.tile_size, g.width - i * g.tile_size) for i in range(g.cols)]
            heights = [min(g.tile_size, g.height - i * g.tile_size) for i in range(g.rows)]
        elif g.rows != g.cols:
            return {'status': 'unsupported', 'reason': 'A cubemap face must be square.'}
        for (r, c), im in images.items():
            w, h = widths[c - g.base], heights[r - g.base]
            # Permit padded border tiles only when dimensions come from metadata.
            if im.width < w or im.height < h or (not g.tile_size and im.size != (w, h)):
                return {'status': 'invalid_dimensions', 'tile': [r, c], 'actual': list(im.size)}
        if sum(widths) != sum(heights):
            return {'status': 'invalid_dimensions', 'reason': 'Face is not square.'}
        canvas = Image.new('RGB', (sum(widths), sum(heights)))
        for (r, c), im in images.items():
            ri, ci = r - g.base, c - g.base
            canvas.paste(im.crop((0, 0, widths[ci], heights[ri])), (sum(widths[:ci]), sum(heights[:ri])))
        dest = out / 'faces' / safe(g.scene) / f'l{g.level}'
        dest.mkdir(parents=True, exist_ok=True)
        filename = dest / f'{g.face}.jpg'
        canvas.save(filename, quality=95)
        return {'status': 'merged', 'path': str(filename), 'size': list(canvas.size),
                'extent_basis': 'scene_metadata' if g.width else 'user_supplied_grid'}
    finally:
        for image in images.values():
            image.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--manifest', type=Path)
    source.add_argument('--har', type=Path, nargs='+')
    p.add_argument('--out', type=Path, default=Path('vinuni-output'))
    p.add_argument('--scene', nargs='+', help='Exact scene names; omit to process every supported scene.')
    p.add_argument('--level', default='highest', help='highest or level number, e.g. 2')
    p.add_argument('--grid', help='HAR only: known ROWSxCOLS, e.g. 5x5 for a1/l2; never assume this for every scene.')
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--merge-only', action='store_true')
    p.add_argument('--probe', action='store_true', help='Download one tile and print the exact error before a full run.')
    args = p.parse_args()
    if args.level != 'highest' and not args.level.isdigit():
        p.error('--level must be highest or a nonnegative integer.')
    if not 1 <= args.workers <= 8:
        p.error('--workers must be between 1 and 8.')
    try:
        grid = tuple(map(int, args.grid.lower().split('x'))) if args.grid else None
        if grid and (len(grid) != 2 or min(grid) < 1):
            raise ValueError('Use --grid ROWSxCOLS.')
        if args.manifest and grid:
            raise ValueError('--grid is for HAR input only.')
        groups, skipped = manifest_faces(args.manifest, args.level, args.scene) if args.manifest else har_faces(args.har, args.level, args.scene, grid)
    except (OSError, ValueError, KeyError) as exc:
        p.error(str(exc))
    args.out.mkdir(parents=True, exist_ok=True)
    summary = []
    for g in groups:
        row = {'scene': g.scene, 'level': g.level, 'face': g.face, 'urls': len(g.urls),
               'rows': g.rows, 'cols': g.cols, 'width': g.width, 'height': g.height}
        summary.append(row)
        print(f'{g.scene:24} l{g.level} {g.face}: {len(g.urls):5} tiles; grid {g.rows}x{g.cols}', flush=True)
    print(f'Scenes: {len(set(g.scene for g in groups))}; tiles: {sum(len(g.urls) for g in groups)}; skipped scenes: {len(skipped)}', flush=True)
    plan = {'faces': summary, 'skipped_scenes': skipped}
    (args.out / 'plan.json').write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding='utf-8')
    for s in skipped:
        print('SKIP', s['scene'], s['reason'])
    if args.dry_run:
        return 2 if skipped or not groups else 0
    if not groups:
        p.error('No supported image tiles found. See plan.json.')
    if args.probe:
        g = groups[0]
        rc = min(g.urls)
        g.directory(args.out).mkdir(parents=True, exist_ok=True)
        error = download_one(g, rc, args.out)
        print(f'PROBE {g.scene}/l{g.level}/{g.face}/{rc[0]}_{rc[1]}: {error or "OK: valid image saved"}', flush=True)
        return 2 if error else 0
    errors = []
    if not args.merge_only:
        if not shutil.which('curl'):
            p.error('curl is required; install it or use --merge-only with cached tiles.')
        jobs = []
        for g in groups:
            g.directory(args.out).mkdir(parents=True, exist_ok=True)
            jobs.extend((g, rc) for rc in g.urls)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(download_one, g, rc, args.out): (g, rc) for g, rc in jobs}
            for i, future in enumerate(as_completed(futures), 1):
                g, rc = futures[future]
                error = future.result()
                if error:
                    errors.append({'scene': g.scene, 'face': g.face, 'level': g.level, 'tile': rc, 'error': error})
                    if len(errors) <= 3:
                        print(f'ERROR {g.scene}/{g.face}/{rc}: {error}', flush=True)
                if i % 25 == 0 or i == len(jobs):
                    print(f'Download {i}/{len(jobs)}; errors {len(errors)}', flush=True)
    results = []
    for g in groups:
        r = {'scene': g.scene, 'level': g.level, 'face': g.face}
        if g.rows is None:
            r.update(status='grid_unknown', reason='Downloaded observed tiles only. Supply --grid from known dimensions to merge.')
        else:
            r.update(merge_face(g, args.out))
        results.append(r)
    scenes = []
    for name in sorted(set(g.scene for g in groups)):
        rs = [r for r in results if r['scene'] == name]
        merged = {r['face'] for r in rs if r['status'] == 'merged'}
        scenes.append({'scene': name, 'complete_six_faces': merged == set(FACES), 'merged_faces': sorted(merged)})
    report = {'scenes': scenes, 'faces': results, 'download_errors': errors, 'skipped_scenes': skipped}
    (args.out / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'Merged {sum(r["status"] == "merged" for r in results)}/{len(results)} faces. Report: {args.out / "report.json"}')
    return 0 if all(s['complete_six_faces'] for s in scenes) and not errors and not skipped else 2


if __name__ == '__main__':
    sys.exit(main())

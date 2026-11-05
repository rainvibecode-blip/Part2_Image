#!/usr/bin/env python3
"""이미지 대시보드 서버. 파이썬 기본 기능만 쓴다.

  python3 serve.py [포트]                  서버 켜기 (기본 8782)
  python3 serve.py log <id> "바꾼 내용"     다시 만든 뒤 이력에 남기기
"""
import json, mimetypes, os, subprocess, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, 'board', 'state.json')
SETS = os.path.join(HERE, 'sets')
BRAND = os.path.join(HERE, 'brand_assets')
BRAND_CATS = ['logo', 'product', 'etc']
STAGES = ['ref', 'prompt', 'make', 'check', 'review']
LOCK = threading.Lock()
EMPTY_IMG = b'<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440" viewBox="0 0 1080 1440"/>'   # 빈 칸. 바탕색은 화면이 정한다


def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def empty_item(n):
    """자리 표시 칸. 내용은 없고 화면 틀만 보여 준다. 진짜 생성물이 생기면 지운다."""
    sid = f'{n:02d}_empty'
    return {'id': sid, 'placeholder': True, 'kicker': '분류', 'title': ['생성물 제목'], 'stage': 'ref', 'done': {},
            'check': None, 'review': '', 'pick': {},
            'out': [f'sets/{sid}/out/{i:02d}_image.png' for i in range(1, 5)],
            'cands': [f'sets/{sid}/cands/c{i:02d}.jpg' for i in range(1, 7)]}


def load():
    if not os.path.exists(STATE):
        save({'stages': STAGES, 'sets': [empty_item(i) for i in range(1, 7)], 'human': {}, 'make': []})
    with open(STATE, encoding='utf-8') as f:
        return json.load(f)


def save(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE)


def mtimes(st):
    mt = {}
    for s in st.get('sets', []):
        for f in s.get('out', []) + s.get('cands', []):
            p = os.path.join(HERE, f)
            if os.path.exists(p):
                mt[f] = int(os.path.getmtime(p) * 1000)
    return mt


def version(st=None):
    st = st or load()
    return f'{int(os.path.getmtime(STATE) * 1000)}-{sum(mtimes(st).values())}'


def add_log(cur, what, memo='', by='나'):
    cur.setdefault('log', []).append({'at': now(), 'what': what, 'by': by, 'memo': memo})


def save_human(p):
    """화면 버튼 결과를 human[id]에 쓴다. log는 지우지 않고 쌓는다."""
    with LOCK:
        st = load()
        sid = p.get('set')
        if sid not in {s['id'] for s in st['sets']}:
            raise ValueError('없는 생성물')
        cur = st['human'].setdefault(sid, {})
        if p.get('seen'):
            cur['seen'] = now()
        if 'verdict' in p:
            v = p['verdict']
            if v not in ('ok', 'redo', ''):
                raise ValueError('verdict는 ok · redo · 빈 값')
            again = v == 'redo' and cur.get('verdict') == 'redo'   # 다시 만든 뒤 또 수정 요청
            if again:
                cur['memo'] = ''
            if cur.get('verdict', '') != v or again:
                cur['verdict'], cur['verdict_at'] = v, now()
                add_log(cur, {'ok': '올려도 됨', 'redo': '수정 요청', '': '표시 지움'}[v], cur.get('memo', '') if v == 'redo' else '')
        if 'memo' in p:
            cur['memo'] = str(p['memo'])[:2000]
            log = cur.get('log') or []
            if cur.get('verdict') == 'redo' and log and log[-1]['what'] == '수정 요청':
                log[-1]['memo'] = cur['memo']   # 수정 요청을 누른 뒤 적는 메모는 그 요청의 메모다
        if set(p) - {'set', 'seen'}:
            cur['at'] = now()
        save(st)
        return cur


def save_make(d):
    topic = str(d.get('topic', '')).strip()[:80]
    if not topic:
        raise ValueError('만들 것을 적어 주세요')
    n = int(d.get('pages') or 4)
    if not 1 <= n <= 10:
        raise ValueError('장 수는 1~10')
    rec = {'mid': time.strftime('m%Y%m%d%H%M%S'), 'topic': topic, 'person': str(d.get('person', '')).strip()[:60],
           'keyword': str(d.get('keyword', '')).strip()[:300], 'pages': n, 'memo': str(d.get('memo', '')).strip()[:1000],
           'state': 'wait', 'step': '', 'why': '', 'set': '', 'at': now()}
    with LOCK:
        st = load()
        st['make'].append(rec)
        save(st)
    return rec


def brand_files():
    out = {}
    for c in BRAND_CATS:
        d = os.path.join(BRAND, c)
        names = sorted(n for n in os.listdir(d) if n.lower().endswith('.png')) if os.path.isdir(d) else []
        out[c] = [{'f': f'{c}/{n}', 'name': n[:-4], 't': int(os.path.getmtime(os.path.join(d, n)))} for n in names]
    return out


def reveal(folder):
    if sys.platform == 'win32':
        os.startfile(folder)
    else:
        subprocess.run(['open' if sys.platform == 'darwin' else 'xdg-open', folder], check=False)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype, cache='no-cache'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(200, json.dumps(obj, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ('/', '/index.html'):
                with open(os.path.join(HERE, 'hub.html'), 'rb') as f:
                    return self._send(200, f.read(), 'text/html; charset=utf-8')
            if u.path == '/api/state':
                st = load()
                st['version'], st['mt'] = version(st), mtimes(st)
                return self._json(st)
            if u.path == '/api/version':
                return self._send(200, version().encode(), 'text/plain')
            if u.path == '/api/brands':
                return self._json({'root': 'brand_assets', 'cats': BRAND_CATS, 'files': brand_files()})
            if u.path in ('/img', '/brand'):
                base = SETS if u.path == '/img' else BRAND
                rel = q.get('f', [''])[0]
                p = os.path.realpath(os.path.join(HERE if u.path == '/img' else BRAND, rel))
                if p.startswith(os.path.realpath(base) + os.sep) and os.path.isfile(p):
                    with open(p, 'rb') as f:
                        return self._send(200, f.read(), mimetypes.guess_type(p)[0] or 'application/octet-stream')
                return self._send(200, EMPTY_IMG, 'image/svg+xml')
            return self._send(404, b'not found', 'text/plain')
        except Exception as e:
            return self._send(500, f'서버 오류: {type(e).__name__}: {e}'.encode(), 'text/plain; charset=utf-8')

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            d = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
            if path == '/api/human':
                return self._json({'ok': True, 'human': save_human(d), 'version': version()})
            if path == '/api/make':
                return self._json({'ok': True, 'make': save_make(d), 'version': version()})
            if path == '/api/reveal':
                folder = os.path.join(SETS, os.path.basename(str(d.get('set'))), 'out')
                found = os.path.isdir(folder)
                if found:
                    reveal(folder)
                return self._json({'ok': found})
            return self._send(404, b'not found', 'text/plain')
        except Exception as e:
            return self._send(400, f'{type(e).__name__}: {e}'.encode(), 'text/plain; charset=utf-8')


def main():
    a = sys.argv[1:]
    if a and a[0] == 'log':
        if len(a) < 3:
            sys.exit(__doc__)
        st = load()
        if a[1] not in {s['id'] for s in st['sets']}:
            sys.exit(f'없는 생성물: {a[1]}')
        add_log(st['human'].setdefault(a[1], {}), '다시 만듦', ' '.join(a[2:]), 'Claude')
        save(st)
        return print(f'{a[1]}: 이력에 「다시 만듦」을 남겼습니다.')
    port = int(a[0]) if a else 8782
    load()
    print(f'http://localhost:{port}')
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


if __name__ == '__main__':
    main()

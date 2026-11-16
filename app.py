import os
import json
import time
import string
import random
import threading
from datetime import datetime
from functools import wraps

import requests
from flask import Flask, render_template, request, jsonify, session, make_response
from flask_socketio import SocketIO
import eventlet
eventlet.monkey_patch()

app = Flask(__name__)
app.secret_key = 'z3nith_K9x2Pv8Q4mR7wL3j_god'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

CREDS = {'flashy': 'Zidan9610'}
DISCORD_BATCH = 'https://discord.com/api/v9/unique-username/batch-available'
CHARS = string.ascii_lowercase + string.digits
CHARS_EXTRA = CHARS + '_'

_s = {
    'running': False,
    'valid': [],
    'invalid': [],
    'proxies': [],
    'proxy_idx': 0,
    'logs': [],
    'webhook': '',
    'token': '',
    'rate_limited': False,
}


def _log(msg, level='info'):
    entry = {'ts': datetime.now().strftime('%H:%M:%S'), 'msg': msg, 'level': level}
    _s['logs'].append(entry)
    if len(_s['logs']) > 1000:
        _s['logs'] = _s['logs'][-1000:]
    socketio.emit('log', entry)


def _gen(length):
    first = random.choice(CHARS)
    rest = ''.join(random.choices(CHARS_EXTRA, k=length - 1))
    return first + rest


def _parse_proxy(p):
    p = p.strip()
    if not p:
        return None
    if '://' in p:
        return {'http': p, 'https': p}
    parts = p.split(':')
    if len(parts) == 4:
        host, port, user, pw = parts
        u = f'http://{user}:{pw}@{host}:{port}'
        return {'http': u, 'https': u}
    return {'http': f'http://{p}', 'https': f'http://{p}'}


def _next_proxy():
    if not _s['proxies']:
        return None
    p = _s['proxies'][_s['proxy_idx'] % len(_s['proxies'])]
    _s['proxy_idx'] += 1
    return _parse_proxy(p)


def _check_batch(usernames):
    proxy = _next_proxy()
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Origin': 'https://discord.com',
        'Referer': 'https://discord.com/',
        'X-Discord-Locale': 'en-US',
        'X-Super-Properties': 'eyJvcyI6IldpbmRvd3MiLCJicm93c2VyIjoiQ2hyb21lIiwiZGV2aWNlIjoiIn0=',
    }
    if _s['token']:
        headers['Authorization'] = _s['token']

    try:
        resp = requests.post(
            DISCORD_BATCH,
            json={'usernames': usernames},
            headers=headers,
            proxies=proxy,
            timeout=15
        )
        if resp.status_code == 429:
            ra = 15
            try:
                ra = resp.json().get('retry_after', 15)
            except Exception:
                pass
            return None, f'rate_limited:{ra}'
        if resp.status_code in (401, 403):
            return None, f'auth_error:{resp.status_code}'
        if resp.status_code != 200:
            return None, f'http_{resp.status_code}'

        data = resp.json()
        result = {}

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    u = item.get('username', '')
                    result[u] = not item.get('taken', True)
        elif isinstance(data, dict):
            for u, val in data.items():
                if isinstance(val, bool):
                    result[u] = not val
                elif isinstance(val, dict):
                    result[u] = not val.get('taken', True)

        return result, None

    except requests.exceptions.ProxyError as e:
        return None, f'proxy_err:{str(e)[:40]}'
    except requests.exceptions.Timeout:
        return None, 'timeout'
    except Exception as e:
        return None, f'err:{str(e)[:40]}'


def _webhook(username, length):
    if not _s['webhook']:
        return
    try:
        payload = {
            "embeds": [{
                "title": "🎯  Z3NITH SNIPER GOD — HIT",
                "description": "**Available username found!**",
                "color": 0x00ff88,
                "fields": [
                    {"name": "⚡ Username", "value": f"```{username}```", "inline": True},
                    {"name": "📏 Type", "value": f"`{length} Character`", "inline": True},
                    {"name": "✅ Status", "value": "`VALID / AVAILABLE`", "inline": True},
                ],
                "footer": {"text": f"Z3NITH SNIPER GOD  •  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"},
                "thumbnail": {"url": "https://cdn3.emoji.gg/emojis/2651_target.png"}
            }]
        }
        requests.post(_s['webhook'], json=payload, timeout=5)
    except Exception as e:
        _log(f'Webhook error: {e}', 'error')


def _sniper(usernames):
    has_px = bool(_s['proxies'])
    batch = 10 if has_px else 3
    delay = 0.4 if has_px else 7.0
    total = len(usernames)
    checked = 0

    _log(f'🚀 Sniper fired — {total} targets | batch={batch} | proxies={"✓" if has_px else "✗"}')

    i = 0
    while i < len(usernames) and _s['running']:
        cur = usernames[i:i + batch]
        _log(f'Checking [{i+1}–{min(i+batch, total)}]  {" ".join(cur[:3])}{"…" if len(cur) > 3 else ""}')

        result, err = _check_batch(cur)

        if err:
            if err.startswith('rate_limited'):
                secs = float(err.split(':')[1]) if ':' in err else 15.0
                _log(f'⚠️  Rate limited — pause {secs:.0f}s', 'warn')
                _s['rate_limited'] = True
                socketio.emit('rate_limit', {'s': secs})
                time.sleep(2 if has_px else secs)
                _s['rate_limited'] = False
                socketio.emit('rate_limit_clear', {})
                continue

            elif err.startswith('auth_error'):
                _log('❌  Auth error — add Discord token in Settings', 'error')
                socketio.emit('auth_error', {})
                _s['running'] = False
                break
            else:
                _log(f'⚡ {err} — skip', 'warn')

        if result:
            for u, avail in result.items():
                if avail:
                    _s['valid'].append(u)
                    _log(f'✅  VALID  {u}  ({len(u)}c)', 'success')
                    _webhook(u, len(u))
                    socketio.emit('new_valid', {'u': u, 'l': len(u)})
                else:
                    _s['invalid'].append(u)
                    socketio.emit('new_invalid', {'u': u})

        i += batch
        checked += len(cur)
        socketio.emit('progress', {
            'checked': checked, 'total': total,
            'valid': len(_s['valid']), 'invalid': len(_s['invalid']),
            'pct': int(checked / total * 100)
        })
        time.sleep(delay + random.uniform(0, 0.3))

    _s['running'] = False
    _log(f'🏁  Done — Valid: {len(_s["valid"])}  Invalid: {len(_s["invalid"])}', 'success')
    socketio.emit('done', {'valid': len(_s['valid']), 'invalid': len(_s['invalid'])})


# ── auth decorator ────────────────────────────────────────────────────────────

def auth(f):
    @wraps(f)
    def w(*a, **kw):
        if not session.get('ok'):
            return jsonify({'error': 'unauthorized'}), 401
        return f(*a, **kw)
    return w


# ── routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/login', methods=['POST'])
def login():
    d = request.json or {}
    if CREDS.get(d.get('u')) == d.get('p'):
        session['ok'] = True
        return jsonify({'ok': True})
    return jsonify({'ok': False}), 401


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})


@app.route('/api/status')
@auth
def status():
    return jsonify({
        'running': _s['running'],
        'valid': len(_s['valid']),
        'invalid': len(_s['invalid']),
        'proxies': len(_s['proxies']),
        'webhook': bool(_s['webhook']),
        'token': bool(_s['token']),
    })


@app.route('/api/settings', methods=['GET', 'POST'])
@auth
def settings():
    if request.method == 'POST':
        d = request.json or {}
        if 'webhook' in d:
            _s['webhook'] = d['webhook']
        if 'token' in d:
            _s['token'] = d['token']
        _log('⚙️  Settings saved')
        return jsonify({'ok': True})
    return jsonify({
        'webhook': _s['webhook'],
        'token_set': bool(_s['token'])
    })


@app.route('/api/proxies', methods=['GET', 'POST', 'DELETE'])
@auth
def proxies():
    if request.method == 'GET':
        return jsonify({'proxies': _s['proxies'], 'count': len(_s['proxies'])})

    if request.method == 'POST':
        d = request.json or {}
        if 'bulk' in d:
            lines = [l.strip() for l in d['bulk'].split('\n') if l.strip()]
            before = len(_s['proxies'])
            existing = set(_s['proxies'])
            for l in lines:
                if l not in existing:
                    _s['proxies'].append(l)
                    existing.add(l)
            added = len(_s['proxies']) - before
            _log(f'Added {added} proxies → total {len(_s["proxies"])}')
            return jsonify({'ok': True, 'added': added, 'total': len(_s['proxies'])})
        elif 'proxy' in d:
            p = d['proxy'].strip()
            if p and p not in _s['proxies']:
                _s['proxies'].append(p)
            return jsonify({'ok': True, 'total': len(_s['proxies'])})

    if request.method == 'DELETE':
        d = request.json or {}
        if d.get('all'):
            _s['proxies'] = []
            _s['proxy_idx'] = 0
        elif 'idx' in d:
            idx = int(d['idx'])
            if 0 <= idx < len(_s['proxies']):
                _s['proxies'].pop(idx)
        return jsonify({'ok': True, 'total': len(_s['proxies'])})


@app.route('/api/snipe/start', methods=['POST'])
@auth
def snipe_start():
    if _s['running']:
        return jsonify({'error': 'Already running'}), 400
    d = request.json or {}
    count = max(1, min(60, int(d.get('count', 20))))
    lengths = d.get('lengths', [4, 5])
    if not lengths:
        lengths = [4, 5]

    usernames = []
    seen = set()
    per = count // len(lengths)
    extra = count % len(lengths)

    for i, length in enumerate(lengths):
        n = per + (1 if i < extra else 0)
        added, tries = 0, 0
        while added < n and tries < n * 15:
            u = _gen(length)
            if u not in seen:
                usernames.append(u)
                seen.add(u)
                added += 1
            tries += 1

    random.shuffle(usernames)
    _s['running'] = True
    threading.Thread(target=_sniper, args=(usernames,), daemon=True).start()
    return jsonify({'ok': True, 'count': len(usernames)})


@app.route('/api/snipe/stop', methods=['POST'])
@auth
def snipe_stop():
    _s['running'] = False
    _log('⛔  Sniper stopped', 'warn')
    return jsonify({'ok': True})


@app.route('/api/check', methods=['POST'])
@auth
def check_one():
    d = request.json or {}
    u = d.get('username', '').strip().lower()
    if not u:
        return jsonify({'error': 'No username'}), 400
    result, err = _check_batch([u])
    if err:
        return jsonify({'error': err}), 500
    avail = result.get(u, False) if result else False
    return jsonify({'username': u, 'available': avail})


@app.route('/api/usernames')
@auth
def usernames():
    return jsonify({'valid': _s['valid'], 'invalid': _s['invalid']})


@app.route('/api/usernames/delete', methods=['POST'])
@auth
def del_usernames():
    d = request.json or {}
    action = d.get('action', '')
    u = d.get('username', '')
    if action == 'valid_all':
        _s['valid'] = []
    elif action == 'invalid_all':
        _s['invalid'] = []
    elif action == 'valid_one' and u:
        _s['valid'] = [x for x in _s['valid'] if x != u]
    elif action == 'invalid_one' and u:
        _s['invalid'] = [x for x in _s['invalid'] if x != u]
    return jsonify({'ok': True})


@app.route('/api/export')
@auth
def export_txt():
    content = '\n'.join(_s['valid'])
    r = make_response(content)
    r.headers['Content-Type'] = 'text/plain'
    r.headers['Content-Disposition'] = 'attachment; filename="z3nith_valid.txt"'
    return r


@app.route('/api/logs')
@auth
def get_logs():
    return jsonify({'logs': _s['logs'][-300:]})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)

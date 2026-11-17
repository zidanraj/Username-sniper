import os, io, time, random, string, threading, concurrent.futures
from datetime import datetime
from functools import wraps
import requests
from flask import Flask, render_template, request, jsonify, session, send_file

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'fallback_change_this')

ADMIN_USER = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'changeme')

lock = threading.Lock()
_pid = [0]
_uid = [0]

state = {
    'proxies': [], 'tokens': [], 'results': [], 'logs': [],
    'running': False, 'stop': False,
    'stats': {'gen': 0, 'checked': 0, 'valid': 0, 'invalid': 0, 'rl': 0, 'err': 0},
    'config': {'lengths': [4], 'amount': 60},
    'webhook': '',
}

# ── EXACT SAME HEADERS AS WORKING BOT ─────────────────────────────────────────
DISC_HDR = {
    'Content-Type': 'application/json',
    'User-Agent': (
        'Mozilla/5.0 (Linux; Android 14; SM-A146B) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36'
    ),
}

# ── WORKING ENDPOINT (unauthed — no token needed) ─────────────────────────────
DISC_URL = 'https://discord.com/api/v9/unique-username/username-attempt-unauthed'

VALID_CHARS = string.ascii_lowercase + string.digits
VOWELS      = 'aeiou'
CONSONANTS  = 'bcdfghjklmnpqrstvwxyz'

# ── HELPERS ───────────────────────────────────────────────────────────────────
def addlog(lvl, msg):
    with lock:
        state['logs'].append({
            't': datetime.utcnow().strftime('%H:%M:%S'),
            'l': lvl, 'm': msg
        })
        if len(state['logs']) > 1000:
            state['logs'] = state['logs'][-500:]

def authed(f):
    @wraps(f)
    def d(*a, **kw):
        if not session.get('ok'):
            return jsonify({'error': 'unauth'}), 401
        return f(*a, **kw)
    return d

def gen_name(lengths):
    l = random.choice(lengths)
    # 3 styles just like the working bot
    style = random.choice(['pronounceable', 'alpha', 'alnum'])
    if style == 'pronounceable':
        return ''.join(
            random.choice(CONSONANTS if i % 2 == 0 else VOWELS)
            for i in range(l)
        )
    if style == 'alpha':
        return ''.join(random.choices(string.ascii_lowercase, k=l))
    return ''.join(random.choices(VALID_CHARS, k=l))

def disc_check(username, proxy=None):
    """
    POST to unauthed endpoint — 100% works without token.
    Returns: 'valid' | 'invalid' | 'rl' | 'proxy_dead' | 'err'
    """
    px = {'http': proxy, 'https': proxy} if proxy else None
    try:
        r = requests.post(
            DISC_URL,
            json={'username': username},
            headers=DISC_HDR,
            proxies=px,
            timeout=14,
        )
        if r.status_code == 200:
            data = r.json()
            # taken=False means AVAILABLE
            return 'valid' if data.get('taken') is False else 'invalid'
        if r.status_code == 429:
            return 'rl'
        return 'err'
    except requests.exceptions.ProxyError:
        return 'proxy_dead'
    except Exception:
        return 'err'

def next_proxy():
    with lock:
        ok = [p for p in state['proxies'] if p['s'] == 'ok']
        return random.choice(ok)['ep'] if ok else None

def fire_webhook(username, length):
    url = state['webhook']
    if not url:
        return
    try:
        requests.post(url, json={
            'username': 'Z3NITH SNIPER GOD',
            'embeds': [{
                'title': '🎯 Z3NITH SNIPER GOD',
                'description': '**Rare Username Discovered!**',
                'color': 0x00ff88,
                'fields': [
                    {'name': '👤 Username', 'value': f'`{username}`',  'inline': True},
                    {'name': '✅ Status',   'value': '**VALID**',      'inline': True},
                    {'name': '🔢 Type',     'value': f'{length} Characters', 'inline': True},
                    {'name': '⏰ Found At', 'value': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC'), 'inline': False},
                ],
                'footer': {'text': 'Z3NITH SNIPER GOD • Stay Grinding 🔥'},
                'timestamp': datetime.utcnow().isoformat(),
            }]
        }, timeout=8)
    except Exception:
        pass

# ── WORKER ────────────────────────────────────────────────────────────────────
def worker():
    addlog('info', '🚀 Sniper started')
    with lock:
        cfg = {**state['config']}
        state['stats'] = {
            'gen': 0, 'checked': 0, 'valid': 0,
            'invalid': 0, 'rl': 0, 'err': 0
        }

    lengths = cfg['lengths']
    amount  = cfg['amount']

    # generate unique pool
    seen = set()
    pool = []
    while len(pool) < amount:
        u = gen_name(lengths)
        if u not in seen:
            seen.add(u)
            pool.append(u)

    with lock:
        state['stats']['gen'] = len(pool)
    addlog('info', f'📋 Generated {len(pool)} unique usernames')

    idx = 0
    while idx < len(pool):
        with lock:
            if state['stop']:
                addlog('warn', '⛔ Stopped by user')
                break
            has_px = any(p['s'] == 'ok' for p in state['proxies'])

        # proxies = 10 at a time, no proxy = 2 at a time (like working bot)
        batch_sz = 10 if has_px else 2
        batch    = pool[idx:idx + batch_sz]
        idx     += batch_sz

        def check_one(u):
            px  = next_proxy()
            res = disc_check(u, proxy=px)
            # double-check if valid (same as working bot)
            if res == 'valid':
                res2 = disc_check(u, proxy=next_proxy())
                if res2 != 'valid':
                    res = 'invalid'
            return u, res, len(u), px

        with concurrent.futures.ThreadPoolExecutor(max_workers=batch_sz) as ex:
            futs = {ex.submit(check_one, u): u for u in batch}
            for fut in concurrent.futures.as_completed(futs):
                orig = futs[fut]
                try:
                    u, res, l, px = fut.result()
                except Exception:
                    with lock:
                        state['stats']['err'] += 1
                    addlog('error', f'💀 Thread err: {orig}')
                    continue

                with lock:
                    state['stats']['checked'] += 1

                if res == 'valid':
                    with lock:
                        state['stats']['valid'] += 1
                        uid = _uid[0]; _uid[0] += 1
                        state['results'].append({
                            'id': uid, 'u': u, 'l': l,
                            't': datetime.utcnow().strftime('%H:%M:%S')
                        })
                    addlog('success', f'✅ VALID → {u} ({l} chars)')
                    fire_webhook(u, l)

                elif res == 'invalid':
                    with lock:
                        state['stats']['invalid'] += 1
                    addlog('info', f'❌ Taken: {u}')

                elif res == 'rl':
                    with lock:
                        state['stats']['rl'] += 1
                    addlog('warn', f'⚠️ Rate limited: {u} — retrying')
                    pool.append(u)

                elif res == 'proxy_dead':
                    if px:
                        with lock:
                            for p in state['proxies']:
                                if p['ep'] == px:
                                    p['s'] = 'dead'
                    pool.append(u)
                    addlog('error', f'💀 Proxy dead: {px}')

                else:
                    with lock:
                        state['stats']['err'] += 1
                    addlog('error', f'🔴 Error: {u}')

        with lock:
            if state['stop']:
                break
            has_px_now = any(p['s'] == 'ok' for p in state['proxies'])

        # delay between batches — like working bot (0.6s no proxy, 0.05s with proxy)
        delay = 0.05 if has_px_now else 0.6
        time.sleep(delay)

    with lock:
        state['running'] = False
        state['stop']    = False
        v = state['stats']['valid']
        c = state['stats']['checked']
    addlog('success', f'🏁 Done! {c} checked → {v} valid found')

# ── ROUTES ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html', page='app' if session.get('ok') else 'login')

@app.route('/api/login', methods=['POST'])
def api_login():
    d = request.json or {}
    if d.get('u') == ADMIN_USER and d.get('p') == ADMIN_PASS:
        session['ok'] = True
        return jsonify({'ok': True})
    return jsonify({'ok': False}), 401

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/start', methods=['POST'])
@authed
def api_start():
    with lock:
        if state['running']:
            return jsonify({'error': 'already running'}), 400
        d = request.json or {}
        state['config']['lengths'] = d.get('lengths', [4])
        state['config']['amount']  = max(1, min(60, int(d.get('amount', 60))))
        state['stop']    = False
        state['running'] = True
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'ok': True})

@app.route('/api/stop', methods=['POST'])
@authed
def api_stop():
    with lock:
        state['stop'] = True
    return jsonify({'ok': True})

@app.route('/api/status')
@authed
def api_status():
    with lock:
        return jsonify({'running': state['running'], 'stats': {**state['stats']}})

@app.route('/api/check-single', methods=['POST'])
@authed
def api_check():
    u = (request.json or {}).get('username', '').strip().lower()
    if not u or len(u) < 2 or len(u) > 32:
        return jsonify({'r': 'invalid_format'})
    r = disc_check(u, proxy=next_proxy())
    addlog('info', f'🔍 Manual: {u} → {r}')
    return jsonify({'r': r, 'u': u})

# proxies
@app.route('/api/proxies')
@authed
def api_proxies_get():
    with lock:
        return jsonify({'proxies': list(state['proxies'])})

@app.route('/api/proxies/add', methods=['POST'])
@authed
def api_proxies_add():
    ep = (request.json or {}).get('ep', '').strip()
    if not ep:
        return jsonify({'error': 'empty'}), 400
    if not any(ep.startswith(x) for x in ('socks5://', 'http://', 'https://')):
        ep = 'http://' + ep
    with lock:
        pid = _pid[0]; _pid[0] += 1
        state['proxies'].append({'id': pid, 'ep': ep, 's': 'untested'})
    return jsonify({'ok': True, 'id': pid})

@app.route('/api/proxies/bulk', methods=['POST'])
@authed
def api_proxies_bulk():
    lines = (request.json or {}).get('data', '').strip().split('\n')
    n = 0
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if not any(ln.startswith(x) for x in ('socks5://', 'http://', 'https://')):
            ln = 'http://' + ln
        with lock:
            pid = _pid[0]; _pid[0] += 1
            state['proxies'].append({'id': pid, 'ep': ln, 's': 'untested'})
        n += 1
    addlog('info', f'📥 Imported {n} proxies')
    return jsonify({'ok': True, 'n': n})

@app.route('/api/proxies/test', methods=['POST'])
@authed
def api_proxies_test():
    def do():
        with lock:
            plist = list(state['proxies'])
        for p in plist:
            try:
                r = requests.post(
                    DISC_URL,
                    json={'username': 'testuser123'},
                    headers=DISC_HDR,
                    proxies={'http': p['ep'], 'https': p['ep']},
                    timeout=10
                )
                s = 'ok' if r.status_code in (200, 429) else 'dead'
            except:
                s = 'dead'
            with lock:
                for px in state['proxies']:
                    if px['id'] == p['id']:
                        px['s'] = s
            addlog('info', f'Proxy {p["ep"][:35]}... → {s}')
    threading.Thread(target=do, daemon=True).start()
    return jsonify({'ok': True})

@app.route('/api/proxies/del', methods=['POST'])
@authed
def api_proxies_del():
    pid = (request.json or {}).get('id')
    with lock:
        state['proxies'] = [p for p in state['proxies'] if p['id'] != pid]
    return jsonify({'ok': True})

@app.route('/api/proxies/del-dead', methods=['POST'])
@authed
def api_proxies_del_dead():
    with lock:
        b = len(state['proxies'])
        state['proxies'] = [p for p in state['proxies'] if p['s'] != 'dead']
        a = len(state['proxies'])
    return jsonify({'ok': True, 'deleted': b - a})

@app.route('/api/proxies/clear', methods=['POST'])
@authed
def api_proxies_clear():
    with lock:
        state['proxies'] = []
    return jsonify({'ok': True})

# tokens (kept for compatibility but not needed anymore)
@app.route('/api/tokens')
@authed
def api_tokens_get():
    with lock:
        return jsonify({'tokens': [t[:22] + '...' for t in state['tokens']], 'count': len(state['tokens'])})

@app.route('/api/tokens/add', methods=['POST'])
@authed
def api_tokens_add():
    t = (request.json or {}).get('token', '').strip()
    if not t:
        return jsonify({'error': 'empty'}), 400
    with lock:
        if t not in state['tokens']:
            state['tokens'].append(t)
    return jsonify({'ok': True})

@app.route('/api/tokens/bulk', methods=['POST'])
@authed
def api_tokens_bulk():
    lines = (request.json or {}).get('data', '').strip().split('\n')
    n = 0
    with lock:
        for ln in lines:
            ln = ln.strip()
            if ln and ln not in state['tokens']:
                state['tokens'].append(ln)
                n += 1
    return jsonify({'ok': True, 'n': n})

@app.route('/api/tokens/del', methods=['POST'])
@authed
def api_tokens_del():
    idx = (request.json or {}).get('idx')
    with lock:
        if isinstance(idx, int) and 0 <= idx < len(state['tokens']):
            state['tokens'].pop(idx)
    return jsonify({'ok': True})

@app.route('/api/tokens/clear', methods=['POST'])
@authed
def api_tokens_clear():
    with lock:
        state['tokens'] = []
    return jsonify({'ok': True})

# results
@app.route('/api/results')
@authed
def api_results():
    lf = request.args.get('l')
    with lock:
        r = list(state['results'])
    if lf:
        r = [x for x in r if str(x['l']) == lf]
    return jsonify({'results': r})

@app.route('/api/results/del', methods=['POST'])
@authed
def api_results_del():
    uid = (request.json or {}).get('id')
    with lock:
        state['results'] = [r for r in state['results'] if r['id'] != uid]
    return jsonify({'ok': True})

@app.route('/api/results/del-all', methods=['POST'])
@authed
def api_results_del_all():
    with lock:
        state['results'] = []
    return jsonify({'ok': True})

@app.route('/api/results/export')
@authed
def api_results_export():
    with lock:
        lines = [r['u'] for r in state['results']]
    buf = io.BytesIO('\n'.join(lines).encode())
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name='z3nith_valid.txt', mimetype='text/plain')

# webhook
@app.route('/api/webhook', methods=['GET', 'POST'])
@authed
def api_webhook():
    if request.method == 'POST':
        with lock:
            state['webhook'] = (request.json or {}).get('url', '').strip()
        return jsonify({'ok': True})
    with lock:
        return jsonify({'url': state['webhook']})

@app.route('/api/webhook/test', methods=['POST'])
@authed
def api_webhook_test():
    fire_webhook('z3nith_test', 4)
    return jsonify({'ok': True})

# logs
@app.route('/api/logs')
@authed
def api_logs():
    with lock:
        return jsonify({'logs': state['logs'][-200:]})

@app.route('/api/logs/clear', methods=['POST'])
@authed
def api_logs_clear():
    with lock:
        state['logs'] = []
    return jsonify({'ok': True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)

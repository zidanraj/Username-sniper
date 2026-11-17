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
    'live_running': False, 'live_stop': False,
    'stats': {'gen': 0, 'checked': 0, 'valid': 0, 'invalid': 0, 'rl': 0, 'err': 0},
    'config': {'lengths': [4], 'amount': 60},
    'live_config': {'lengths': [4]},
    'webhook': '',
}

DISC_HDR = {
    'Content-Type': 'application/json',
    'User-Agent': (
        'Mozilla/5.0 (Linux; Android 14; SM-A146B) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36'
    ),
}
DISC_URL = 'https://discord.com/api/v9/unique-username/username-attempt-unauthed'

VOWELS     = 'aeiou'
CONSONANTS = 'bcdfghjklmnpqrstvwxyz'

# ── HELPERS ───────────────────────────────────────────────────────────────────
def addlog(lvl, msg):
    with lock:
        state['logs'].append({'t': datetime.utcnow().strftime('%H:%M:%S'), 'l': lvl, 'm': msg})
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
    if l == 4:
        # 4-char: number-heavy combos are more available
        # pure alpha 4-char are almost all taken
        style = random.choice(['num_mix', 'num_heavy', 'alnum_rand', 'underscore_mid'])
        if style == 'num_mix':
            # 2 letters + 2 numbers shuffled
            chars = random.sample(string.ascii_lowercase, 2) + random.sample(string.digits, 2)
            random.shuffle(chars)
            return ''.join(chars)
        if style == 'num_heavy':
            # 1 letter + 3 numbers or 3 letters + 1 number
            if random.random() > 0.5:
                chars = random.sample(string.ascii_lowercase, 1) + random.sample(string.digits, 3)
            else:
                chars = random.sample(string.ascii_lowercase, 3) + random.sample(string.digits, 1)
            random.shuffle(chars)
            return ''.join(chars)
        if style == 'alnum_rand':
            return ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        if style == 'underscore_mid':
            # x_x2 or x2_x pattern — mid underscore
            a = random.choice(string.ascii_lowercase + string.digits)
            b = random.choice(string.ascii_lowercase + string.digits)
            c = random.choice(string.ascii_lowercase + string.digits)
            return f'{a}_{b}{c}'
    elif l == 5:
        style = random.choice(['pronounceable', 'alpha', 'alnum'])
        if style == 'pronounceable':
            return ''.join(random.choice(CONSONANTS if i % 2 == 0 else VOWELS) for i in range(5))
        if style == 'alpha':
            return ''.join(random.choices(string.ascii_lowercase, k=5))
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=5))
    else:
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=l))

def disc_check(username, proxy=None):
    """Returns (status, wait_seconds)"""
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
            taken = r.json().get('taken', True)
            return ('valid' if taken is False else 'invalid'), 0
        if r.status_code == 429:
            try:
                wait = float(r.json().get('retry_after', 12))
                wait = min(wait, 45)
            except Exception:
                wait = 12
            return 'rl', wait
        return 'err', 0
    except requests.exceptions.ProxyError:
        return 'proxy_dead', 0
    except Exception:
        return 'err', 0

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

def save_result(u, l):
    with lock:
        state['stats']['valid'] += 1
        uid = _uid[0]; _uid[0] += 1
        state['results'].append({
            'id': uid, 'u': u, 'l': l,
            't': datetime.utcnow().strftime('%H:%M:%S')
        })
    addlog('success', f'✅ VALID → {u} ({l} chars)')
    fire_webhook(u, l)

def sleep_interruptible(seconds, stop_key='stop'):
    """Sleep but wake up early if stop flag set."""
    for _ in range(int(seconds * 10)):
        with lock:
            if state[stop_key]:
                return True  # interrupted
        time.sleep(0.1)
    return False

# ── REGULAR WORKER ────────────────────────────────────────────────────────────
def worker():
    addlog('info', '🚀 Sniper started')
    with lock:
        cfg = {**state['config']}
        state['stats'] = {'gen': 0, 'checked': 0, 'valid': 0, 'invalid': 0, 'rl': 0, 'err': 0}

    lengths = cfg['lengths']
    amount  = cfg['amount']

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
                addlog('warn', '⛔ Stopped')
                break
            has_px = any(p['s'] == 'ok' for p in state['proxies'])

        batch_sz = 10 if has_px else 2
        batch = pool[idx:idx + batch_sz]
        idx  += batch_sz

        def check_one(u):
            px = next_proxy()
            res, wait = disc_check(u, proxy=px)
            if res == 'valid':
                res2, _ = disc_check(u, proxy=next_proxy())
                if res2 != 'valid':
                    res = 'invalid'
            return u, res, wait, len(u), px

        with concurrent.futures.ThreadPoolExecutor(max_workers=batch_sz) as ex:
            futs = {ex.submit(check_one, u): u for u in batch}
            for fut in concurrent.futures.as_completed(futs):
                orig = futs[fut]
                try:
                    u, res, wait, l, px = fut.result()
                except Exception:
                    with lock:
                        state['stats']['err'] += 1
                    continue

                with lock:
                    state['stats']['checked'] += 1

                if res == 'valid':
                    save_result(u, l)

                elif res == 'invalid':
                    with lock:
                        state['stats']['invalid'] += 1

                elif res == 'rl':
                    with lock:
                        state['stats']['rl'] += 1
                    wait = max(wait, 12)
                    addlog('warn', f'⚠️ Rate limited — waiting {int(wait)}s')
                    # actually sleep — this is the fix
                    interrupted = sleep_interruptible(wait, 'stop')
                    pool.append(u)
                    if interrupted:
                        break

                elif res == 'proxy_dead':
                    if px:
                        with lock:
                            for p in state['proxies']:
                                if p['ep'] == px:
                                    p['s'] = 'dead'
                    pool.append(u)

                else:
                    with lock:
                        state['stats']['err'] += 1

        with lock:
            if state['stop']:
                break
            has_px_now = any(p['s'] == 'ok' for p in state['proxies'])

        time.sleep(0.05 if has_px_now else 0.6)

    with lock:
        state['running'] = False
        state['stop']    = False
        v = state['stats']['valid']
        c = state['stats']['checked']
    addlog('success', f'🏁 Done! {c} checked → {v} valid')

# ── LIVE WORKER ───────────────────────────────────────────────────────────────
def live_worker():
    addlog('info', '🔴 LIVE mode started — infinite snipe until you stop')
    with lock:
        lengths = list(state['live_config']['lengths'])

    seen = set()
    checked = 0
    rl_streak = 0

    while True:
        with lock:
            if state['live_stop']:
                addlog('warn', '⛔ LIVE stopped')
                state['live_running'] = False
                state['live_stop']    = False
                break

        u = gen_name(lengths)
        if u in seen:
            continue
        seen.add(u)

        # clear seen periodically to avoid memory bloat
        if len(seen) > 50000:
            seen = set(list(seen)[-10000:])

        px = next_proxy()
        res, wait = disc_check(u, proxy=px)
        checked += 1

        with lock:
            state['stats']['checked'] += 1

        if res == 'valid':
            # double confirm
            res2, _ = disc_check(u, proxy=next_proxy())
            if res2 == 'valid':
                save_result(u, len(u))
                rl_streak = 0

        elif res == 'invalid':
            with lock:
                state['stats']['invalid'] += 1
            rl_streak = 0
            with lock:
                has_px = any(p['s'] == 'ok' for p in state['proxies'])
            time.sleep(0.05 if has_px else 0.5)

        elif res == 'rl':
            with lock:
                state['stats']['rl'] += 1
            rl_streak += 1
            # progressive wait — longer if keeps hitting rl
            actual_wait = max(wait, 10 + min(rl_streak * 3, 25))
            addlog('warn', f'⚠️ Rate limited (x{rl_streak}) — waiting {int(actual_wait)}s')
            interrupted = sleep_interruptible(actual_wait, 'live_stop')
            if interrupted:
                with lock:
                    state['live_running'] = False
                    state['live_stop']    = False
                addlog('warn', '⛔ LIVE stopped during RL wait')
                break

        elif res == 'proxy_dead':
            if px:
                with lock:
                    for p in state['proxies']:
                        if p['ep'] == px:
                            p['s'] = 'dead'
            addlog('error', f'💀 Proxy dead: {px}')

        else:
            with lock:
                state['stats']['err'] += 1

    addlog('success', f'🏁 LIVE ended — {checked} checked, {state["stats"]["valid"]} valid total')

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
        if state['running'] or state['live_running']:
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

@app.route('/api/live/start', methods=['POST'])
@authed
def api_live_start():
    with lock:
        if state['running'] or state['live_running']:
            return jsonify({'error': 'already running'}), 400
        d = request.json or {}
        state['live_config']['lengths'] = d.get('lengths', [4])
        state['live_stop']    = False
        state['live_running'] = True
    threading.Thread(target=live_worker, daemon=True).start()
    return jsonify({'ok': True})

@app.route('/api/live/stop', methods=['POST'])
@authed
def api_live_stop():
    with lock:
        state['live_stop'] = True
    return jsonify({'ok': True})

@app.route('/api/status')
@authed
def api_status():
    with lock:
        return jsonify({
            'running':      state['running'],
            'live_running': state['live_running'],
            'stats':        {**state['stats']},
        })

@app.route('/api/check-single', methods=['POST'])
@authed
def api_check():
    u = (request.json or {}).get('username', '').strip().lower()
    if not u or len(u) < 2 or len(u) > 32:
        return jsonify({'r': 'invalid_format'})
    r, _ = disc_check(u, proxy=next_proxy())
    addlog('info', f'🔍 Manual: {u} → {r}')
    return jsonify({'r': r, 'u': u})

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

@app.route('/api/tokens')
@authed
def api_tokens_get():
    with lock:
        return jsonify({'tokens': [t[:22]+'...' for t in state['tokens']], 'count': len(state['tokens'])})

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

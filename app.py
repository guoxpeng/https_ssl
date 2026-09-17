"""https_ssl —— 自签证书管理系统（Flask + OpenSSL + nginx 单容器）。

职责划分：
  · 证书生命周期：创建 CA、签发/删除/下载证书、验证证书
  · 反向代理：生成 nginx 站点配置、启停服务、热加载

安全约定（改动前请先读这一段）：
  · 任何进入文件路径的字符串都必须先过 _valid_name()，包括签发入口。
  · 任何进入 nginx 配置或 OpenSSL 配置的字符串都必须先过对应的校验函数。
  · 任何拼进 HTML 响应的用户数据都必须过 escape()。
  · 落盘一律「先校验、后写入」，避免脏数据持久化。
"""
import datetime
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
import uuid
from functools import wraps
from http.server import BaseHTTPRequestHandler, HTTPServer

from flask import (Flask, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from markupsafe import escape
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CA_DIR = os.path.join(BASE_DIR, 'ca')
CERTS_DIR = os.path.join(BASE_DIR, 'certs')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')

OPENSSL_CMD = os.environ.get('QILIN_OPENSSL', '/usr/bin/openssl')

# nginx 由 entrypoint.sh 与 Flask 一起拉起，站点配置写在 PROXY_DIR 下。
PROXY_DIR = os.environ.get('QILIN_PROXY_DIR', os.path.join(BASE_DIR, 'proxy'))
PROXY_SITES_DIR = os.path.join(PROXY_DIR, 'sites')
PROXY_CERTS_DIR = os.path.join(PROXY_DIR, 'certs')
PROXY_LISTEN_HOST = os.environ.get('QILIN_PROXY_LISTEN_HOST', '')
PROXY_DATA_FILE = os.path.join(PROXY_DIR, 'proxy_data.json')

# 容器是否跑在 host 网络模式下（docker-compose.host.yml 会置 1）。
# 桥接模式下端口映射在容器创建时就固定了，用户必须先在 compose 的 ports 里
# 放行；host 模式下 nginx 监听的端口就是宿主机端口，填了即生效。
# 两种模式下界面要给的提示完全不同，所以这里读出来传给模板。
HOST_NETWORK = os.environ.get('QILIN_HOST_NETWORK') == '1'

# 面板监听端口。桥接模式下容器内固定 2002，由 compose 的端口映射对外暴露；
# host 模式下没有映射，Flask 直接绑宿主机端口，改端口只能靠这个变量。
try:
    PANEL_PORT = int(os.environ.get('QILIN_PORT') or 2002)
except ValueError:
    PANEL_PORT = 2002

CA_KEY = os.path.join(CA_DIR, 'qilin-ca.key')
CA_CRT = os.path.join(CA_DIR, 'qilin-ca.crt')
CA_INFO_FILE = os.path.join(CA_DIR, 'ca_info.json')

app.config.update(
    SECRET_KEY=os.environ.get('QILIN_SECRET_KEY') or os.urandom(24),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    # 面板经 HTTPS 反代暴露时置 QILIN_COOKIE_SECURE=1
    SESSION_COOKIE_SECURE=os.environ.get('QILIN_COOKIE_SECURE') == '1',
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    PROXY_DATA_FILE=PROXY_DATA_FILE,
)
if not os.environ.get('QILIN_SECRET_KEY'):
    print('[warn] 未设置 QILIN_SECRET_KEY，本次使用随机密钥；容器重启后所有登录会话失效。', flush=True)

for _d in (CA_DIR, CERTS_DIR, UPLOAD_DIR, PROXY_SITES_DIR, PROXY_CERTS_DIR):
    os.makedirs(_d, exist_ok=True)
if not os.path.exists(PROXY_DATA_FILE):
    with open(PROXY_DATA_FILE, 'w', encoding='utf-8') as _f:
        _f.write('[]')

# ---------------------------------------------------------------- 校验与工具 --

# 名称白名单：允许中英文、数字、下划线、连字符、点，作为单层路径组件使用。
# 不含 / \ : * ? " < > | 及控制字符，因此既不能穿越目录，也不能注入配置。
NAME_RE = re.compile(r'^[\w.-]{1,64}$')
# nginx server_name 允许通配前缀，但不允许分号/换行/大括号。
SERVER_NAME_RE = re.compile(r'^[A-Za-z0-9*_.-]{1,253}$')
# 上游地址：http(s) 开头的 URL，字符集排除引号、空格与换行。
UPSTREAM_RE = re.compile(r'^https?://[A-Za-z0-9._\-:\[\]/%?=&~+@]{1,512}$')
# 域名（含通配）：写入 SAN 前必须校验，否则换行可注入 OpenSSL 配置。
DOMAIN_RE = re.compile(r'^(?:\*\.)?[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
                       r'(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$')
# 证书目录名 'ca' 被 /download 用作根证书别名，不能作为证书名占用。
RESERVED_CERT_NAMES = {'ca'}
ALLOWED_UPLOAD_EXT = {'.crt', '.pem', '.cer', '.key'}


def _valid_name(name):
    """单层路径组件是否安全（用于证书名、服务名、文件名）。"""
    return bool(name) and name not in ('.', '..') and bool(NAME_RE.match(name))


def _valid_server_name(host):
    return bool(host) and bool(SERVER_NAME_RE.match(host))


def _valid_upstream(url):
    return bool(url) and bool(UPSTREAM_RE.match(url))


def _valid_domain(domain):
    return bool(domain) and len(domain) <= 253 and bool(DOMAIN_RE.match(domain))


def _valid_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _body():
    """读取请求体，同时兼容 JSON 与表单提交。

    历史上前端发 multipart、后端只读 request.json，导致接口恒失败；
    统一走这里后两种提交方式都能用。
    """
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict()


def _err(message, code=400):
    return jsonify({'success': False, 'message': message}), code


def _openssl(args, timeout=15):
    """执行 OpenSSL 命令，返回 (是否成功, stdout, stderr)。"""
    try:
        proc = subprocess.run(
            [OPENSSL_CMD, *args], capture_output=True, text=True,
            timeout=timeout, stdin=subprocess.DEVNULL,
        )
        return proc.returncode == 0, proc.stdout or '', proc.stderr or ''
    except FileNotFoundError:
        return False, '', f'未找到 OpenSSL: {OPENSSL_CMD}'
    except subprocess.TimeoutExpired:
        return False, '', 'OpenSSL 命令执行超时'


def _read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, payload):
    """原子写入 JSON，避免并发/中断留下半截文件。"""
    tmp = f'{path}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=4)
    os.replace(tmp, path)


def _ca_info():
    return _read_json(CA_INFO_FILE) or {}


def _pretty_date(text):
    """把 OpenSSL 的 notBefore/notAfter 原文转成 YYYY-MM-DD。

    面板上直接显示 `Feb 16 08:19:50 2036 GMT` 既长又难读，和 CA 那行的
    `2036-02-16` 也对不齐。解析不了就原样返回，不吞掉信息。
    """
    try:
        return datetime.datetime.strptime(text, '%b %d %H:%M:%S %Y %Z').strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        return text or ''


def _cert_info(cert_path):
    """解析证书的 subject / SAN / 有效期。

    OpenSSL 读不出来时返回 None，调用方据此区分「文件损坏」与「有效证书」。
    """
    if not os.path.isfile(cert_path):
        return None
    ok, out, _ = _openssl(['x509', '-in', cert_path, '-noout',
                           '-subject', '-dates', '-ext', 'subjectAltName'])
    if not ok:
        return None
    info = {'subject': '', 'cn': '', 'domains': [], 'ips': [],
            'not_before': '', 'not_after': '', 'not_after_ts': 0}
    # OpenSSL 3.x 把 SAN 里的 IP 打印成 "IP Address:"，旧版本是 "IP:"；
    # 不归一化的话整行都匹配不上，域名和 IP 会一起丢掉。
    out = re.sub(r'IP\s+Address:', 'IP:', out, flags=re.IGNORECASE)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('subject='):
            info['subject'] = line[len('subject='):].strip()
        elif line.startswith('notBefore='):
            info['not_before'] = line[len('notBefore='):].strip()
        elif line.startswith('notAfter='):
            info['not_after'] = line[len('notAfter='):].strip()
        elif line.upper().startswith('DNS:') or line.upper().startswith('IP:'):
            for part in line.split(','):
                part = part.strip()
                if part.upper().startswith('DNS:'):
                    info['domains'].append(part[4:])
                elif part.upper().startswith('IP:'):
                    info['ips'].append(part[3:])
    matched = re.search(r'CN\s*=\s*([^,]+)', info['subject'])
    if matched:
        info['cn'] = matched.group(1).strip()
    if info['not_after']:
        try:
            info['not_after_ts'] = datetime.datetime.strptime(
                info['not_after'], '%b %d %H:%M:%S %Y %Z').timestamp()
        except ValueError:
            pass
        # 时间戳已经算出来了，展示用的两个字段再转成人能读的日期
        info['not_after'] = _pretty_date(info['not_after'])
    info['not_before'] = _pretty_date(info['not_before'])
    return info


def _key_matches_cert(cert_path, key_path):
    """确认私钥与证书属于同一密钥对（pkey 同时支持 RSA / EC）。"""
    ok_c, cert_pub, _ = _openssl(['x509', '-in', cert_path, '-noout', '-pubkey'])
    ok_k, key_pub, _ = _openssl(['pkey', '-in', key_path, '-pubout'])
    return bool(ok_c and ok_k) and cert_pub.strip() == key_pub.strip()


def _cert_expiry(crt_file):
    info = _cert_info(crt_file)
    return (info or {}).get('not_after', '')


def _chains_to_ca(crt_file):
    """证书是否由本机 CA 签发。

    以前用 cert_name.startswith('qilin-') 判断，导致名字不带该前缀的自签证书
    被拿去比对系统信任库而必然报「链校验失败」。这里改为真正做一次链校验。
    """
    if not os.path.isfile(CA_CRT):
        return False
    ok, _, _ = _openssl(['verify', '-CAfile', CA_CRT, crt_file])
    return ok


def _dns_match(host, pattern):
    """域名匹配，支持 *.example.com 形式的通配。"""
    if pattern.startswith('*.'):
        return (host.count('.') == pattern.count('.')
                and host.endswith(pattern[1:]))
    return host == pattern


def _address_covered(address, info):
    """地址是否被证书的 SAN（或 CN）覆盖。地址为空时视为不校验。"""
    host = (address or '').strip().lower()
    if not host:
        return True
    if any(ip.lower() == host for ip in info.get('ips', [])):
        return True
    for name in list(info.get('domains', [])) + [info.get('cn', '')]:
        if name and _dns_match(host, name.lower()):
            return True
    return False


def _tls_handshake_check(crt_file, key_file, timeout=10, ca_file=None):
    """用真实 TLS 握手验证证书可用性。

    在回环地址临时起一个 HTTPS 服务端，客户端开启证书校验后连接：
    握手能完成且链能被信任，才说明证书真的可用。ca_file 给定时用它作为
    唯一信任锚（本机 CA 签发的证书），否则用系统信任库（自定义证书）。
    服务端在 finally 中关闭，失败也不会残留占用端口。
    """

    class _QuietHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'ok')

        def log_message(self, *args):
            pass

    try:
        httpd = HTTPServer(('127.0.0.1', 0), _QuietHandler)
    except OSError as e:
        return False, f'无法启动校验服务器：{e}'

    port = httpd.server_address[1]
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        ctx.load_cert_chain(certfile=crt_file, keyfile=key_file)
    except ssl.SSLError as e:
        httpd.server_close()
        return False, f'证书或私钥无法加载：{e}'
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        client_ctx = ssl.create_default_context()
        if ca_file:
            client_ctx.load_verify_locations(cafile=ca_file)
        client_ctx.check_hostname = False
        client_ctx.verify_mode = ssl.CERT_REQUIRED
        with socket.create_connection(('127.0.0.1', port), timeout=timeout) as sock:
            with client_ctx.wrap_socket(sock, server_hostname='localhost') as tls_sock:
                peer = tls_sock.getpeercert()
                version = tls_sock.version()
                cipher = tls_sock.cipher()
        subject = dict(x[0] for x in (peer or {}).get('subject', []))
        cn = subject.get('commonName', '未知')
        return True, f'TLS 握手成功（{version}，{cipher[0] if cipher else "未知"}，CN={cn}）'
    except ssl.SSLCertVerificationError as e:
        return False, f'证书链校验失败：{e.verify_message}'
    except ssl.SSLError as e:
        return False, f'TLS 握手失败：{e}'
    except OSError as e:
        return False, f'TLS 连接失败：{e}'
    finally:
        httpd.shutdown()
        httpd.server_close()


def _write_subj_config(path, fields, ca=False):
    """写出带 UTF-8 主题的 OpenSSL 配置。

    非 ASCII 值经 -subj 传递会按 latin-1 编码导致证书乱码，
    改用配置文件里的 utf8 字符串类型可以保留原样。
    """
    lines = ['[ req ]', 'distinguished_name = dn', 'prompt = no',
             'string_mask = utf8only', '', '[ dn ]']
    lines += [f'{k} = {v}' for k, v in fields.items()]
    if ca:
        lines += ['', '[ v3_ca ]', 'basicConstraints = critical, CA:TRUE',
                  'keyUsage = critical, keyCertSign, cRLSign',
                  'subjectKeyIdentifier = hash',
                  'authorityKeyIdentifier = keyid:always']
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return path


# ------------------------------------------------------------ 用户与会话 --

DEFAULT_ADMIN_PASSWORD = 'admin'


def load_users():
    """读取用户表；首次启动用 QILIN_ADMIN_PASSWORD 初始化（缺省 admin）。

    只在「还没有 users.json」时生效——一旦账号建立，改这个环境变量不再影响密码，
    改密码请走面板的「设置」页。
    """
    users_file = os.path.join(BASE_DIR, 'users.json')
    users = _read_json(users_file)
    if users:
        return users
    initial_password = os.environ.get('QILIN_ADMIN_PASSWORD') or DEFAULT_ADMIN_PASSWORD
    if initial_password == DEFAULT_ADMIN_PASSWORD:
        print('[warn] 管理员密码仍是默认值 admin，请登录后立即在「设置」页修改。', flush=True)
    users = {'admin': {'password': generate_password_hash(initial_password),
                       'role': 'admin'}}
    save_users(users)
    return users


def save_users(users):
    users_file = os.path.join(BASE_DIR, 'users.json')
    fd = os.open(users_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=4)


USERS = load_users()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username') or ''
        password = request.form.get('password') or ''
        user = USERS.get(username)
        if user and check_password_hash(user['password'], password):
            session['username'] = username
            if request.form.get('remember'):
                session.permanent = True
                app.permanent_session_lifetime = datetime.timedelta(days=30)
            return redirect(url_for('index'))
        return render_template('login.html', error='用户名或密码错误')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    current = session['username']
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        old_password = request.form.get('old_password') or ''
        new_password = request.form.get('new_password') or ''
        error = None
        if not check_password_hash(USERS[current]['password'], old_password):
            error = '原密码错误'
        elif not _valid_name(username):
            error = '用户名只能包含中英文、数字、下划线、连字符和点'
        elif username != current and username in USERS:
            error = '该用户名已被占用'
        elif len(new_password) < 6:
            error = '新密码至少 6 位'
        if error:
            return render_template('settings.html', username=current, error=error)

        USERS[username] = USERS.pop(current)
        USERS[username]['password'] = generate_password_hash(new_password)
        save_users(USERS)
        session['username'] = username
        return render_template('settings.html', username=username, success='用户信息更新成功')
    return render_template('settings.html', username=current)


@app.errorhandler(413)
def too_large(_e):
    return jsonify({'success': False, 'message': '上传文件过大（上限 8MB）'}), 413


@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    """暂存上传的证书/私钥文件。

    文件名由服务端生成（uuid + 白名单后缀），因此原始文件名里的中文、
    空格或路径都不会影响落盘，也不会出现 secure_filename 归零导致写入目录的情况。
    """
    file = request.files.get('file')
    if not file or not file.filename:
        return _err('没有选择文件')
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        return _err('仅支持 .crt / .pem / .cer / .key 文件')
    stored = f'{uuid.uuid4().hex}{ext}'
    file.save(os.path.join(UPLOAD_DIR, stored))
    return jsonify({'success': True, 'filename': stored, 'original': file.filename})


# ------------------------------------------------------------ 页面路由 --

@app.route('/')
@login_required
def index():
    return render_template('index.html', ca_info=_read_json(CA_INFO_FILE))


@app.route('/verify')
@login_required
def verify():
    return render_template('verify.html')


@app.route('/proxy')
@login_required
def proxy():
    return render_template('proxy.html', host_network=HOST_NETWORK)


@app.route('/tutorial')
@login_required
def tutorial():
    return render_template('tutorial.html')


@app.route('/about')
@login_required
def about():
    return render_template('about.html')


# ------------------------------------------------------------ 虚拟机构 CA --

def generate_ca(org_name=None, password=None):
    """创建自签根证书，返回 ca_info。"""
    org_name = org_name or 'https_ssl CA'
    if not os.path.isfile(OPENSSL_CMD):
        raise RuntimeError(f'未找到 OpenSSL: {OPENSSL_CMD}')

    passin = ['-passin', f'pass:{password}'] if password else []
    key_cmd = ['genrsa']
    if password:
        key_cmd += ['-des3', '-passout', f'pass:{password}']
    key_cmd += ['-out', CA_KEY, '4096']
    ok, _, err = _openssl(key_cmd, timeout=60)
    if not ok:
        raise RuntimeError(f'生成 CA 私钥失败：{err.strip() or "未知错误"}')

    _write_subj_config(os.path.join(CA_DIR, 'qilin-ca.cnf'), {
        'C': 'CN', 'ST': 'Guangdong', 'L': 'Shenzhen', 'O': org_name,
        'OU': 'Certificate Authority Department', 'CN': org_name,
        'emailAddress': 'ca@https-ssl.local',
    }, ca=True)
    ok, _, err = _openssl([
        'req', '-x509', '-new', '-nodes', '-key', CA_KEY,
        '-sha256', '-days', '3650',
        '-config', os.path.join(CA_DIR, 'qilin-ca.cnf'),
        '-extensions', 'v3_ca', '-out', CA_CRT, *passin,
    ], timeout=60)
    if not ok:
        raise RuntimeError(f'生成 CA 证书失败：{err.strip() or "未知错误"}')

    ca_info = {
        'org_name': org_name,
        'created_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'valid_until': (datetime.datetime.now() + datetime.timedelta(days=3650)).strftime('%Y-%m-%d'),
        'has_password': bool(password),
    }
    _write_json(CA_INFO_FILE, ca_info)
    return ca_info


def _ca_row(ca_info):
    """CA 表格行；org_name 由用户输入，必须转义。"""
    name = escape(ca_info.get('org_name', ''))
    return f'''<tr>
            <td>{name}</td>
            <td>{escape(ca_info.get('valid_until', ''))}</td>
            <td>{escape(ca_info.get('created_at', ''))}</td>
            <td><a href="{url_for('download', cert_dir='ca', filename='qilin-ca.crt')}"><i class="fas fa-download"></i> 下载CA证书</a></td>
        </tr>'''


def _sync_proxies_after_ca_change():
    """CA 变更后同步各站点。

    证书仍能链到新 CA 的刷新站点证书链；链不上的说明已成废证，
    直接停用服务并清掉站点配置，避免留下让 nginx 校验失败的坏配置。
    """
    proxies = _load_proxies()
    stopped = False
    for p in proxies:
        if p.get('cert_type') != 'qilin':
            continue
        cert_id = p.get('cert_id') or p['id']
        crt = os.path.join(CERTS_DIR, cert_id, f'{cert_id}.crt')
        if _chains_to_ca(crt):
            _copy_certificate(cert_id, p['id'])
        elif p.get('status') == 'on':
            _remove_site_conf(p['id'])
            p['status'], p['pid'] = 'off', None
            stopped = True
    if stopped:
        _save_proxies(proxies)
    _apply_nginx()


@app.route('/create_ca', methods=['POST'])
@login_required
def create_ca():
    org_name = (request.form.get('org_name') or '').strip()
    password = request.form.get('password') or None
    try:
        for path in (CA_KEY, CA_CRT, CA_INFO_FILE, os.path.join(CA_DIR, 'qilin-ca.srl')):
            if os.path.exists(path):
                os.remove(path)
        ca_info = generate_ca(org_name, password)
    except Exception as e:
        return str(e), 500
    # 新 CA 无法验证旧证书：能链上的刷新，链不上的停用。
    _sync_proxies_after_ca_change()
    return _ca_row(ca_info)


@app.route('/delete_ca', methods=['POST'])
@login_required
def delete_ca():
    for path in (CA_KEY, CA_CRT, CA_INFO_FILE, os.path.join(CA_DIR, 'qilin-ca.srl')):
        if os.path.exists(path):
            os.remove(path)
    _sync_proxies_after_ca_change()
    return '<tr class="empty-row"><td colspan="4">证书申请前需要创建虚拟机构，请点击左上角按钮创建</td></tr>'


@app.route('/check_ca_password')
@login_required
def check_ca_password():
    """前端据此提示是否需要输入 CA 密码（此前该接口缺失，提示恒为降级文案）。"""
    return jsonify({'has_password': bool(_ca_info().get('has_password'))})


# ------------------------------------------------------------ 证书签发 --

def _parse_san(ip_addresses, domains, cert_name):
    """解析并校验 SAN 列表，返回 (san_entries, error)。

    域名/IP 会原样写入 OpenSSL 配置，不校验的话换行可以注入额外扩展
    （例如把 CA:TRUE 塞进去），因此这里逐项严格校验。
    """
    entries, invalid = [], []
    for ip in (ip_addresses or '').replace(',', ';').split(';'):
        ip = ip.strip()
        if not ip:
            continue
        if _valid_ip(ip):
            entries.append(f'IP:{ip}')
        else:
            invalid.append(ip)
    for domain in (domains or '').replace(',', ';').split(';'):
        domain = domain.strip()
        if not domain:
            continue
        if _valid_domain(domain):
            entries.append(f'DNS:{domain}')
        else:
            invalid.append(domain)
    if invalid:
        return None, '以下 IP 或域名格式不合法：' + '、'.join(invalid[:5])
    # 用 IP 直连时客户端不发 SNI，补一个与证书同名的兜底域名。
    if f'DNS:{cert_name}' not in entries:
        entries.append(f'DNS:{cert_name}')
    return entries, None


def _cert_row(cert_name, valid_until, ips, domains):
    """证书表格行，create_cert 与 list_certs 共用。"""
    def cell(values):
        if not values:
            return '/'
        more = ' has-more' if len(values) > 3 else ''
        items = ''.join(f'<li>{escape(v)}</li>' for v in values)
        return f'<ul class="address-list{more}">{items}</ul>'

    name = escape(cert_name)
    return f'''<tr>
                <td><input type="checkbox" class="cert-checkbox" data-cert-name="{name}"></td>
                <td>{name}</td>
                <td>{escape(valid_until)}</td>
                <td class="ip-address">{cell(ips)}</td>
                <td class="domain-name">{cell(domains)}</td>
                <td><a href="{url_for('download', cert_dir=cert_name, filename=cert_name + '.crt')}"><i class="fas fa-certificate"></i> {name}.crt</a></td>
                <td><a href="{url_for('download', cert_dir=cert_name, filename=cert_name + '.key')}"><i class="fas fa-key"></i> {name}.key</a></td>
            </tr>'''


def _leaf_days(cap=3650):
    """叶子证书有效期上限：不超过 CA 剩余有效期，避免签发即超出签发者。"""
    info = _cert_info(CA_CRT)
    if not info or not info.get('not_after_ts'):
        return cap
    remaining = int((info['not_after_ts'] - time.time()) // 86400) - 1
    return max(1, min(cap, remaining))


@app.route('/create_cert', methods=['POST'])
@login_required
def create_cert():
    cert_name = (request.form.get('cert_name') or '').strip()
    ip_addresses = request.form.get('ip_addresses') or ''
    domains = request.form.get('domains') or ''
    password = request.form.get('cert_password') or ''

    if not _valid_name(cert_name):
        return '证书名称只能包含中英文、数字、下划线、连字符和点（1-64 字符）', 400
    if cert_name in RESERVED_CERT_NAMES:
        return f'证书名称 {cert_name} 为系统保留名称', 400
    if not (os.path.isfile(CA_KEY) and os.path.isfile(CA_CRT)):
        return '请先创建虚拟机构，后申请证书', 400

    cert_dir = os.path.join(CERTS_DIR, cert_name)
    if os.path.exists(cert_dir):
        return f'证书 {cert_name} 已存在，请先删除或更换名称', 409

    san_entries, error = _parse_san(ip_addresses, domains, cert_name)
    if error:
        return error, 400

    has_password = bool(_ca_info().get('has_password'))
    if has_password and not password:
        return 'CA证书有密码保护，请提供密码', 400

    # 先在暂存目录里完成全部签发，成功后再改名到位：
    # 任何一步失败都不会留下半成品证书目录。
    staging = os.path.join(CERTS_DIR, f'.staging-{uuid.uuid4().hex}')
    os.makedirs(staging)
    try:
        _issue_cert(staging, cert_name, san_entries, password if has_password else None)
    except _SignError as e:
        shutil.rmtree(staging, ignore_errors=True)
        return str(e), e.code
    os.rename(staging, cert_dir)

    info = _cert_info(os.path.join(cert_dir, f'{cert_name}.crt')) or {}
    return _cert_row(cert_name, info.get('not_after', ''), info.get('ips', []),
                     info.get('domains', []))


class _SignError(Exception):
    """签发失败，附带应返回给前端的 HTTP 状态码。"""

    def __init__(self, message, code=500):
        super().__init__(message)
        self.code = code


def _issue_cert(cert_dir, cert_name, san_entries, password):
    """在给定目录内生成私钥、CSR 并用本机 CA 签发证书。"""
    key_file = os.path.join(cert_dir, f'{cert_name}.key')
    csr_file = os.path.join(cert_dir, f'{cert_name}.csr')
    crt_file = os.path.join(cert_dir, f'{cert_name}.crt')
    ext_file = os.path.join(cert_dir, f'{cert_name}.ext')
    csr_conf = os.path.join(cert_dir, f'{cert_name}.cnf')

    ok, _, err = _openssl(['genrsa', '-out', key_file, '2048'])
    if not ok:
        raise _SignError(f'生成私钥失败：{err.strip() or "未知错误"}')

    _write_subj_config(csr_conf, {
        'C': 'CN', 'ST': 'Guangdong', 'L': 'Shenzhen', 'O': 'https_ssl CA',
        'OU': 'IT Department', 'CN': cert_name,
    })
    ok, _, err = _openssl(['req', '-new', '-key', key_file, '-config', csr_conf,
                           '-out', csr_file])
    if not ok:
        raise _SignError(f'生成证书请求失败：{err.strip() or "未知错误"}')

    with open(ext_file, 'w', encoding='utf-8') as f:
        f.write('[req]\nreq_extensions = v3_req\n[v3_req]\n'
                'basicConstraints = CA:FALSE\n'
                'keyUsage = critical, digitalSignature, keyEncipherment\n'
                'extendedKeyUsage = serverAuth\n'
                'subjectKeyIdentifier = hash\n'
                'authorityKeyIdentifier = keyid,issuer\n'
                f'subjectAltName = {", ".join(san_entries)}')

    sign_cmd = ['x509', '-req', '-in', csr_file, '-CA', CA_CRT, '-CAkey', CA_KEY,
                '-CAcreateserial', '-out', crt_file, '-days', str(_leaf_days()),
                '-extfile', ext_file, '-extensions', 'v3_req']
    if password:
        sign_cmd += ['-passin', f'pass:{password}']
    ok, _, err = _openssl(sign_cmd, timeout=20)
    if not ok:
        detail = err.strip() or '未知错误'
        if 'bad decrypt' in detail or 'bad password' in detail or 'passphrase' in detail:
            raise _SignError('CA证书密码错误，请重试', 400)
        raise _SignError(f'证书签发失败：{detail}')


@app.route('/download/<path:cert_dir>/<filename>')
@login_required
def download(cert_dir, filename):
    """下载证书文件，路径限制在 CA 与证书目录内。

    cert_dir 来自 URL，因此解析真实路径后必须确认它仍在允许的根目录下，
    否则 ../../etc 这类取值可以读到目录外的文件。
    """
    if not _valid_name(cert_dir) or not _valid_name(filename):
        return '非法的文件名', 400

    if cert_dir == 'ca':
        # 只放行根证书本身：CA 私钥留在宿主卷里，不经面板分发。
        if filename != 'qilin-ca.crt':
            return '该文件不可下载', 403
        root, full_path = CA_DIR, os.path.join(CA_DIR, filename)
    else:
        root = CERTS_DIR
        full_path = os.path.join(CERTS_DIR, cert_dir, filename)

    root_real = os.path.realpath(root)
    full_real = os.path.realpath(full_path)
    if full_real != root_real and not full_real.startswith(root_real + os.sep):
        return '非法的文件路径', 400
    if not os.path.isfile(full_real):
        return '文件不存在', 404
    return send_file(full_real, as_attachment=True)


@app.route('/list_certs')
@login_required
def list_certs():
    """证书列表，同时服务主页表格（HTML）与下拉框（JSON）。"""
    want_json = ('application/json' in request.headers.get('Accept', '')
                 or request.args.get('format') == 'json')
    cert_list, html_rows = [], []
    for cert_name in sorted(os.listdir(CERTS_DIR)):
        cert_dir = os.path.join(CERTS_DIR, cert_name)
        crt_file = os.path.join(cert_dir, f'{cert_name}.crt')
        key_file = os.path.join(cert_dir, f'{cert_name}.key')
        if not os.path.isdir(cert_dir) or not (os.path.isfile(crt_file) and os.path.isfile(key_file)):
            continue
        info = _cert_info(crt_file) or {}
        valid_until = info.get('not_after', '')
        domains, ips = info.get('domains', []), info.get('ips', [])
        cert_list.append({'name': cert_name, 'valid_until': valid_until,
                          'domains': domains, 'ips': ips,
                          'files': {'crt': f'{cert_name}.crt', 'key': f'{cert_name}.key'}})
        html_rows.append(_cert_row(cert_name, valid_until, ips, domains))
    if want_json:
        return jsonify({'certs': cert_list})
    return ''.join(html_rows)


@app.route('/delete_certs', methods=['POST'])
@login_required
def delete_certs():
    """删除证书；被反代引用的证书会先停用对应服务，避免留下坏配置。"""
    cert_names = _body().get('cert_names') or []
    if not isinstance(cert_names, list):
        return _err('参数格式错误')
    certs_root = os.path.realpath(CERTS_DIR)
    deleted, skipped, stopped = [], [], []
    for name in cert_names:
        if not _valid_name(name):
            skipped.append(str(name))
            continue
        path = os.path.realpath(os.path.join(certs_root, name))
        if os.path.dirname(path) != certs_root or not os.path.isdir(path):
            skipped.append(name)
            continue
        stopped += _stop_proxies_using_cert(name)
        shutil.rmtree(path)
        deleted.append(name)
    return jsonify({'status': 'success', 'deleted': deleted,
                    'skipped': skipped, 'stopped_proxies': stopped})


def _stop_proxies_using_cert(cert_name):
    """停用引用了指定证书的反向代理，返回被停用的服务名列表。"""
    proxies = _load_proxies()
    stopped = []
    for p in proxies:
        if p.get('cert_type') == 'qilin' and (p.get('cert_id') or p['id']) == cert_name:
            _remove_site_conf(p['id'])
            p['status'], p['pid'] = 'off', None
            stopped.append(p['id'])
    if stopped:
        _save_proxies(proxies)
        _apply_nginx()
    return stopped


# ------------------------------------------------------------ 证书验证 --

@app.route('/verify_cert', methods=['POST'])
@login_required
def verify_cert():
    """两步验证证书：先本地解析，再用真实 TLS 握手确认可用。

    本地这一步不需要网络就能查出文件损坏、私钥不匹配、已过期；
    握手这一步证明客户端确实能完成协商与链校验，也就是用户真正关心的结果。
    """
    data = _body()
    address = (data.get('address') or '').strip()
    cert_name = (data.get('cert_name') or data.get('cert_id') or '').strip()
    cert_filename = (data.get('cert_filename') or '').strip()
    key_filename = (data.get('key_filename') or '').strip()

    if cert_name:
        if not _valid_name(cert_name):
            return _err('证书名称非法')
        cert_dir = os.path.join(CERTS_DIR, cert_name)
        crt_file = os.path.join(cert_dir, f'{cert_name}.crt')
        key_file = os.path.join(cert_dir, f'{cert_name}.key')
    elif cert_filename and key_filename:
        if not (_valid_name(cert_filename) and _valid_name(key_filename)):
            return _err('上传的证书文件名非法')
        crt_file = os.path.join(UPLOAD_DIR, cert_filename)
        key_file = os.path.join(UPLOAD_DIR, key_filename)
    else:
        return _err('请选择证书，或上传证书与私钥文件')

    if not (os.path.isfile(crt_file) and os.path.isfile(key_file)):
        return _err('证书文件不存在，请重新上传', 404)

    info = _cert_info(crt_file)
    if not info:
        return _err('证书解析失败，文件可能已损坏或不是 PEM 格式的 X.509 证书')
    details = ['证书解析成功']

    if not _key_matches_cert(crt_file, key_file):
        return _err('证书与私钥不匹配')
    details.append('证书与私钥匹配')

    if info.get('not_after_ts') and time.time() > info['not_after_ts']:
        return _err(f"证书已于 {info.get('not_after')} 过期")
    details.append(f"证书在有效期内（到期 {info.get('not_after')}）")

    if not _address_covered(address, info):
        covered = ', '.join(info.get('domains', []) + info.get('ips', [])) or '无'
        return _err(f'证书未覆盖地址 {address}；该证书覆盖：{covered}')
    if address:
        details.append(f'证书覆盖地址 {address}')

    ca_file = CA_CRT if _chains_to_ca(crt_file) else None
    tls_ok, tls_msg = _tls_handshake_check(crt_file, key_file, ca_file=ca_file)
    if not tls_ok:
        return jsonify({'success': False, 'message': tls_msg, 'details': details,
                        'chain_verified': False, 'chain_error': tls_msg}), 200
    details.append(tls_msg)

    return jsonify({
        'success': True,
        'message': '；'.join(details),
        'details': details,
        'address': address,
        'domains': info.get('domains', []),
        'ips': info.get('ips', []),
        'not_after': info.get('not_after', ''),
        'chain_verified': True,
        'chain_error': '',
    })


# ------------------------------------------------------- 反向代理（nginx） --

def _load_proxies():
    data = _read_json(PROXY_DATA_FILE)
    return data if isinstance(data, list) else []


def _save_proxies(proxies):
    _write_json(PROXY_DATA_FILE, proxies)


def _nginx(*args, timeout=30):
    """执行 nginx 命令，返回 (是否成功, 输出)。"""
    try:
        proc = subprocess.run(['nginx', *args], capture_output=True,
                              text=True, timeout=timeout)
        return proc.returncode == 0, ((proc.stdout or '') + (proc.stderr or '')).strip()
    except FileNotFoundError:
        return False, '容器内未找到 nginx，请确认镜像构建正确'
    except subprocess.TimeoutExpired:
        return False, 'nginx 命令执行超时'


def _start_nginx():
    """以后台方式启动 nginx。

    nginx 会 fork 后退出，但继承了 stdout 管道的子进程会让 capture 一直等待，
    因此这里把两个流都从守护进程的启动路径上摘掉。
    """
    try:
        proc = subprocess.run(['nginx', '-g', 'daemon on;'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              text=True, timeout=15)
        return proc.returncode == 0, (proc.stderr or '').strip()
    except FileNotFoundError:
        return False, '容器内未找到 nginx，请确认镜像构建正确'
    except subprocess.TimeoutExpired:
        return False, 'nginx 命令执行超时'


def _nginx_running():
    """容器内是否有存活的 nginx 进程。

    只信 pid 文件并不可靠：可能存在多个 master，且停止后文件会残留，
    因此直接扫描进程表。
    """
    try:
        entries = os.listdir('/proc')
    except OSError:
        return False
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f'/proc/{entry}/comm', 'r') as f:
                if f.read().strip() == 'nginx':
                    return True
        except OSError:
            continue
    return False


def _prune_broken_sites():
    """移除证书文件已丢失的站点配置，并把对应服务标记为停止。

    nginx -t 是整份配置一起校验的，只要有一个站点引用了不存在的证书，
    所有服务的启停都会失败。这里先自愈，避免单点拖垮全局。
    """
    if not os.path.isdir(PROXY_SITES_DIR):
        return []
    broken = []
    for fname in os.listdir(PROXY_SITES_DIR):
        if not fname.endswith('.conf'):
            continue
        path = os.path.join(PROXY_SITES_DIR, fname)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read()
        except OSError:
            continue
        missing = [p for p in re.findall(r'ssl_certificate(?:_key)?\s+(\S+);', text)
                   if not os.path.exists(p)]
        if missing:
            os.remove(path)
            broken.append(fname[:-len('.conf')])
    if broken:
        proxies = _load_proxies()
        changed = False
        for p in proxies:
            if p['id'] in broken and p.get('status') == 'on':
                p['status'], p['pid'] = 'off', None
                changed = True
        if changed:
            _save_proxies(proxies)
    return broken


def _apply_nginx():
    """校验配置后启动或热加载 nginx。

    nginx 常驻运行，reload 即可应用站点变更而不影响其他服务；
    进程不存在时则启动一个。
    """
    pruned = _prune_broken_sites()
    ok, output = _nginx('-t')
    if not ok:
        return False, f'nginx 配置校验失败：{output}'
    if not _nginx_running():
        ok, output = _start_nginx()
        if not ok:
            return False, f'启动 nginx 失败：{output}'
        return True, ''
    ok, output = _nginx('-s', 'reload')
    if not ok:
        return False, f'重载 nginx 配置失败：{output}'
    if pruned:
        return True, f'已自动停用证书缺失的服务：{"、".join(pruned)}'
    return True, ''


def _parse_proxy_url(proxy_url):
    """拆分反代地址为 (scheme, host, port, path)，并补默认端口。"""
    raw = (proxy_url or '').strip().rstrip('/')
    if '://' not in raw:
        raw = 'https://' + raw
    scheme, rest = raw.split('://', 1)
    scheme = scheme.lower()
    if '/' in rest:
        authority, _, tail = rest.partition('/')
        path = '/' + tail
    else:
        authority, path = rest, '/'
    if ':' in authority:
        host, _, port_text = authority.rpartition(':')
        try:
            port = int(port_text)
        except ValueError:
            host, port = authority, (443 if scheme == 'https' else 80)
    else:
        host, port = authority, (443 if scheme == 'https' else 80)
    return scheme, host, port, path


def _proxy_target(proxy):
    """校验反代记录中会写入 nginx 配置的字段。

    返回 (scheme, host, port, upstream, error)。所有值最终都会进 nginx 配置，
    未转义的换行或大括号可以改写配置而不只是描述一个 server。
    """
    service_name = proxy.get('id')
    if not _valid_name(service_name):
        return None, None, None, None, f'服务名称非法：{service_name}'

    scheme, host, port, _ = _parse_proxy_url(proxy.get('proxy_url'))
    if not _valid_server_name(host):
        return None, None, None, None, f'反代后地址非法：{host}'
    if not 0 < port < 65536:
        return None, None, None, None, f'端口非法：{port}'

    upstream = (proxy.get('original_url') or '').strip()
    if '://' not in upstream:
        upstream = 'http://' + upstream
    if not _valid_upstream(upstream):
        return None, None, None, None, f'原本地址非法：{proxy.get("original_url")}'
    return scheme, host, port, upstream, None


def _write_site_conf(proxy):
    """按服务生成一个 nginx server 块。"""
    scheme, host, port, upstream, error = _proxy_target(proxy)
    if error:
        raise ValueError(error)
    service_name = proxy['id']

    listen = f'listen {port}{" ssl" if scheme == "https" else ""};'
    if PROXY_LISTEN_HOST:
        listen = listen.replace('listen ', f'listen {PROXY_LISTEN_HOST}:')

    ssl_block = ''
    if scheme == 'https':
        ssl_block = (f'    ssl_certificate     {PROXY_CERTS_DIR}/{service_name}.crt;\n'
                     f'    ssl_certificate_key {PROXY_CERTS_DIR}/{service_name}.key;\n'
                     '    ssl_protocols TLSv1.2 TLSv1.3;\n'
                     '    ssl_prefer_server_ciphers off;\n\n')

    conf = f'''server {{
    {listen}
    server_name {host};

{ssl_block}    location / {{
        proxy_pass {upstream};
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $http_host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
    }}
}}
'''
    with open(os.path.join(PROXY_SITES_DIR, f'{service_name}.conf'), 'w',
              encoding='utf-8') as f:
        f.write(conf)


def _remove_site_conf(service_name):
    conf_path = os.path.join(PROXY_SITES_DIR, f'{service_name}.conf')
    if os.path.exists(conf_path):
        os.remove(conf_path)


def _proxy_cert_paths(service_name):
    return (os.path.join(PROXY_CERTS_DIR, f'{service_name}.crt'),
            os.path.join(PROXY_CERTS_DIR, f'{service_name}.key'))


def _write_proxy_cert(service_name, chain_text, key_src):
    """写入 nginx 用的证书对，私钥权限收紧到 0600。"""
    crt_path, key_path = _proxy_cert_paths(service_name)
    with open(crt_path, 'w', encoding='utf-8') as f:
        f.write(chain_text)
    shutil.copyfile(key_src, key_path)
    os.chmod(key_path, 0o600)


def _copy_certificate(cert_id, service_name):
    """把本机签发的证书复制到 nginx 证书目录，并追加 CA 形成完整链。

    客户端只需信任该 CA 即可通过校验。证书缺失时返回错误说明，成功返回 None。
    """
    src_dir = os.path.join(CERTS_DIR, cert_id)
    crt = os.path.join(src_dir, f'{cert_id}.crt')
    key = os.path.join(src_dir, f'{cert_id}.key')
    if not (os.path.isfile(crt) and os.path.isfile(key)):
        return f'证书 {cert_id} 不存在，请先在主页申请证书'
    with open(crt, 'r', encoding='utf-8') as f:
        chain = f.read().rstrip('\n') + '\n'
    if os.path.isfile(CA_CRT):
        with open(CA_CRT, 'r', encoding='utf-8') as f:
            chain += f.read().rstrip('\n') + '\n'
    _write_proxy_cert(service_name, chain, key)
    return None


def _install_custom_cert(service_name, cert_name, key_name):
    """把上传的自定义证书对放进 nginx 证书目录。

    证书可以自带完整链，也可以是裸叶子证书，因此商业证书与自签证书都能用；
    但必须能被 OpenSSL 解析，且私钥与证书确实配对。
    """
    if not (_valid_name(cert_name) and _valid_name(key_name)):
        return '证书文件名非法'
    src_crt = os.path.join(UPLOAD_DIR, cert_name)
    src_key = os.path.join(UPLOAD_DIR, key_name)
    if not (os.path.isfile(src_crt) and os.path.isfile(src_key)):
        return '上传的证书或私钥文件不存在，请重新上传'
    if not _cert_info(src_crt):
        return '证书文件无法解析，请确认是 PEM 格式的 X.509 证书'
    if not _key_matches_cert(src_crt, src_key):
        return '证书与私钥不匹配'
    with open(src_crt, 'r', encoding='utf-8') as f:
        _write_proxy_cert(service_name, f.read(), src_key)
    return None


def _delete_proxy_files(service_name):
    """清理代理占用的站点配置与证书文件。"""
    _remove_site_conf(service_name)
    for path in _proxy_cert_paths(service_name):
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


@app.route('/get_proxy_list', methods=['GET'])
@login_required
def get_proxy_list():
    return jsonify({'success': True, 'proxies': _load_proxies()})


@app.route('/get_proxy_pid/<proxy_id>', methods=['GET'])
@login_required
def get_proxy_pid(proxy_id):
    """按 nginx 进程状态回报单个服务的运行状态。"""
    proxy = next((p for p in _load_proxies() if str(p['id']) == str(proxy_id)), None)
    if not proxy:
        return _err('未找到配置', 404)
    running = _nginx_running() and proxy.get('status') == 'on'
    return jsonify({'success': True,
                    'pid': proxy.get('pid') if running else None,
                    'status': 'on' if running else 'off'})


@app.route('/run_proxy', methods=['POST'])
@login_required
def run_proxy():
    proxy_id = _body().get('proxy_id')
    proxies = _load_proxies()
    proxy = next((p for p in proxies if str(p['id']) == str(proxy_id)), None)
    if not proxy:
        return _err('未找到配置', 404)

    if proxy.get('cert_type') == 'qilin':
        error = _copy_certificate(proxy.get('cert_id') or proxy['id'], proxy['id'])
        if error:
            return _err(error, 500)
    else:
        crt_path, key_path = _proxy_cert_paths(proxy['id'])
        if not (os.path.isfile(crt_path) and os.path.isfile(key_path)):
            return _err('站点证书文件缺失，请重新上传证书', 500)

    _write_site_conf(proxy)
    ok, output = _apply_nginx()
    if not ok:
        return _err(output, 500)

    proxy['status'], proxy['pid'] = 'on', 'nginx'
    _save_proxies(proxies)
    return jsonify({'success': True, 'pid': 'nginx', 'message': output})


@app.route('/stop_proxy', methods=['POST'])
@login_required
def stop_proxy():
    """停用单个服务：删掉它的站点配置并热加载，其他服务不受影响。"""
    proxy_id = _body().get('proxy_id')
    proxies = _load_proxies()
    proxy = next((p for p in proxies if str(p['id']) == str(proxy_id)), None)
    if not proxy:
        return _err('未找到配置', 404)

    _remove_site_conf(proxy['id'])
    ok, output = _apply_nginx()
    if not ok:
        # 回滚配置，保持「面板状态 = 实际状态」。
        _write_site_conf(proxy)
        _apply_nginx()
        return _err(output, 500)

    proxy['status'], proxy['pid'] = 'off', None
    _save_proxies(proxies)
    return jsonify({'success': True, 'message': output})


@app.route('/delete_proxy', methods=['POST'])
@login_required
def delete_proxy():
    data = _body()
    ids = data.get('proxy_ids') or ([data['proxy_id']] if data.get('proxy_id') else [])
    ids = [str(i) for i in ids if i]
    if not ids:
        return _err('未指定要删除的配置')

    proxies = _load_proxies()
    _save_proxies([p for p in proxies if str(p['id']) not in ids])
    for pid in ids:
        _delete_proxy_files(pid)
    _apply_nginx()
    return jsonify({'success': True, 'deleted': ids})


@app.route('/create_proxy', methods=['POST'])
@login_required
def create_proxy():
    """新增或更新一个反向代理服务。

    所有校验都放在落盘之前：旧实现先写 proxy_data.json 再校验，校验失败时
    脏记录已经持久化且无法通过界面清理。
    """
    service_name = (request.form.get('service_name') or '').strip()
    original_service_name = (request.form.get('original_service_name') or '').strip()
    cert_type = request.form.get('cert_type') or 'qilin'
    proxy_url = (request.form.get('proxy_url') or '').strip()
    original_url = (request.form.get('original_url') or '').strip()

    data = {
        'id': service_name, 'service_name': service_name,
        'original_url': original_url, 'proxy_url': proxy_url,
        'cert_type': cert_type, 'status': 'off', 'pid': None,
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    _, _, _, _, error = _proxy_target(data)
    if error:
        return _err(error)
    if cert_type not in ('qilin', 'custom'):
        return _err(f'证书类型非法：{cert_type}')

    # 准备 nginx 用的证书文件；失败时直接返回，不写任何状态。
    if cert_type == 'qilin':
        cert_id = (request.form.get('cert_id') or service_name).strip()
        if not _valid_name(cert_id):
            return _err(f'证书名称非法：{cert_id}')
        error = _copy_certificate(cert_id, service_name)
        if error:
            return _err(error, 500)
        data.update(cert_id=cert_id, cert_filename=f'{cert_id}.crt',
                    key_filename=f'{cert_id}.key',
                    cert_display=f'{cert_id}.crt', key_display=f'{cert_id}.key')
    else:
        cert_filename = (request.form.get('cert_filename') or '').strip()
        key_filename = (request.form.get('key_filename') or '').strip()
        if not (cert_filename and key_filename):
            return _err('请上传证书和私钥文件')
        error = _install_custom_cert(service_name, cert_filename, key_filename)
        if error:
            return _err(error, 500)
        # cert_filename 存服务端生成的存储名（编辑时据此复用），
        # cert_display 存用户看到的原始文件名（仅用于界面展示）。
        data.update(cert_id=service_name, cert_filename=cert_filename,
                    key_filename=key_filename,
                    cert_display=(request.form.get('cert_display') or cert_filename),
                    key_display=(request.form.get('key_display') or key_filename))

    crt_path, _ = _proxy_cert_paths(service_name)
    data['cert_expiry'] = _cert_expiry(crt_path)

    proxies = _load_proxies()
    existing = next((p for p in proxies if p['id'] == service_name), None)
    # 编辑时保留原有启用状态，避免面板显示与实际服务不一致。
    if existing and existing.get('status') == 'on':
        data['status'], data['pid'] = 'on', 'nginx'
    data['created_at'] = (existing or {}).get('created_at', data['created_at'])
    proxies = [p for p in proxies if p['id'] not in (service_name, original_service_name)]
    proxies.append(data)
    _save_proxies(proxies)

    # 改名时清掉旧服务的站点配置与证书文件。
    if original_service_name and original_service_name != service_name:
        _delete_proxy_files(original_service_name)

    try:
        _write_site_conf(data)
    except ValueError as e:
        return _err(str(e), 500)

    message = ''
    if data['status'] == 'on':
        ok, output = _apply_nginx()
        if not ok:
            return _err(output, 500)
        message = output
    return jsonify({'success': True, 'proxy_id': service_name, 'proxy': data,
                    'cert_expiry': data['cert_expiry'], 'message': message})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PANEL_PORT, debug=False)

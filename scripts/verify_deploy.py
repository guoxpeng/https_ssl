"""部署后验证：用真实数据把关键接口跑一遍，输出 PASS/FAIL 汇总。

与 scripts/smoke_test.py 的区别：smoke_test 在临时目录里从零走一遍业务流程，
不碰真实数据；本脚本直接对**当前部署**的真实 CA、证书、反代配置做体检，
用于升级后确认服务正常。

用法（推荐在容器内跑，能同时覆盖 nginx 相关逻辑）：

    docker cp scripts/verify_deploy.py qilin_ssl:/tmp/
    docker exec qilin_ssl python /tmp/verify_deploy.py

也可以在装了 Flask 的机器上直接跑，但此时仅覆盖 Flask 侧逻辑：

    QILIN_ADMIN_PASSWORD=x python scripts/verify_deploy.py
"""
import json
import os
import re
import sys

# 容器内 /app 是应用根目录；本机运行时退回脚本所在目录的上一级。
APP_DIR = '/app' if os.path.isdir('/app/templates') else os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

import app as A  # noqa: E402  必须在设置好环境变量后导入

_passed, _failed = [], []


def check(name, ok, detail=''):
    (_passed if ok else _failed).append(name)
    print(f'  {"PASS" if ok else "FAIL"}  {name}' + (f'  <- {detail}' if detail and not ok else ''))


def json_of(client, path):
    """带 Accept 头取 JSON。缺这个头 /list_certs 会返回 HTML 行，get_json() 拿到 None。"""
    r = client.get(path, headers={'Accept': 'application/json'})
    try:
        return r.status_code, r.get_json()
    except Exception:  # noqa: BLE001
        return r.status_code, None


def main():
    print('=' * 64)
    print('运行环境')
    for label, val in (('BASE_DIR', A.BASE_DIR), ('CA_DIR', A.CA_DIR),
                       ('CERTS_DIR', A.CERTS_DIR), ('UPLOAD_DIR', A.UPLOAD_DIR),
                       ('PROXY_DIR', A.PROXY_DIR), ('OPENSSL', A.OPENSSL_CMD)):
        print(f'  {label:<10}= {val}')
    nginx_here = A._nginx_running()
    print(f'  nginx     = {"运行中" if nginx_here else "未运行（本机运行或容器内未起）"}')
    print('=' * 64)

    c = A.app.test_client()
    with c.session_transaction() as s:
        s['username'] = 'admin'

    print('\n[1] 证书列表（验证 OpenSSL 3.x 的 "IP Address:" SAN 解析）')
    code, data = json_of(c, '/list_certs')
    check('GET /list_certs 返回 JSON', code == 200 and isinstance(data, dict), f'{code} {data!r:.120}')
    certs = (data or {}).get('certs', []) if isinstance(data, dict) else []
    print(f'    共 {len(certs)} 张证书')
    for item in certs:
        print(f'      · {item.get("name"):<12} IP={item.get("ips")} 域名={item.get("domains")}'
              f'  到期={item.get("valid_until")}')

    # 精确回归检测：拿证书里真实的 SAN 与解析结果对照。
    # 证书本身没配 SAN 是合法的，不算失败；但「证书里有 SAN、解析结果却为空」
    # 就是 OpenSSL 3.x 把 IP 打印成 "IP Address:" 导致的老 bug 复现。
    mismatched, checked = [], 0
    for item in certs:
        name = item.get('name')
        crt = os.path.join(A.CERTS_DIR, name, f'{name}.crt')
        if not os.path.isfile(crt):
            continue
        ok, out, _err = A._openssl(['x509', '-in', crt, '-noout', '-ext', 'subjectAltName'])
        if not ok:
            continue
        checked += 1
        has_san_in_cert = ('DNS:' in out) or ('IP' in out and 'Address' in out) or ('IP:' in out)
        if has_san_in_cert and not (item.get('domains') or item.get('ips')):
            mismatched.append(name)
    if checked:
        check(f'{checked} 张证书的 SAN 解析与证书内容一致', not mismatched,
              f'以下证书有 SAN 但解析为空：{mismatched}')
    else:
        print('    （无可解析证书，跳过）')

    print('\n[2] CA 状态')
    code, data = json_of(c, '/check_ca_password')
    check('GET /check_ca_password 可用', code == 200 and isinstance(data, dict), f'{code} {data!r}')
    print(f'    has_password = {(data or {}).get("has_password")}')

    print('\n[3] 反向代理')
    code, data = json_of(c, '/get_proxy_list')
    check('GET /get_proxy_list 可用', code == 200 and isinstance(data, dict), f'{code} {data!r:.120}')
    proxies = (data or {}).get('proxies', []) if isinstance(data, dict) else []
    print(f'    共 {len(proxies)} 个服务')
    for p in proxies:
        print(f'      · {p.get("service_name"):<12} {p.get("proxy_url")} -> {p.get("original_url")}'
              f'  [{p.get("status")}]')
    for p in proxies:
        pid = p.get('id') or p.get('service_name')
        code, d = json_of(c, f'/get_proxy_pid/{pid}')
        check(f'状态查询 {pid}', code == 200 and isinstance(d, dict), f'{code} {d!r}')
        conf = os.path.join(A.PROXY_SITES_DIR, f'{pid}.conf')
        if p.get('status') == 'on':
            check(f'{pid} 启用状态与站点配置一致', os.path.isfile(conf),
                  f'记录为 on 但缺少 {conf}')

    print('\n[4] 页面渲染')
    for path in ('/', '/verify', '/proxy', '/settings', '/tutorial', '/about'):
        r = c.get(path)
        check(f'GET {path}', r.status_code == 200, f'status={r.status_code}')

    print('\n[5] 前端资源引用一致性')
    missing = []
    tpl_dir = os.path.join(A.BASE_DIR, 'templates')
    for fn in sorted(os.listdir(tpl_dir)):
        if not fn.endswith('.html'):
            continue
        with open(os.path.join(tpl_dir, fn), encoding='utf-8') as f:
            html = f.read()
        for ref in re.findall(r'(?:src|href)="(/static/[^"]+)"', html):
            if not os.path.isfile(os.path.join(A.BASE_DIR, ref.lstrip('/'))):
                missing.append(f'{fn} -> {ref}')
    check('模板引用的静态资源都存在', not missing, str(missing))

    print('\n[6] 下载接口权限')
    r = c.get('/download/ca/https-ssl-ca.crt')
    if os.path.isfile(A.CA_CRT):
        check('根证书可下载', r.status_code == 200, f'status={r.status_code}')
    else:
        print('    （尚无 CA，跳过）')
    r = c.get('/download/ca/https-ssl-ca.key')
    check('CA 私钥被拒绝（必须 403）', r.status_code == 403, f'status={r.status_code}')

    print('\n[7] 未登录访问被拦截')
    anon = A.app.test_client()
    r = anon.get('/list_certs', headers={'Accept': 'application/json'})
    check('匿名访问 /list_certs 被重定向到登录', r.status_code in (301, 302), f'status={r.status_code}')

    print('=' * 64)
    print(f'结果：{len(_passed)} 通过，{len(_failed)} 失败')
    if _failed:
        print('失败项：')
        for name in _failed:
            print('  -', name)
    return 1 if _failed else 0


if __name__ == '__main__':
    sys.exit(main())

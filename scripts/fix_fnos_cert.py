"""Align fnOS system nginx certificates with the https_ssl CA chain.

fnOS serves /usr/trim/nginx with a custom module that selects a certificate by
SNI. A client connecting to a bare IP sends no SNI, so nginx falls back to the
``fallback`` entry in network_gateway_cert.conf. That entry originally held a
self-signed ``CN=fnOS`` certificate, which no client trusts. This script copies
the https_ssl-issued certificate (leaf + CA chain, SAN including the IP) over the
configured system and fallback certificate paths, then reloads trim nginx.

Usage:
    sudo python3 fix_fnos_cert.py [cert_name] [container]
"""

import json
import os
import shutil
import subprocess
import sys
import time

CERT_CONF = '/usr/trim/etc/network_gateway_cert.conf'
TRIM_NGINX = '/usr/trim/nginx/sbin/nginx'
TARGET_HOSTS = ('fallback', 'fnos')


def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kwargs)


def docker_cat(container, path):
    return run(['docker', 'exec', container, 'cat', path]).stdout


def load_entries(conf_path):
    with open(conf_path, encoding='utf-8') as f:
        return json.load(f)


def backup(path):
    stamp = time.strftime('%Y%m%d%H%M%S')
    shutil.copy2(path, f'{path}.bak-{stamp}')


def target_dirs(entries, cert_name):
    dirs = {}
    wanted = set(TARGET_HOSTS) | {cert_name}
    for entry in entries:
        host = entry.get('host', '')
        if host in wanted:
            dirs[host] = entry
    return dirs


def apply_entry(entry, leaf_crt, leaf_key):
    crt_path = entry['cert']
    key_path = entry['key']
    if not os.path.exists(crt_path) or not os.path.exists(key_path):
        print(f'跳过（路径不存在）: {crt_path}')
        return False
    backup(crt_path)
    backup(key_path)
    shutil.copyfile(leaf_crt, crt_path)
    shutil.copyfile(leaf_key, key_path)
    # trim nginx worker 非 root，证书与私钥必须可读；沿用 fnOS 既有的 755。
    os.chmod(crt_path, 0o755)
    os.chmod(key_path, 0o755)
    print(f'已更新 {crt_path}')
    return True


def reload_trim_nginx():
    if not os.path.exists(TRIM_NGINX):
        return
    run([TRIM_NGINX, '-t'])
    pids = run(['pgrep', '-f', 'nginx: master']).stdout.split()
    if pids:
        os.kill(int(pids[0]), 1)  # SIGHUP
        print(f'已重载 trim nginx (master {pids[0]})')


def main():
    cert_name = sys.argv[1] if len(sys.argv) > 1 else 'fnos'
    container = sys.argv[2] if len(sys.argv) > 2 else 'qilin_ssl'

    if not os.path.exists(CERT_CONF):
        print(f'未找到 {CERT_CONF}，确认这是 fnOS 系统', file=sys.stderr)
        return 1

    running = run(['docker', 'ps', '--format', '{{.Names}}']).stdout.splitlines()
    if container not in running:
        print(f'容器 {container} 未运行', file=sys.stderr)
        return 1

    leaf_crt = docker_cat(container, f'/app/proxy/certs/{cert_name}.crt')
    leaf_key = docker_cat(container, f'/app/proxy/certs/{cert_name}.key')
    if 'BEGIN CERTIFICATE' not in leaf_crt:
        print(
            f'容器内 /app/proxy/certs/{cert_name}.crt 不存在或为空，请先在面板启动该反代',
            file=sys.stderr,
        )
        return 1

    tmp_dir = os.path.join('/tmp', f'https_ssl-fnos-{os.getpid()}')
    os.makedirs(tmp_dir, exist_ok=True)
    crt_tmp = os.path.join(tmp_dir, 'leaf.crt')
    key_tmp = os.path.join(tmp_dir, 'leaf.key')
    with open(crt_tmp, 'w', encoding='utf-8') as f:
        f.write(leaf_crt)
    with open(key_tmp, 'w', encoding='utf-8') as f:
        f.write(leaf_key)

    entries = load_entries(CERT_CONF)
    targets = target_dirs(entries, cert_name)
    if not targets:
        print(f'证书映射里没有 {cert_name}/fallback/fnos 条目', file=sys.stderr)
        return 1

    changed = False
    for host, entry in targets.items():
        if apply_entry(entry, crt_tmp, key_tmp):
            changed = True

    shutil.rmtree(tmp_dir, ignore_errors=True)

    if changed:
        reload_trim_nginx()
        print(f'完成：{cert_name} 的 CA 链与 fallback 证书已更新')
    else:
        print('没有可更新的证书路径')
    return 0


if __name__ == '__main__':
    sys.exit(main())

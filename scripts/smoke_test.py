#!/usr/bin/env python3
"""https_ssl 冒烟测试：在临时目录里跑一遍完整业务流程。

脚本会把 app.py / templates / static 复制到临时工作目录后导入，
因此不会读写项目里的真实 ca、certs、users.json。

用法：
    python scripts/smoke_test.py
    QILIN_OPENSSL=/usr/bin/openssl python scripts/smoke_test.py

Windows 下若 openssl 不在 PATH，可指定：
    set QILIN_OPENSSL=C:\\Program Files\\Git\\usr\\bin\\openssl.exe
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADMIN_PASSWORD = 'TestAdmin@123'

_passed, _failed = [], []


def check(name, condition, detail=''):
    (_passed if condition else _failed).append(name)
    print(f'  {"PASS" if condition else "FAIL"}  {name}' + (f'  <- {detail}' if detail and not condition else ''))


def find_openssl():
    candidate = os.environ.get('QILIN_OPENSSL')
    if candidate and os.path.isfile(candidate):
        return candidate
    found = shutil.which('openssl')
    if found:
        return found
    for path in (r'C:\Program Files\Git\usr\bin\openssl.exe',
                 r'C:\Program Files\OpenSSL\bin\openssl.exe',
                 '/usr/bin/openssl'):
        if os.path.isfile(path):
            return path
    return None


def prepare_workspace():
    """把运行所需文件复制到临时目录，返回该目录。"""
    workdir = tempfile.mkdtemp(prefix='https_ssl-smoke-')
    for item in ('app.py', 'templates', 'static'):
        src = os.path.join(PROJECT_DIR, item)
        dst = os.path.join(workdir, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns('*.bak'))
        else:
            shutil.copyfile(src, dst)
    return workdir


def self_signed_pair(workdir, openssl_cmd, name='external'):
    """生成一对与 https_ssl CA 无关的自签证书，用于测试自定义证书分支。"""
    crt = os.path.join(workdir, f'{name}.crt')
    key = os.path.join(workdir, f'{name}.key')
    subprocess.run([openssl_cmd, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', key, '-out', crt, '-days', '365',
                    '-subj', '/CN=external.local'],
                   check=True, capture_output=True)
    return crt, key


def run(workdir):
    sys.path.insert(0, workdir)
    import app as qilin  # noqa: E402  必须在设置好环境变量后导入

    client = qilin.app.test_client()

    print('\n[1] 登录')
    r = client.post('/login', data={'username': 'admin', 'password': 'wrong'})
    check('错误密码被拒绝', r.status_code == 200 and '用户名或密码错误' in r.get_data(as_text=True))
    r = client.post('/login', data={'username': 'admin', 'password': ADMIN_PASSWORD})
    check('正确密码登录成功', r.status_code == 302, f'status={r.status_code}')

    print('\n[2] 创建 CA')
    r = client.post('/create_ca', data={'org_name': '测试机构', 'password': ''})
    check('创建无密码 CA', r.status_code == 200, r.get_data(as_text=True)[:120])
    check('CA 证书已生成', os.path.isfile(qilin.CA_CRT))
    check('CA 名称正确转义', '测试机构' in r.get_data(as_text=True))
    r = client.get('/check_ca_password')
    check('CA 密码状态接口可用', r.status_code == 200 and r.get_json()['has_password'] is False)

    print('\n[3] 签发证书：名称校验')
    r = client.post('/create_cert', data={'cert_name': '../evil', 'domains': 'a.com'})
    check('路径穿越名称被拒', r.status_code == 400, f'status={r.status_code}')
    check('穿越未产生目录', not os.path.exists(os.path.join(os.path.dirname(qilin.CERTS_DIR), 'evil')))
    r = client.post('/create_cert', data={'cert_name': 'ca'})
    check('保留名 ca 被拒', r.status_code == 400)
    r = client.post('/create_cert', data={'cert_name': 'bad<script>'})
    check('含特殊字符名称被拒', r.status_code == 400)

    print('\n[4] 签发证书：SAN 注入防护')
    r = client.post('/create_cert', data={
        'cert_name': 'inject',
        'domains': 'ok.com\nbasicConstraints = critical, CA:TRUE'})
    check('SAN 换行注入被拒', r.status_code == 400, r.get_data(as_text=True)[:120])

    print('\n[5] 签发证书：中文名与正常流程')
    r = client.post('/create_cert', data={
        'cert_name': '我的证书', 'ip_addresses': '192.168.5.3;127.0.0.1',
        'domains': 'nas.local;*.nas.local'})
    check('中文名签发成功', r.status_code == 200, r.get_data(as_text=True)[:160])
    body = r.get_data(as_text=True)
    check('返回行含证书名', '我的证书' in body)
    check('返回行无脚本注入', '<script>' not in body)
    r = client.post('/create_cert', data={'cert_name': '我的证书'})
    check('重名证书被拒', r.status_code == 409, f'status={r.status_code}')

    print('\n[6] 证书列表与下载')
    r = client.get('/list_certs', headers={'Accept': 'application/json'})
    certs = r.get_json()['certs']
    names = [c['name'] for c in certs]
    check('JSON 列表含中文名证书', '我的证书' in names, str(names))
    # 列表里的有效期必须是 YYYY-MM-DD；直接吐 OpenSSL 的
    # "Feb 16 08:19:50 2036 GMT" 既难读，也和 CA 那行格式不一致。
    row = next(c for c in certs if c['name'] == '我的证书')
    check('有效期为 YYYY-MM-DD 格式',
          re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['valid_until'] or '') is not None,
          str(row.get('valid_until')))
    r = client.get('/list_certs')
    html = r.get_data(as_text=True)
    check('HTML 列表可渲染', r.status_code == 200 and '我的证书' in html)
    # CA 行与证书行都应是 YYYY-MM-DD，页面上不该再出现 OpenSSL 原始日期
    check('HTML 列表不吐 OpenSSL 原始日期', 'GMT' not in html)
    with qilin.app.test_request_context():
        quoted = qilin.url_for('download', cert_dir='我的证书', filename='我的证书.crt')
    r = client.get(quoted)
    check('中文名证书可下载', r.status_code == 200, f'status={r.status_code}')
    r = client.get('/download/ca/qilin-ca.crt')
    check('CA 证书可下载', r.status_code == 200)
    r = client.get('/download/ca/qilin-ca.key')
    check('CA 私钥被拒绝下载', r.status_code == 403, f'status={r.status_code}')

    print('\n[7] 证书验证：multipart 表单（前端真实提交方式）')
    r = client.post('/verify_cert', data={
        'address': '192.168.5.3', 'cert_type': 'qilin', 'cert_name': '我的证书'},
        content_type='multipart/form-data')
    payload = r.get_json() or {}
    check('multipart 提交可用', r.status_code == 200 and payload.get('success') is True,
          f'status={r.status_code} {payload.get("message")}')
    check('链校验通过（本机 CA）', payload.get('chain_verified') is True)
    r = client.post('/verify_cert', data={
        'address': 'other.example.com', 'cert_name': '我的证书'},
        content_type='multipart/form-data')
    payload = r.get_json() or {}
    check('地址未被覆盖时报错', payload.get('success') is False
          and '未覆盖' in (payload.get('message') or ''), payload.get('message'))
    r = client.post('/verify_cert', data={
        'address': 'nas.local', 'cert_name': '我的证书'},
        content_type='multipart/form-data')
    check('通配域名匹配成功', (r.get_json() or {}).get('success') is True)
    r = client.post('/verify_cert', data={'cert_name': '../evil'})
    check('非法证书名被拒', r.status_code == 400)

    print('\n[8] 证书验证：自定义证书分支')
    crt, key = self_signed_pair(workdir, qilin.OPENSSL_CMD)
    with open(crt, 'rb') as f1, open(key, 'rb') as f2:
        up1 = client.post('/upload', data={'file': (f1, '外部证书.crt')},
                          content_type='multipart/form-data').get_json()
        up2 = client.post('/upload', data={'file': (f2, '外部私钥.key')},
                          content_type='multipart/form-data').get_json()
    check('中文名文件上传成功', up1.get('success') is True and up2.get('success') is True,
          str(up1) + str(up2))
    r = client.post('/verify_cert', data={
        'address': 'external.local', 'cert_type': 'custom',
        'cert_filename': up1['filename'], 'key_filename': up2['filename']},
        content_type='multipart/form-data')
    payload = r.get_json() or {}
    check('外部自签证书走到链校验', payload.get('success') is False
          and '链校验失败' in (payload.get('message') or ''), payload.get('message'))
    bad = client.post('/upload', data={'file': (open(crt, 'rb'), 'x.txt')},
                      content_type='multipart/form-data')
    check('非证书扩展名被拒', bad.status_code == 400)

    print('\n[9] 反向代理：创建与校验')
    r = client.post('/create_proxy', data={
        'service_name': 'nas-panel', 'original_url': 'http://127.0.0.1:2002',
        'proxy_url': 'https://192.168.5.3:14000', 'cert_type': 'qilin',
        'cert_id': '我的证书'})
    payload = r.get_json() or {}
    check('创建反代成功', r.status_code == 200 and payload.get('success') is True,
          str(payload))
    check('返回 proxy_id', payload.get('proxy_id') == 'nas-panel')
    check('返回 cert_expiry', bool(payload.get('cert_expiry')), str(payload.get('cert_expiry')))
    check('站点配置已生成', os.path.isfile(os.path.join(qilin.PROXY_SITES_DIR, 'nas-panel.conf')))
    conf = open(os.path.join(qilin.PROXY_SITES_DIR, 'nas-panel.conf'), encoding='utf-8').read()
    check('配置指向所选证书', 'nas-panel.crt' in conf and 'listen 14000 ssl;' in conf)
    check('使用了 cert_id 指定的证书', os.path.isfile(os.path.join(qilin.PROXY_CERTS_DIR, 'nas-panel.crt')))

    r = client.post('/create_proxy', data={
        'service_name': '../../pwn', 'original_url': 'http://127.0.0.1:1',
        'proxy_url': 'https://x.local', 'cert_type': 'qilin'})
    check('非法服务名被拒', r.status_code == 400, f'status={r.status_code}')
    stored = open(qilin.PROXY_DATA_FILE, encoding='utf-8').read()
    check('非法服务名未落盘', 'pwn' not in stored)
    r = client.post('/create_proxy', data={
        'service_name': 'bad-url', 'original_url': 'http://127.0.0.1:1',
        'proxy_url': 'https://x.local"}; location / { return 200; } #',
        'cert_type': 'qilin'})
    check('非法反代地址被拒', r.status_code == 400, f'status={r.status_code}')
    check('非法地址未落盘', 'bad-url' not in open(qilin.PROXY_DATA_FILE, encoding='utf-8').read())
    r = client.post('/create_proxy', data={
        'service_name': 'no-cert', 'original_url': 'http://127.0.0.1:1',
        'proxy_url': 'https://y.local', 'cert_type': 'qilin', 'cert_id': '不存在'})
    check('证书不存在时拒绝创建', r.status_code == 500)
    check('失败后未落盘', 'no-cert' not in open(qilin.PROXY_DATA_FILE, encoding='utf-8').read())

    print('\n[10] 反向代理：编辑、启停、删除')
    r = client.post('/create_proxy', data={
        'service_name': 'nas-panel', 'original_url': 'http://127.0.0.1:2003',
        'proxy_url': 'https://192.168.5.3:14001', 'cert_type': 'qilin',
        'cert_id': '我的证书'})
    check('编辑已存在服务成功', r.status_code == 200 and r.get_json()['success'] is True)
    proxies = client.get('/get_proxy_list').get_json()['proxies']
    check('编辑后只有一条记录', len(proxies) == 1, str(len(proxies)))
    conf = open(os.path.join(qilin.PROXY_SITES_DIR, 'nas-panel.conf'), encoding='utf-8').read()
    check('站点配置已更新端口', 'listen 14001 ssl;' in conf)
    # 开发机通常没有 nginx，容器内则有：两种环境下都要给出确定行为，
    # 不能把「本机没装 nginx」写死成预期结果。
    nginx_present = bool(shutil.which('nginx')) or qilin._nginx_running()
    r = client.post('/run_proxy', json={'proxy_id': 'nas-panel'})
    body = r.get_json() or {}
    if nginx_present:
        check('run_proxy 在装有 nginx 的环境下启用成功',
              r.status_code == 200 and body.get('success') is True, str(body))
    else:
        check('run_proxy 在无 nginx 时返回明确错误',
              r.status_code == 500 and 'nginx' in body.get('message', ''), str(body))
    r = client.get('/get_proxy_pid/nas-panel')
    want = 'on' if nginx_present else 'off'
    check('状态查询可用', r.status_code == 200 and r.get_json()['status'] == want,
          str(r.get_json()))

    print('\n[11] 删除证书的联动')
    r = client.post('/delete_certs', json={'cert_names': ['我的证书']})
    payload = r.get_json() or {}
    check('删除证书成功', payload.get('deleted') == ['我的证书'], str(payload))
    check('引用的反代被自动停用', payload.get('stopped_proxies') == ['nas-panel'], str(payload))
    check('站点配置被清理', not os.path.isfile(os.path.join(qilin.PROXY_SITES_DIR, 'nas-panel.conf')))
    r = client.post('/delete_certs', json={'cert_names': ['../etc']})
    check('非法名称被跳过', (r.get_json() or {}).get('skipped') == ['../etc'])
    r = client.post('/delete_proxy', json={'proxy_ids': ['nas-panel']})
    check('删除反代成功', (r.get_json() or {}).get('success') is True)
    check('代理记录已清空', client.get('/get_proxy_list').get_json()['proxies'] == [])
    check('代理证书文件已清理',
          not os.path.isfile(os.path.join(qilin.PROXY_CERTS_DIR, 'nas-panel.crt')))

    print('\n[12] 带密码的 CA')
    r = client.post('/create_ca', data={'org_name': 'PwdCA', 'password': 'CaPass123'})
    check('创建带密码 CA', r.status_code == 200, r.get_data(as_text=True)[:120])
    check('密码状态接口反映为真', client.get('/check_ca_password').get_json()['has_password'] is True)
    r = client.post('/create_cert', data={'cert_name': 'pwd1'})
    check('缺密码时被拒', r.status_code == 400 and '密码' in r.get_data(as_text=True))
    r = client.post('/create_cert', data={'cert_name': 'pwd1', 'cert_password': 'wrong'})
    check('错误密码被识别', r.status_code == 400 and '密码错误' in r.get_data(as_text=True),
          r.get_data(as_text=True)[:120])
    check('失败未留下半成品目录', not os.path.exists(os.path.join(qilin.CERTS_DIR, 'pwd1')))
    r = client.post('/create_cert', data={'cert_name': 'pwd1', 'cert_password': 'CaPass123'})
    check('正确密码签发成功', r.status_code == 200, r.get_data(as_text=True)[:120])

    print('\n[13] 设置页')
    r = client.post('/settings', data={'username': 'admin', 'old_password': 'bad',
                                       'new_password': 'abcdef'})
    check('原密码错误被拒', '原密码错误' in r.get_data(as_text=True))
    r = client.post('/settings', data={'username': 'admin', 'old_password': ADMIN_PASSWORD,
                                       'new_password': '123'})
    check('过短新密码被拒', '至少 6 位' in r.get_data(as_text=True))
    r = client.post('/settings', data={'username': 'root', 'old_password': ADMIN_PASSWORD,
                                       'new_password': 'NewPass123'})
    check('改名成功', '用户信息更新成功' in r.get_data(as_text=True))
    check('改名后可用新密码登录',
          client.get('/logout').status_code == 302
          and client.post('/login', data={'username': 'root', 'password': 'NewPass123'}).status_code == 302)

    print('\n[14] 页面渲染与前端契约')
    for path in ('/', '/verify', '/proxy', '/tutorial', '/about', '/settings'):
        r = client.get(path)
        check(f'页面 {path} 渲染正常', r.status_code == 200, f'status={r.status_code}')
    proxy_html = client.get('/proxy').get_data(as_text=True)
    verify_html = client.get('/verify').get_data(as_text=True)
    check('proxy.html 只有一个 </body>', proxy_html.count('</body>') == 1)
    check('proxy.html 只有一个 </html>', proxy_html.count('</html>') == 1)
    for element in ('id="cert-list"', 'id="qilin-cert-select"', 'id="cert-files"',
                    'id="cert-file"', 'id="key-file"', 'data-target="cert-file"'):
        check(f'proxy.html 含 {element}', element in proxy_html)
    check('proxy.html 引入 cert_picker.js', 'cert_picker.js' in proxy_html)
    check('proxy.html 不再引用 file_upload.js', 'file_upload.js' not in proxy_html)
    check('verify.html 含证书下拉', 'id="cert-list"' in verify_html and 'id="cert-files"' in verify_html)
    check('verify.html 结果区可展示明细', 'id="verify-details"' in verify_html)
    check('verify.html 不再打开假链接', 'window.open' not in verify_html)
    check('首页不再引用 file_upload.js',
          'file_upload.js' not in client.get('/').get_data(as_text=True))

    print('\n[15] 旧数据兼容：反代记录只有 cert_type，没有 cert_id')
    picker_js = open(os.path.join(qilin.BASE_DIR, 'static/js/cert_picker.js'),
                     encoding='utf-8').read()
    check('选择器按 cert_id→证书文件名→服务名 依次兜底',
          'options.certStored' in picker_js and 'options.serviceName' in picker_js)
    check('不存在的证书名不会被硬选中（selectedIndex=-1 保护）',
          'this.value === selectedId' in picker_js)
    check('编辑弹窗把服务名传给选择器', 'serviceName: data.id' in proxy_html)
    # 后端同样回退到服务名，前端兜底值才能与服务端解析结果一致。
    # 用当前真实存在的证书名当服务名，模拟「服务名 == 证书名」的旧记录。
    certs = client.get('/list_certs',
                       headers={'Accept': 'application/json'}).get_json()['certs']
    check('存在可用于兜底测试的证书', bool(certs))
    if certs:
        legacy_name = certs[0]['name']
        r = client.post('/create_proxy', data={
            'service_name': legacy_name, 'original_url': 'http://127.0.0.1:1',
            'proxy_url': 'https://legacy.local:14002', 'cert_type': 'qilin'})
        check('缺 cert_id 时后端按服务名兜底', r.status_code == 200,
              r.get_data(as_text=True)[:160])
        check('兜底后 cert_id 等于服务名',
              (r.get_json() or {}).get('proxy', {}).get('cert_id') == legacy_name,
              str(r.get_json()))

    print('\n[16] 默认管理员密码')
    from werkzeug.security import check_password_hash
    check('默认密码常量是 admin', qilin.DEFAULT_ADMIN_PASSWORD == 'admin')
    # 未设置 QILIN_ADMIN_PASSWORD 时应回退到 admin，而不是拒绝启动。
    # 这里删掉临时目录里的 users.json 重新初始化，不影响真实数据。
    saved = os.environ.pop('QILIN_ADMIN_PASSWORD', None)
    try:
        users_file = os.path.join(qilin.BASE_DIR, 'users.json')
        if os.path.isfile(users_file):
            os.remove(users_file)
        users = qilin.load_users()
        check('未设置 QILIN_ADMIN_PASSWORD 时回退到 admin',
              check_password_hash(users['admin']['password'], 'admin'))
    finally:
        if saved is not None:
            os.environ['QILIN_ADMIN_PASSWORD'] = saved


def main():
    openssl_cmd = find_openssl()
    if not openssl_cmd:
        print('未找到 openssl，请设置 QILIN_OPENSSL 环境变量后重试')
        return 1
    workdir = prepare_workspace()
    os.environ.update(
        QILIN_OPENSSL=openssl_cmd,
        QILIN_ADMIN_PASSWORD=ADMIN_PASSWORD,
        QILIN_SECRET_KEY='smoke-test-key',
        QILIN_PROXY_DIR=os.path.join(workdir, 'proxy'),
    )
    print(f'openssl : {openssl_cmd}')
    print(f'workdir : {workdir}')
    try:
        run(workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f'\n结果：{len(_passed)} 通过，{len(_failed)} 失败')
    if _failed:
        print('失败项：')
        for name in _failed:
            print(f'  - {name}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

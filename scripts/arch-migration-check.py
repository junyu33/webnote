#!/usr/bin/env python3
"""Exercise a release in disposable loopback-only Docker projects on Arch Linux."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tarfile
import tempfile
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]


def run(args, cwd=None, check=True):
    return subprocess.run(list(map(str, args)), cwd=cwd, check=check)


def output(args, cwd=None):
    return subprocess.check_output(list(map(str, args)), cwd=cwd, text=True).strip()


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def env(path):
    return dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line)


def write_env(path, values):
    path.write_text(''.join(k + '=' + v + '\n' for k, v in values.items()))
    path.chmod(0o600)


def request(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
    try:
        c.request(method, path, body, headers or {})
        r = c.getresponse()
        return r.status, dict(r.headers), r.read()
    finally:
        c.close()


def check_file(port, info, body):
    path = '/api/files/' + info['id']
    code, _, downloaded = request(port, 'GET', path)
    assert code == 200 and downloaded == body
    status, headers, content = request(port, 'HEAD', path)
    assert status == 200 and int(headers['Content-Length']) == len(body) and content == b''
    assert json.loads(request(port, 'GET', path + '/meta')[2]) == info


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser()
    p.add_argument('--bundle', type=Path, default=ROOT / 'dist/webnote-portable.tar.gz')
    p.add_argument('--result', type=Path, required=True)
    args = p.parse_args()
    os_release = Path('/etc/os-release').read_text()
    assert 'ID=arch' in os_release
    image = output(['docker', 'info', '--format', '{{.OSType}}/{{.Architecture}}'])
    assert image in ('linux/x86_64', 'linux/amd64'), image
    suffix = secrets.token_hex(5)
    projects = ['webnote-arch-check-' + suffix + '-' + s for s in ('source', 'target')]
    stage = Path(tempfile.mkdtemp(prefix='webnote-arch-check-'))
    dirs, ports, infos = [], [free_port(), free_port()], []
    assert ports[0] != ports[1]
    result = {'host_os': 'Arch Linux', 'host_arch': 'x86_64',
              'bundle_sha256': hashlib.sha256(args.bundle.read_bytes()).hexdigest(),
              'projects': projects, 'ports': ports, 'checks': {}, 'cleanup': False}
    try:
        for index in range(2):
            parent = stage / str(index)
            parent.mkdir()
            with tarfile.open(args.bundle) as archive:
                assert not any(Path(m.name).name.startswith('.env') for m in archive.getmembers())
                archive.extractall(parent, filter='data')
            directory = parent / 'webnote-portable'
            dirs.append(directory)
            run(['sha256sum', '-c', 'SHA256SUMS'], cwd=directory)
            # Only the disposable copy is changed: no persistent restart policy.
            compose = directory / 'compose.portable.yaml'
            text = compose.read_text()
            assert text.count('restart: unless-stopped') == 1
            compose.write_text(text.replace('restart: unless-stopped', 'restart: "no"'))
            if index == 0:
                run(['./webnote.sh', 'init', 'http://127.0.0.1:' + str(ports[0])], cwd=directory)
                cfg = env(directory / '.env')
            else:
                cfg = env(dirs[0] / '.env')
            cfg['WEBNOTE_PROJECT'] = projects[index]
            cfg['WEBNOTE_BIND_PORT'] = str(ports[index])
            cfg['WEBNOTE_PUBLIC_URL'] = 'http://127.0.0.1:' + str(ports[index])
            write_env(directory / '.env', cfg)
        def cli(index, *argv, check=True):
            return run(['./webnote.sh', *argv], cwd=dirs[index], check=check)
        cli(0, 'load')
        cli(0, 'up')
        cid = output(['docker', 'compose', '--env-file', dirs[0] / '.env', '-f', dirs[0] / 'compose.portable.yaml', 'ps', '-q', 'webnote'], cwd=dirs[0])
        detail = json.loads(output(['docker', 'inspect', cid]))[0]
        assert detail['HostConfig']['RestartPolicy']['Name'] == 'no'
        assert detail['HostConfig']['ReadonlyRootfs'] is True
        assert detail['Config']['User'] == '10001:10001'
        assert detail['HostConfig']['PortBindings']['8765/tcp'][0]['HostIp'] == '127.0.0.1'
        assert next(m for m in detail['Mounts'] if m['Destination'] == '/data')['Type'] == 'volume'
        image_info = json.loads(output(['docker', 'image', 'inspect', detail['Image']]))[0]
        assert image_info['Architecture'] == 'amd64' and image_info['Os'] == 'linux'
        result['image_id'] = detail['Image']
        result['checks']['native_image_loopback_no_restart'] = True
        assert (dirs[0] / '.env').stat().st_mode & 0o777 == 0o600
        original = (dirs[0] / '.env').read_bytes()
        assert cli(0, 'init', 'http://localhost:8765', check=False).returncode != 0
        assert (dirs[0] / '.env').read_bytes() == original
        result['checks']['private_configuration_no_overwrite'] = True
        bodies = [b'', b'\xef\xbb\xbf  UTF-8 text\r\n\nlast line  \n',
                  bytes(range(256)) * 4096 + b'\x00  bytes\r\ntrailing spaces  \n']
        for index, body in enumerate(bodies):
            filename = urllib.parse.quote('migration-' + str(index) + '-文件.bin', safe='')
            status, _, raw = request(ports[0], 'POST', '/api/files', body, {'X-Filename': filename})
            assert status == 201
            info = json.loads(raw)
            assert info['sha256'] == hashlib.sha256(body).hexdigest()
            infos.append(info)
            check_file(ports[0], info, body)
        result['checks']['empty_bom_binary_bytes_head_metadata'] = True
        archive = stage / 'migration-data.tar.gz'
        cli(0, 'backup', str(archive))
        cli(0, 'down')
        cli(0, 'up')
        for info, body in zip(infos, bodies):
            check_file(ports[0], info, body)
        result['checks']['source_recreation_persistence'] = True
        cli(0, 'down')
        cli(1, 'restore', str(archive))
        cli(1, 'up')
        for info, body in zip(infos, bodies):
            check_file(ports[1], info, body)
        result['checks']['empty_target_restore_preserves_ids_bytes_metadata'] = True
        assert env(dirs[0] / '.env')['WEBNOTE_TOKEN'] == env(dirs[1] / '.env')['WEBNOTE_TOKEN']
        token = env(dirs[1] / '.env')['WEBNOTE_TOKEN']
        for info in infos:
            headers = {'X-Filename': urllib.parse.quote(info['filename'], safe=''), 'X-File-ID': info['id']}
            assert request(ports[1], 'DELETE', '/api/files/by-name', headers=headers)[0] == 401
            headers['Authorization'] = 'Bearer invalid-token'
            assert request(ports[1], 'DELETE', '/api/files/by-name', headers=headers)[0] == 401
            headers['Authorization'] = 'Bearer ' + token
            assert request(ports[1], 'DELETE', '/api/files/by-name', headers=headers)[0] == 200
            assert request(ports[1], 'GET', '/api/files/' + info['id'])[0] == 404
        result['checks']['token_preserved_deletion_auth_and_cleanup'] = True
        result['sample_bytes'] = list(map(len, bodies))
        result['success'] = True
        print('ARCH MIGRATION PASS: native archive load, loopback-only temporary service, bytes/IDs/metadata, backup/restore and token retention.')
    finally:
        clean = True
        for index, directory in reversed(list(enumerate(dirs))):
            if not (directory / '.env').exists():
                continue
            assert env(directory / '.env')['WEBNOTE_PROJECT'] == projects[index]
            down = run(['./webnote.sh', 'down'], cwd=directory, check=False)
            clean &= down.returncode == 0
            volume = projects[index] + '_data'
            exists = subprocess.run(['docker', 'volume', 'inspect', volume], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            if exists:
                clean &= run(['docker', 'volume', 'rm', volume], check=False).returncode == 0
            assert not output(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + projects[index]])
            assert not output(['docker', 'network', 'ls', '-q', '--filter', 'label=com.docker.compose.project=' + projects[index]])
        if clean:
            shutil.rmtree(stage)
        result['cleanup'] = clean
        args.result.write_text(json.dumps(result, indent=2))
        assert clean, 'Temporary resources need inspection: ' + str(stage)
        print('CLEANUP PASS: temporary containers, networks, volumes, private .env and extracted files removed.')


if __name__ == '__main__':
    main()

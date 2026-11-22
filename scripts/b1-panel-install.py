#!/usr/bin/env python3
"""Install a reviewed B1 status/menu release on the already-identified CB1."""
import datetime
import json
import os
import pwd
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request


def main():
    assert os.geteuid() == 0, 'Run installer once with sudo'
    stage = Path('/home/biqu/b1-panel-stage')
    home = Path('/home/biqu')
    state = json.load(urllib.request.urlopen(
        'http://127.0.0.1:7125/printer/objects/query?print_stats&idle_timeout&webhooks',
        timeout=5))['result']['status']
    assert state['webhooks']['state'] == 'ready'
    assert state['print_stats']['state'] not in ('printing', 'paused')
    assert state['idle_timeout']['state'] == 'Idle'
    try:
        account = pwd.getpwnam('b1-panel')
        assert account.pw_shell in ('/usr/sbin/nologin', '/bin/false'), 'Unexpected login-enabled service account'
    except KeyError:
        subprocess.run(['useradd', '--system', '--user-group', '--no-create-home',
                        '--home-dir', '/var/lib/b1-panel', '--shell',
                        '/usr/sbin/nologin', 'b1-panel'], check=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = home / '.local/state/b1-panel' / ('menu-' + stamp)
    backup.mkdir(parents=True)
    targets = {
        home / 'klipper/klippy/extras/b1_panel.py': stage / 'b1_panel.py',
        home / 'klipper/klippy/extras/b1_panel_ui.py': stage / 'b1_panel_ui.py',
        Path('/usr/local/lib/b1-panel/b1-panel-host.py'): stage / 'b1-panel-host.py',
        Path('/usr/local/lib/b1-panel/b1_panel_ui.py'): stage / 'b1_panel_ui.py',
        Path('/etc/systemd/system/b1-panel-host.service'): stage / 'b1-panel-host.service',
        Path('/etc/systemd/system/klipper.service.d/b1-panel.conf'): stage / 'b1-panel-klipper.conf',
        Path('/etc/polkit-1/rules.d/49-b1-panel.rules'): stage / '49-b1-panel.rules',
    }
    # Verify the reviewed base ordering before resetting its After directive.
    base = subprocess.check_output(['systemctl', 'cat', 'klipper'], text=True)
    assert 'After=network-online.target' in base
    for target, source in targets.items():
        assert source.is_file(), source
        if target.exists():
            destination = backup / str(target).lstrip('/')
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, destination)
    config = home / 'printer_data/config/b1-panel.cfg'
    shutil.copy2(config, backup / 'b1-panel.cfg')
    assert 'wiring_verified: True' in config.read_text()
    for target, source in targets.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o644)
    policy = Path('/etc/b1-panel.json')
    if policy.exists():
        shutil.copy2(policy, backup / 'b1-panel.json')
    settings = json.loads((stage / 'b1-panel-host.example.json').read_text())
    settings['allow_network_changes'] = False  # build/test/cancel only
    settings['serial_recovery_approved'] = False
    policy.write_text(json.dumps(settings, indent=2) + '\n')
    policy.chmod(0o644)
    text = config.read_text()
    if re.search(r'^status_menu:', text, re.M):
        text = re.sub(r'^status_menu:.*$', 'status_menu: True', text, flags=re.M)
    else:
        text += '\nstatus_menu: True\n'
    config.write_text(text)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', 'b1-panel-host.service'], check=True)
    subprocess.run(['systemctl', 'restart', 'b1-panel-host.service'], check=True)
    print('Installed status/menu; network application locked for build/test.')
    print('Backup:', backup)


if __name__ == '__main__':
    main()

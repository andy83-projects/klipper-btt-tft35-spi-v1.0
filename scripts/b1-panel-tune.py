#!/usr/bin/env python3
"""Apply the reviewed network unlock and faster display release on CB1."""
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import urllib.request


def main():
    assert os.geteuid() == 0, 'One-time deployment requires sudo'
    status = json.load(urllib.request.urlopen(
        'http://127.0.0.1:7125/printer/objects/query?webhooks&print_stats&idle_timeout',
        timeout=5))['result']['status']
    assert status['webhooks']['state'] == 'ready'
    assert status['print_stats']['state'] == 'standby'
    assert status['idle_timeout']['state'] == 'Idle'
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10)
        client.connect('/run/b1-panel/host.sock')
        client.sendall(b'{"action":"snapshot"}\n')
        response = b''
        while b'\n' not in response:
            response += client.recv(65536)
        assert not json.loads(response)['checkpoint'], 'Finish pending network change first'
    stage = Path('/home/biqu/b1-panel-stage')
    home = Path('/home/biqu')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = home / '.local/state/b1-panel' / ('unlock-speed-' + stamp)
    backup.mkdir(parents=True)
    targets = {
        home / 'klipper/klippy/extras/b1_panel.py': stage / 'b1_panel.py',
        home / 'klipper/klippy/extras/b1_panel_ui.py': stage / 'b1_panel_ui.py',
        Path('/usr/local/lib/b1-panel/b1-panel-host.py'): stage / 'b1-panel-host.py',
        Path('/usr/local/lib/b1-panel/b1_panel_ui.py'): stage / 'b1_panel_ui.py',
    }
    policy = Path('/etc/b1-panel.json')
    config = home / 'printer_data/config/b1-panel.cfg'
    for path in list(targets) + [policy, config]:
        if path.exists():
            destination = backup / str(path).lstrip('/')
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    for target, source in targets.items():
        shutil.copyfile(source, target)
        target.chmod(0o644)
    settings = json.loads(policy.read_text())
    settings['allow_network_changes'] = True
    # Owner explicitly chose serial recovery later if needed. Keep the NM
    # checkpoint/physical confirmation while removing this preparation gate.
    settings['require_recovery'] = False
    policy.write_text(json.dumps(settings, indent=2) + '\n')
    config.write_text(re.sub(r'^spi_speed:.*$', 'spi_speed: 2000000',
                             config.read_text(), flags=re.M))
    subprocess.run(['systemctl', 'restart', 'b1-panel-host.service'], check=True)
    subprocess.run(['systemctl', 'restart', 'klipper.service'], check=True)
    print('Enabled network application; recovery gate deferred by owner.')
    print('Display: 2 MHz SPI, rectangle streaming, 32 idle packets per 10 ms.')
    print('No network connection was changed. Backup:', backup)


if __name__ == '__main__':
    main()

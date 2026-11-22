#!/usr/bin/env python3
"""Install an incremental panel update (extension, UI, helper), preserving host policy/config."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

from b1_panel_plugin import LIBRARY, ensure_links


def status():
    url = ('http://127.0.0.1:7125/printer/objects/query?webhooks&print_stats'
           '&pause_resume&idle_timeout&extruder&heater_bed')
    return json.load(urllib.request.urlopen(url, timeout=4))['result']['status']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_file(source, target):
    temporary = target.with_name(target.name + '.filament-update')
    shutil.copyfile(source, temporary)
    temporary.chmod(0o644)
    os.replace(temporary, target)


def restart():
    subprocess.run(['systemctl', 'restart', 'b1-panel-host.service', 'klipper.service'],
                   check=True, timeout=30)


def wait_ready():
    for _ in range(30):
        try:
            if status()['webhooks']['state'] == 'ready':
                subprocess.run(['systemctl', 'is-active', '--quiet', 'b1-panel-host.service'],
                               check=True, timeout=5)
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError('Klipper/helper did not become ready after update')


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run this installer with sudo on the CB1')
    stage = Path(__file__).resolve().parent
    # Plugin layout: one root-owned copy, symlinked into klippy/extras.
    targets = {
        LIBRARY / 'b1_panel.py': stage / 'klippy/extras/b1_panel.py',
        LIBRARY / 'b1_panel_ui.py': stage / 'klippy/extras/b1_panel_ui.py',
        LIBRARY / 'b1-panel-host.py': stage / 'scripts/b1-panel-host.py',
    }
    expected = json.loads((stage / 'previous-sha256.json').read_text())
    for target, source in targets.items():
        compile(source.read_text(), str(source), 'exec')
        if digest(target) != expected[str(target)]:
            raise SystemExit('Installed file changed since review; stopping: ' + str(target))
    current = status()
    if (current['webhooks']['state'] != 'ready'
            or current['print_stats']['state'] in ('printing', 'paused')
            or current['pause_resume']['is_paused']
            or current['idle_timeout']['state'] not in ('Idle', 'Ready')
            or any(current[h]['target'] > 0 for h in ('extruder', 'heater_bed'))):
        raise SystemExit('Wait until printer is idle, unpaused and heater targets are off')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path('/home/biqu/.local/state/b1-panel') / ('filament-' + stamp)
    for target in targets:
        saved = backup / str(target).lstrip('/')
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, saved)
    print('Backup:', backup, flush=True)
    try:
        for target, source in targets.items():
            install_file(source, target)
        ensure_links()
        restart()
        wait_ready()
        for target, source in targets.items():
            if digest(target) != digest(source):
                raise RuntimeError('Installed file verification failed: ' + str(target))
    except Exception:
        for target in targets:
            install_file(backup / str(target).lstrip('/'), target)
        restart()
        raise
    print('Installed panel update; Klipper and panel helper ready.')
    print('No motion, heating, print start or resume was executed by this installer.')


if __name__ == '__main__':
    main()

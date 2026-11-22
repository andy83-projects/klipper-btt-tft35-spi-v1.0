#!/usr/bin/env python3
"""Keep the B1 panel outside the Klipper git checkout, like other plugins.

The extension lives root-owned in /usr/local/lib/b1-panel next to the host
helper. klippy/extras only holds symlinks, and those (plus other plugin
symlinks such as klipper_tmc_autotune) are listed in .git/info/exclude so
Moonraker never reports the Klipper repo as dirty because of them, and
Klipper updates never overwrite the panel.

Run with sudo on the CB1 when idle: converts an older copied-in install,
restarts Klipper and rolls back if Klipper or the panel does not come up.
"""
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

KLIPPER = Path('/home/biqu/klipper')
EXTRAS = KLIPPER / 'klippy/extras'
LIBRARY = Path('/usr/local/lib/b1-panel')
PANEL_FILES = ('b1_panel.py', 'b1_panel_ui.py')
# Untracked plugin links that belong to other add-ons; excluded, never changed.
OTHER_PLUGIN_LINKS = ('autotune_tmc.py', 'motor_constants.py', 'motor_database.cfg')
EXCLUDE_HEADER = '# Plugins kept outside this checkout (B1 panel installer)'


def status():
    url = ('http://127.0.0.1:7125/printer/objects/query?webhooks&print_stats'
           '&pause_resume&idle_timeout&extruder&heater_bed&b1_panel')
    return json.load(urllib.request.urlopen(url, timeout=4))['result']['status']


def require_idle():
    current = status()
    if (current['webhooks']['state'] != 'ready'
            or current['print_stats']['state'] in ('printing', 'paused')
            or current['pause_resume']['is_paused']
            or current['idle_timeout']['state'] not in ('Idle', 'Ready')
            or any(current[h]['target'] > 0 for h in ('extruder', 'heater_bed'))):
        raise SystemExit('Wait until printer is idle, unpaused and heater targets are off')


def wait_ready():
    for _ in range(40):
        try:
            current = status()
            if current['webhooks']['state'] == 'ready' and current['b1_panel'].get('initialized'):
                subprocess.run(['systemctl', 'is-active', '--quiet', 'b1-panel-host.service'],
                               check=True, timeout=5)
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError('Klipper/panel did not become ready')


def restart():
    subprocess.run(['systemctl', 'restart', 'b1-panel-host.service', 'klipper.service'],
                   check=True, timeout=30)


def exclude_entries():
    names = PANEL_FILES + tuple(n for n in OTHER_PLUGIN_LINKS
                                if (EXTRAS / n).is_symlink())
    return ['/klippy/extras/' + n for n in names]


def ensure_excluded():
    exclude = KLIPPER / '.git/info/exclude'
    text = exclude.read_text() if exclude.exists() else ''
    missing = [e for e in exclude_entries() if e not in text.splitlines()]
    if missing:
        if EXCLUDE_HEADER not in text:
            text = text.rstrip('\n') + '\n' + EXCLUDE_HEADER + '\n'
        exclude.write_text(text + '\n'.join(missing) + '\n')


def ensure_links():
    """Point klippy/extras at the library copy; safe to repeat."""
    for name in PANEL_FILES:
        link, source = EXTRAS / name, LIBRARY / name
        if not source.is_file():
            raise RuntimeError('Missing installed panel file: %s' % source)
        if link.is_symlink() and os.readlink(link) == str(source):
            continue
        temporary = link.with_name(name + '.b1-link')
        if temporary.is_symlink() or temporary.exists():
            temporary.unlink()
        temporary.symlink_to(source)
        os.replace(temporary, link)
    ensure_excluded()


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo on the CB1')
    require_idle()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path('/home/biqu/.local/state/b1-panel') / ('plugin-layout-' + stamp)
    backup.mkdir(parents=True)
    exclude = KLIPPER / '.git/info/exclude'
    shutil.copy2(exclude, backup / 'exclude')
    saved = {}
    for name in PANEL_FILES:
        path = EXTRAS / name
        if path.is_symlink():
            saved[name] = ('link', os.readlink(path))
        else:
            shutil.copy2(path, backup / name)
            saved[name] = ('file', None)
    library_existed = (LIBRARY / 'b1_panel.py').exists()
    # Earlier installs copied a duplicate of klippy/mcu.py into extras; it is
    # never loaded ([mcu] objects exist first). Keep it in the backup only.
    stray = EXTRAS / 'mcu.py'
    stray_moved = False
    if (stray.is_file() and not stray.is_symlink()
            and stray.read_bytes() == (KLIPPER / 'klippy/mcu.py').read_bytes()):
        shutil.move(str(stray), str(backup / 'extras-mcu.py'))
        stray_moved = True
    print('Backup:', backup, flush=True)
    try:
        if not library_existed:
            shutil.copyfile(EXTRAS / 'b1_panel.py', LIBRARY / 'b1_panel.py')
            (LIBRARY / 'b1_panel.py').chmod(0o644)
        ensure_links()
        restart()
        wait_ready()
    except Exception:
        for name, (kind, target) in saved.items():
            path = EXTRAS / name
            if path.is_symlink() or path.exists():
                path.unlink()
            if kind == 'link':
                path.symlink_to(target)
            else:
                shutil.copy2(backup / name, path)
        shutil.copy2(backup / 'exclude', exclude)
        if stray_moved:
            shutil.move(str(backup / 'extras-mcu.py'), str(stray))
        if not library_existed and (LIBRARY / 'b1_panel.py').exists():
            (LIBRARY / 'b1_panel.py').unlink()
        restart()
        raise
    left = subprocess.run(['git', '-C', str(KLIPPER), 'status', '--porcelain', '--',
                           'klippy/extras'], capture_output=True, text=True).stdout
    print('Panel now runs from', LIBRARY, 'via symlinks; Klipper ready.')
    print('Remaining git changes under klippy/extras:', left.strip() or 'none')


if __name__ == '__main__':
    main()

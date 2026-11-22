#!/usr/bin/env python3
"""Local unprivileged panel helper using scoped PolicyKit grants; no secret logs."""
import argparse
import grp
import ipaddress
import json
import math
import os
from pathlib import Path
import pwd
import socket
import struct
import time
import urllib.request
import uuid

NM = 'org.freedesktop.NetworkManager'
BASE = '/org/freedesktop/NetworkManager'
PROPS = 'org.freedesktop.DBus.Properties'


class Host:
    def __init__(self, config):
        import dbus
        self.dbus = dbus
        self.bus = dbus.SystemBus()
        self.nm = self.interface(BASE, NM)
        self.settings = self.interface(BASE + '/Settings', NM + '.Settings')
        self.config = config
        self.device = self.nm.GetDeviceByIpIface(config.get('interface', 'wlan0'))
        self.pending = None
        self.preferences = Path('/var/lib/b1-panel/preferences.json')
        self.brightness = 100
        if self.preferences.exists():
            self.brightness = int(json.loads(self.preferences.read_text()).get('brightness', 100))

    def interface(self, path, name):
        return self.dbus.Interface(self.bus.get_object(NM, path), name)

    def properties(self, path, name):
        return self.interface(path, PROPS).GetAll(name)

    def saved(self, ssid):
        for path in self.settings.ListConnections():
            settings = self.interface(path, NM + '.Settings.Connection').GetSettings()
            if bytes(settings.get('802-11-wireless', {}).get('ssid', [])) == ssid.encode('utf-8'):
                return path, settings
        return None, None

    def networks(self):
        wifi = self.interface(self.device, NM + '.Device.Wireless')
        saved = set()
        for path in self.settings.ListConnections():
            s = self.interface(path, NM + '.Settings.Connection').GetSettings()
            saved.add(bytes(s.get('802-11-wireless', {}).get('ssid', [])))
        networks = {}
        for path in wifi.GetAllAccessPoints():
            p = self.properties(path, NM + '.AccessPoint')
            raw = bytes(p['Ssid'])
            try:
                ssid = raw.decode('utf-8')
            except UnicodeDecodeError:
                continue
            if not ssid:
                continue
            wpa = int(p.get('WpaFlags', 0)) | int(p.get('RsnFlags', 0))
            security = ('wpa-psk' if wpa & 0x100 else 'sae' if wpa & 0x400
                        else 'unsupported' if wpa or int(p.get('Flags', 0)) & 1 else 'open')
            entry = dict(ssid=ssid, signal=int(p['Strength']), security=security,
                         saved=raw in saved)
            if ssid not in networks or networks[ssid]['signal'] < entry['signal']:
                networks[ssid] = entry
        return sorted(networks.values(), key=lambda n: (-n['signal'], n['ssid']))[:32]

    def ethernet_recovery(self):
        for path in self.nm.GetDevices():
            p = self.properties(path, NM + '.Device')
            if int(p.get('DeviceType', 0)) == 1 and int(p.get('State', 0)) == 100:
                ip = p.get('Ip4Config', '/')
                if str(ip) != '/' and self.properties(ip, NM + '.IP4Config').get('AddressData'):
                    return True
        return False

    def changes_allowed(self):
        # Serial recovery approval is a deliberate per-installation setting.
        return bool(self.config.get('allow_network_changes', False) and (
            not self.config.get('require_recovery', True) or
            self.ethernet_recovery() or self.config.get('serial_recovery_approved', False)))

    def snapshot(self):
        p = self.properties(self.device, NM + '.Device')
        w = self.properties(self.device, NM + '.Device.Wireless')
        ip, gateway, dns, ssid = '', '0.0.0.0', '1.1.1.1', ''
        prefix = 24
        if str(p.get('Ip4Config', '/')) != '/':
            data = self.properties(p['Ip4Config'], NM + '.IP4Config')
            addresses = data.get('AddressData', [])
            if addresses:
                ip, prefix = str(addresses[0]['address']), int(addresses[0]['prefix'])
            gateway = str(data.get('Gateway', '')) or '0.0.0.0'
            dnsdata = data.get('NameserverData', [])
            if dnsdata:
                dns = str(dnsdata[0]['address'])
        if str(w.get('ActiveAccessPoint', '/')) != '/':
            ssid = bytes(self.properties(w['ActiveAccessPoint'], NM + '.AccessPoint')['Ssid']).decode('utf-8', 'replace')
        method = 'auto'
        if str(p.get('ActiveConnection', '/')) != '/':
            active = self.properties(p['ActiveConnection'], NM + '.Connection.Active')
            settings = self.interface(active['Connection'], NM + '.Settings.Connection').GetSettings()
            method = str(settings.get('ipv4', {}).get('method', 'auto'))
        if self.pending:
            checkpoints = self.properties(BASE, NM)['Checkpoints']
            if self.pending['checkpoint'] not in checkpoints:
                self.pending = None
        try:
            printer = self.printer_snapshot()
        except Exception:
            printer = {}
        return dict(ip=ip, ssid=ssid, state=int(p['State']), brightness=self.brightness,
                    ipv4=dict(method=method, address=ip or '192.168.1.100',
                              prefix=prefix, gateway=gateway, dns=dns),
                    checkpoint=bool(self.pending), changes_allowed=self.changes_allowed(),
                    networks=self.networks(), printer=printer)

    def printer_snapshot(self):
        url = ('http://127.0.0.1:7125/printer/objects/query?print_stats&idle_timeout'
               '&extruder&heater_bed&toolhead&fan&configfile&pause_resume')
        status = json.load(urllib.request.urlopen(url, timeout=4))['result']['status']
        cfg = status['configfile']['config']
        return dict(state=status['print_stats']['state'],
                    paused=status['pause_resume']['is_paused'],
                    busy=status['idle_timeout']['state'] == 'Printing',
                    homed=status['toolhead']['homed_axes'],
                    position=status['toolhead']['position'][:3],
                    minimum=status['toolhead']['axis_minimum'][:3],
                    maximum=status['toolhead']['axis_maximum'][:3],
                    nozzle_max=max(0, int(float(cfg['extruder']['max_temp'])) - 5),
                    nozzle_min=float(cfg['extruder'].get('min_extrude_temp', 170)),
                    nozzle_temperature=status['extruder']['temperature'],
                    can_extrude=status['extruder']['can_extrude'],
                    extrude_limit=float(cfg['extruder'].get('max_extrude_only_distance', 50)),
                    bed_max=max(0, int(float(cfg['heater_bed']['max_temp'])) - 5),
                    nozzle_target=status['extruder']['target'],
                    bed_target=status['heater_bed']['target'],
                    fan_target=round(status['fan']['speed'] * 100))

    def printer_action(self, request):
        data = self.printer_snapshot()
        command = build_printer_command(request, data)
        endpoint = '/printer/emergency_stop' if request['action'] == 'emergency' else '/printer/gcode/script'
        body = {} if request['action'] == 'emergency' else {'script': command}
        req = urllib.request.Request('http://127.0.0.1:7125' + endpoint,
            data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        json.load(urllib.request.urlopen(req,
            timeout=30 if request['action'] in ('load', 'unload') else 10))

    def require_idle(self, cool=False):
        url = ('http://127.0.0.1:7125/printer/objects/query?print_stats&idle_timeout'
               '&extruder&heater_bed')
        data = json.load(urllib.request.urlopen(url, timeout=4))['result']['status']
        if data['print_stats']['state'] in ('printing', 'paused') or data['idle_timeout']['state'] == 'Printing':
            raise ValueError('Printer must be idle')
        if cool and any(data[h]['target'] > 0 or data[h]['temperature'] > limit
                        for h, limit in [('extruder', 50), ('heater_bed', 60)]):
            raise ValueError('Turn heaters off; wait to cool')

    def apply(self, request):
        if not self.changes_allowed():
            raise ValueError('Network changes locked: need recovery')
        self.require_idle()
        if self.pending:
            raise ValueError('Confirm or roll back previous change')
        # Only known SSIDs from NetworkManager; no shell interpretation.
        ssid = request.get('ssid', '')
        available = {n['ssid']: n for n in self.networks()}
        if ssid not in available:
            raise ValueError('Wi-Fi network is no longer visible')
        from b1_panel_ui import validate_ipv4
        ipv4 = request['ipv4']
        validate_ipv4(ipv4)
        dbus = self.dbus
        oldpath, settings = self.saved(ssid)
        secret = request.get('password')
        if secret is None:
            if oldpath is None:
                raise ValueError('No saved profile; enter password')
            # Secrets stay solely in this process and NetworkManager.
            connection = self.interface(oldpath, NM + '.Settings.Connection')
            if '802-11-wireless-security' in settings:
                secrets = connection.GetSecrets('802-11-wireless-security')
                for section, values in secrets.items():
                    settings.setdefault(section, {}).update(values)
        else:
            settings = {'connection': {}, '802-11-wireless': {
                'ssid': dbus.ByteArray(ssid.encode()), 'mode': 'infrastructure'}}
            security = available[ssid]['security']
            if security != 'open':
                if security not in ('wpa-psk', 'sae'):
                    raise ValueError('Enterprise/WEP network unsupported')
                if not (isinstance(secret, str) and (8 <= len(secret) <= 63 or (
                        len(secret) == 64 and all(c in '0123456789abcdefABCDEF' for c in secret)))):
                    raise ValueError('Invalid WPA password length')
                settings['802-11-wireless-security'] = {'key-mgmt': security, 'psk': secret}
        settings['connection'].update(id='B1 panel: ' + ssid,
                                      uuid=str(uuid.uuid4()), autoconnect=False,
                                      **{'interface-name': self.config.get('interface', 'wlan0'),
                                         'autoconnect-priority': self.dbus.Int32(10),
                                         'type': '802-11-wireless'})
        settings['ipv4'] = {'method': ipv4['method']}
        if ipv4['method'] == 'manual':
            settings['ipv4'].update({
                'address-data': dbus.Array([dbus.Dictionary({
                    'address': ipv4['address'], 'prefix': dbus.UInt32(ipv4['prefix'])}, signature='sv')], signature='a{sv}'),
                'gateway': '' if ipv4['gateway'] == '0.0.0.0' else ipv4['gateway'],
                'dns': dbus.Array([dbus.UInt32(struct.unpack('=I', socket.inet_aton(ipv4['dns']))[0])], signature='u')})
        settings.setdefault('ipv6', {'method': 'auto'})
        typed = dbus.Dictionary({k: dbus.Dictionary(v, signature='sv')
                                 for k, v in settings.items()}, signature='sa{sv}')
        # 0x2 removes newly created connections on rollback. Existing profiles
        # are never changed. New credentials are not saved until physical Keep.
        checkpoint = self.nm.CheckpointCreate(dbus.Array([self.device], signature='o'),
                                              dbus.UInt32(90), dbus.UInt32(2))
        try:
            path = self.settings.AddConnectionUnsaved(typed)
            self.nm.ActivateConnection(path, self.device, dbus.ObjectPath('/'))
            self.pending = dict(checkpoint=checkpoint, profile=path)
        except Exception:
            self.nm.CheckpointRollback(checkpoint)
            raise ValueError('Connection failed; previous settings restored') from None

    def dispatch(self, request):
        action = request.get('action')
        if action == 'permissions':
            login = self.dbus.Interface(self.bus.get_object('org.freedesktop.login1',
                '/org/freedesktop/login1'), 'org.freedesktop.login1.Manager')
            return dict(ok=True, network={str(k): str(v) for k, v in self.nm.GetPermissions().items()},
                        poweroff=str(login.CanPowerOff()))
        if action == 'snapshot':
            return dict(ok=True, **self.snapshot())
        if action == 'scan':
            try:
                before = self.properties(self.device, NM + '.Device.Wireless').get('LastScan')
                self.interface(self.device, NM + '.Device.Wireless').RequestScan(
                    self.dbus.Dictionary({}, signature='sv'))
                for _ in range(24):
                    time.sleep(.25)
                    after = self.properties(self.device, NM + '.Device.Wireless').get('LastScan')
                    if before != after:
                        break
            except Exception:
                pass  # NetworkManager may throttle scans; retain cached results.
            return dict(ok=True, networks=self.networks())
        if action == 'apply':
            self.apply(request)
        elif action in ('keep', 'rollback'):
            if not self.pending:
                raise ValueError('Rollback timer expired')
            if action == 'keep':
                if self.snapshot()['state'] != 100:
                    raise ValueError('Connection is not ready')
                connection = self.interface(self.pending['profile'], NM + '.Settings.Connection')
                settings = connection.GetSettings()
                # Preserve in-memory secrets while saving the new profile.
                if '802-11-wireless-security' in settings:
                    for section, values in connection.GetSecrets('802-11-wireless-security').items():
                        settings.setdefault(section, {}).update(values)
                settings['connection']['autoconnect'] = True
                connection.UpdateUnsaved(settings)
                connection.Save()
                self.nm.CheckpointDestroy(self.pending['checkpoint'])
            else:
                self.nm.CheckpointRollback(self.pending['checkpoint'])
            self.pending = None
        elif action == 'brightness':
            value = request.get('value')
            if not isinstance(value, int) or not 10 <= value <= 100:
                raise ValueError('Invalid text brightness')
            self.brightness = value
            self.preferences.parent.mkdir(parents=True, exist_ok=True)
            temp = self.preferences.with_suffix('.new')
            temp.write_text(json.dumps(dict(brightness=value)))
            temp.chmod(0o600)
            os.replace(temp, self.preferences)
        elif action == 'poweroff':
            self.require_idle(cool=True)
            # Called only after the shutdown message has completely flushed.
            login = self.dbus.Interface(self.bus.get_object('org.freedesktop.login1',
                '/org/freedesktop/login1'), 'org.freedesktop.login1.Manager')
            login.PowerOff(False)
        elif action in ('jog', 'home', 'pause', 'resume', 'cancel', 'heater',
                        'fan', 'motors_off', 'emergency', 'load', 'unload',
                        'filament_heat', 'filament_stop'):
            self.printer_action(request)
        else:
            raise ValueError('Unknown panel action')
        return dict(ok=True)


def build_printer_command(request, data):
    """Validate a physical menu action; pure function for no-motion testing."""
    action = request['action']
    if action in ('jog', 'home', 'motors_off'):
        if data['state'] in ('printing', 'paused') or data['busy']:
            raise ValueError('Motion controls require idle printer')
    if action == 'jog':
        axis, distance = request.get('axis'), float(request.get('distance', 0))
        if axis not in ('X', 'Y', 'Z') or not math.isfinite(distance) or not 0 < abs(distance) <= 50:
            raise ValueError('Invalid jog request')
        if axis.lower() not in data['homed']:
            raise ValueError('Home this axis first')
        index = 'XYZ'.index(axis)
        destination = data['position'][index] + distance
        if not data['minimum'][index] <= destination <= data['maximum'][index]:
            raise ValueError('Jog exceeds configured axis limits')
        feedrate = 300 if axis == 'Z' else 3000
        return ('SAVE_GCODE_STATE NAME=BTT_PANEL_JOG\nG91\nG1 %s%.3f F%d\n'
                'RESTORE_GCODE_STATE NAME=BTT_PANEL_JOG' % (axis, distance, feedrate))
    if action == 'home':
        axes = request.get('axes')
        if axes not in ('XY', 'Z', 'XYZ'):
            raise ValueError('Invalid homing request')
        if axes == 'Z' and not all(axis in data['homed'] for axis in 'xy'):
            raise ValueError('Home XY before homing Z')
        return 'G28' if axes == 'XYZ' else 'G28 ' + ' '.join(axes)
    if action == 'motors_off':
        return 'M84'
    if action in ('pause', 'resume', 'cancel'):
        permitted = {'pause': ('printing',), 'resume': ('paused',),
                     'cancel': ('printing', 'paused')}
        paused = data.get('paused', data['state'] == 'paused')
        if (action == 'resume' and not paused) or (
                action == 'pause' and paused) or (
                action != 'resume' and data['state'] not in permitted[action]):
            raise ValueError('Action does not match print state')
        return {'pause': 'PAUSE', 'resume': 'RESUME', 'cancel': 'CANCEL_PRINT'}[action]
    if action in ('load', 'unload'):
        paused = data.get('paused', data['state'] == 'paused')
        if not paused and (data['state'] in ('printing', 'paused') or data['busy']):
            raise ValueError('Pause print before changing filament')
        length, temperature = request.get('length'), request.get('temperature')
        if (not isinstance(length, (int, float)) or not math.isfinite(length)
                or not 5 <= length <= 50):
            raise ValueError('Filament length must be 5-50 mm')
        if (not isinstance(temperature, (int, float)) or not math.isfinite(temperature)
                or not data['nozzle_min'] <= temperature <= data['nozzle_max']):
            raise ValueError('Invalid filament temperature')
        if (not data['can_extrude'] or data['nozzle_temperature'] < temperature - 5
                or data['nozzle_target'] < temperature):
            raise ValueError('Heat nozzle first; wait for temp')
        limit = min(50., data['extrude_limit'])
        if not math.isfinite(limit) or limit < .1:
            raise ValueError('Invalid extrusion distance limit')
        # Extruder-only moves preserve both PAUSE_STATE and the caller's
        # positioning/extrusion modes. No homing, XY/Z move or automatic RESUME.
        moves = ['SAVE_GCODE_STATE NAME=BTT_PANEL_FILAMENT', 'M83']
        remaining = length
        while remaining > .000001:
            step = min(remaining, limit)
            moves.append('G1 E%.3f F%d' % (
                step if action == 'load' else -step, 300 if action == 'load' else 600))
            remaining -= step
        moves.append('RESTORE_GCODE_STATE NAME=BTT_PANEL_FILAMENT')
        moves.append('M400')
        return '\n'.join(moves)
    if action in ('filament_heat', 'filament_stop'):
        paused = data.get('paused', data['state'] == 'paused')
        if not paused and (data['state'] in ('printing', 'paused')
                           or (action == 'filament_heat' and data['busy'])):
            raise ValueError('Pause print before changing filament')
        target = request.get('target')
        minimum = data['nozzle_min'] if action == 'filament_heat' else 0
        if (not isinstance(target, (int, float)) or not math.isfinite(target)
                or not minimum <= target <= data['nozzle_max']):
            raise ValueError('Invalid filament temperature')
        # Nonblocking heat: the UI polls actual temperature before sending a
        # separately revalidated feed. No long M109 request or hidden retry.
        return 'M104 S%.1f' % target
    if action == 'heater':
        heater, target = request.get('heater'), request.get('target')
        if heater == 'all' and target == 0:
            return 'TURN_OFF_HEATERS'
        if heater not in ('nozzle', 'bed') or not isinstance(target, (int, float)) or not math.isfinite(target):
            raise ValueError('Invalid heater request')
        if not 0 <= target <= data[heater + '_max']:
            raise ValueError('Target exceeds configured heater limit')
        return '%s S%.1f' % ('M104' if heater == 'nozzle' else 'M140', target)
    if action == 'fan':
        percent = request.get('percent')
        if not isinstance(percent, (int, float)) or not math.isfinite(percent) or not 0 <= percent <= 100:
            raise ValueError('Invalid fan percentage')
        return 'M106 S%d' % round(percent * 255 / 100)
    if action == 'emergency':
        return 'M112'
    raise ValueError('Unknown printer action')


def serve(config):
    username = config.get('user', 'biqu')
    uid = pwd.getpwnam(username).pw_uid
    gid = grp.getgrnam(config.get('group', username)).gr_gid
    path = Path('/run/b1-panel/host.sock')
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o750)
    if path.exists():
        path.unlink()
    host = Host(config)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path)); os.chmod(path, 0o660)
        server.listen(4)
        while True:
            client, _ = server.accept()
            with client:
                client.settimeout(4)
                peer = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET,
                                            socket.SO_PEERCRED, 12))[1]
                if peer not in (0, uid):
                    continue
                try:
                    data = b''
                    while b'\n' not in data and len(data) <= 8192:
                        block = client.recv(4096)
                        if not block:
                            break
                        data += block
                    if len(data) > 8192 or b'\n' not in data:
                        raise ValueError('Invalid panel request')
                    response = host.dispatch(json.loads(data.split(b'\n')[0]))
                except ValueError as exc:
                    response = dict(ok=False, error=str(exc))
                except Exception:
                    # Never log requests, secrets, or D-Bus exceptions.
                    response = dict(ok=False, error='Host action unavailable; try again')
                try:
                    client.sendall(json.dumps(response).encode() + b'\n')
                except OSError:
                    pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/b1-panel.json')
    args = parser.parse_args()
    serve(json.loads(Path(args.config).read_text()))

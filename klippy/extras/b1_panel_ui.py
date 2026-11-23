# Encoder-driven B1 panel UI. No hardware, networking or password logging here.
# Distributed under the GNU GPLv3 license.
import ipaddress
import socket
import string
import time


def duration(seconds):
    if seconds is None:
        return '--:--'
    minutes = max(0, int(seconds)) // 60
    return '%dh%02dm' % divmod(minutes, 60)


def status_lines(snapshot, network, footer='Click: Menu', runout=False):
    stats = snapshot.get('print_stats', {})
    state = stats.get('state', 'standby')
    if snapshot.get('pause_resume', {}).get('is_paused'):
        state = 'paused'
    hot = snapshot.get('extruder', {})
    bed = snapshot.get('heater_bed', {})
    warming = any(h.get('target', 0) > h.get('temperature', 0) + 3
                  for h in (hot, bed))
    label = {'printing': 'Printing', 'complete': 'Complete',
             'paused': 'Paused', 'error': 'Error', 'cancelled': 'Cancelled'}
    label = label.get(state, 'Idle')
    if warming and state not in ('paused', 'error', 'cancelled', 'complete'):
        label = 'Warming'
    elapsed = max(0., stats.get('print_duration', 0.))
    progress = snapshot.get('display_status', {}).get('progress', 0.) or 0.
    remaining = None
    if state == 'complete':
        remaining = 0.
    elif state == 'printing' and elapsed >= 30 and .01 <= progress < 1:
        remaining = elapsed * (1. - progress) / progress
    fans = []
    for key in ('fan', 'heater_fan heatbreak_fan1', 'temperature_fan SoC_fan'):
        fans.append(round(snapshot.get(key, {}).get('speed', 0.) * 100))
    address = network.get('ip', '')
    if runout:
        label = 'Out of filament'
    return [label,
            'Print %s Left%s%s' % (duration(elapsed),
                                  '~' if remaining else ' ', duration(remaining)),
            'Nozzle %d/%d Bed %d/%d C' % (
                round(hot.get('temperature', 0)), round(hot.get('target', 0)),
                round(bed.get('temperature', 0)), round(bed.get('target', 0))),
            'Fans P:%d%% H:%d%% C:%d%%' % tuple(fans),
            'Load new filament,' if runout else 'IP ' + (address or 'Not connected'),
            'then choose Resume' if runout else
            ('http://' + address + ':81/') if address else 'Fluidd: waiting for IP',
            '%d%% %s' % (min(100, max(0, int(progress * 100))),
                         stats.get('filename', '').rsplit('/', 1)[-1]),
            footer]


def file_label(path):
    name = path.rsplit('/', 1)[-1]
    for suffix in ('.gcode', '.gco', '.g'):
        if name.lower().endswith(suffix):
            return name[:-len(suffix)]
    return name


class PanelUI:
    # All password characters are entered by rotation, never by G-code.
    CHARACTERS = string.ascii_lowercase + string.ascii_uppercase + string.digits + string.punctuation + ' '
    PASSWORD_KEYS = list(CHARACTERS) + ['<DELETE>', '<DONE>', '<BACK>']
    # Preheat presets match the installed Orca filament profiles' first layer.
    PRESETS = (('PLA', 210, 60), ('PETG', 220, 65))
    Z_STEP = .025
    Z_LIMIT = 1.
    BED_COOL = 35
    STATUS_SHORT = {'Load filament': 'Load', 'Resume print': 'Resume',
                    'Adjust Z offset': 'Z offset'}

    def __init__(self):
        self.mode = 'status'
        self.selection = 0
        self.network = {}
        self.brightness = 100
        self.ssid = ''
        self.secret = ''
        self.security = 'open'
        self.saved = False
        self.draft = dict(method='auto', address='192.168.1.100', prefix=24,
                          gateway='192.168.1.1', dns='1.1.1.1')
        self.edit_field = 'address'
        self.octets = [0, 0, 0, 0]
        self.octet = 0
        self.actions = []
        self.message = []
        self.previous_mode = 'network'
        self.waiting = False
        self.printer_data = {}
        self.jog_axis = 'X'
        self.jog_step = 1.
        self.jog_delta = 0.
        self.jog_pending = False
        self.target_kind = 'nozzle'
        self.target_value = 0
        self.confirm_action = None
        self.confirm_title = ''
        self.action_back = 'printer'
        self.filament_action = 'load'
        self.filament_temperature = 210
        self.filament_length = 20
        self.filament_phase = None
        self.filament_heat_confirmed = False
        self.filament_previous_target = 0
        self.filament_deadline = 0
        self.filament_back = 'printer'
        self.confirm_note = []
        self.files = []
        self.print_state = 'standby'
        self.print_file = ''
        self.print_duration = 0.
        self.bed_temperature = 0.
        self.runout = False
        self.finished_dismissed = None
        self.status_keys = ()
        self.z_offset = 0.
        self.z_queued = 0.
        self.z_sent = 0.
        self.z_pending = False
        self.zoffset_back = 'printer'
        self.job_name = None

    def public_status(self):
        # Never expose the secret, character editor or action payloads.
        return dict(page=self.mode, selection=self.selection,
                    text_brightness=self.brightness, network_busy=self.waiting)

    # Screens whose cursor starts on Back (bottom) so a stray click is harmless.
    BACK_DEFAULT = ('filament', 'action_confirm', 'shutdown_confirm')

    def enter(self, mode):
        self.mode, self.selection = mode, 0
        if mode in self.BACK_DEFAULT:
            self.selection = len(self.items()) - 1

    def tell(self, *lines, back='network'):
        self.message = list(lines)
        self.previous_mode = back
        self.enter('message')

    def help_lines(self, hostname=None):
        # Where the printed guide lives: by mDNS name and by IP address.
        name = (hostname or socket.gethostname()).lower()
        if '.' not in name:
            name += '.local'
        ip = self.network.get('ip', '')
        return ['Help guide (iPad/computer)',
                'http://%s/help' % name,
                ('http://%s/help' % ip) if ip else 'IP: not connected',
                'Name: ' + name,
                'IP:   ' + (ip or 'not connected'),
                'Dashboard: add :81',
                'Same Wi-Fi as printer']

    def update_network(self, result):
        first = not self.network
        if self.mode == 'wifi' and 'networks' in self.network:
            result = dict(result, networks=self.network['networks'])
        self.network = result
        self.printer_data = result.get('printer', self.printer_data)
        self._filament_progress()
        job = result.get('job') or {}
        if self.mode == 'job' and job.get('name') == self.job_name:
            if job.get('state') == 'failed':
                self.tell('Calibration failed' if self.job_name == 'calibrate'
                          else 'Z offset not saved', job.get('error', '')[:30],
                          'Nothing was saved', back='printer')
            elif job.get('state') == 'done':
                self.tell('Saved', 'Printer is restarting', back='status')
        if first:
            self.brightness = result.get('brightness', 100)
            self.draft.update(result.get('ipv4', {}))
        if self.items():
            self.selection %= len(self.items())
        if result.get('checkpoint') and self.mode == 'connecting':
            if result.get('state') == 100 and result.get('ip'):
                self.enter('keep')
        elif self.mode in ('connecting', 'keep') and not result.get('checkpoint'):
            self.waiting = False
            self.tell('Connection rolled back', 'Previous settings restored')

    def result(self, action, result):
        if action == 'jog':
            self.jog_pending = False
            if result.get('ok'):
                self.jog_delta = 0.
                self.enter('jog')
            else:
                self.tell(result.get('error', 'Jog failed'), back='jog')
            return
        if action == 'z_adjust':
            self.z_pending = False
            if result.get('ok'):
                # Klipper's gcode_move now includes it; avoid double counting.
                self.z_offset += self.z_sent
                self.z_sent = 0.
                self._send_z()
            else:
                self.z_sent = self.z_queued = 0.
                self.tell(result.get('error', 'Z offset not changed'), back='zoffset')
            return
        if action == 'files':
            if result.get('ok'):
                self.files = result.get('files', [])
                if self.mode == 'files_loading':
                    if self.files:
                        self.enter('files')
                    else:
                        self.tell('No files on the printer', 'Send one from OrcaSlicer',
                                  back='main')
            elif self.mode == 'files_loading':
                self.tell(result.get('error', 'Could not list files'), back='main')
            return
        if action == 'filament_heat':
            if self.filament_phase != 'heating':
                return  # A cancelled heat request must never trigger feeding.
            if not result.get('ok'):
                self.filament_phase = None
                self.waiting = False
                self.tell(result.get('error', 'Could not heat nozzle'),
                          'No filament was fed', back='printer')
            else:
                self.filament_heat_confirmed = True
            return
        if action == 'filament_stop':
            self.waiting = False
            self.tell('Filament change cancelled', 'No filament was fed',
                      'Previous heat target restored' if result.get('ok') else
                      'Check nozzle heat in Heat menu', back='printer')
            return
        self.waiting = False
        if not result.get('ok', False):
            self.secret = ''
            if action in ('load', 'unload'):
                self.filament_phase = None
            self.tell(result.get('error', 'Action failed'),
                      back='filament' if action in ('load', 'unload') else 'main')
            return
        if action == 'scan':
            self.network['networks'] = result.get('networks', [])
            if self.mode == 'scanning':
                self.enter('wifi')
        elif action == 'apply':
            self.secret = ''
            self.network['checkpoint'] = True
            self.waiting = True
            self.enter('connecting')
        elif action in ('keep', 'rollback'):
            self.tell('Connection saved' if action == 'keep'
                      else 'Previous connection restored')
        elif action in ('load', 'unload'):
            self.filament_phase = None
            self.enter('filament_done')
            self.selection = 0 if self.filament_action == 'load' else 1
        elif action == 'print':
            self.finished_dismissed = None
            self.tell('Print starting', 'Bed heats, then it homes',
                      'and levels before printing', back='status')
        elif action == 'preheat':
            self.tell('Preheating', 'Temperatures show on the', 'status screen', back='status')
        elif action in ('calibrate', 'z_save'):
            self.job_name = action
            self.enter('job')
        elif action in ('home', 'pause', 'resume', 'cancel', 'heater',
                        'fan', 'motors_off', 'emergency'):
            self.tell('Command accepted', back=self.action_back)

    def _filament_progress(self):
        if self.filament_phase != 'heating' or not self.filament_heat_confirmed:
            return
        data = self.printer_data
        if not data.get('paused') and data.get('state') == 'printing':
            self.filament_phase = None
            self.waiting = False
            self.tell('Print started; change stopped', 'No filament was fed', back='printer')
        elif time.monotonic() >= self.filament_deadline:
            self._cancel_filament()
        elif data.get('nozzle_target') != self.filament_temperature:
            self.filament_phase = None
            self.waiting = False
            self.tell('Nozzle target changed', 'No filament was fed', back='printer')
        elif (data.get('can_extrude') and
              data.get('nozzle_temperature', 0) >= self.filament_temperature - 5):
            self.filament_phase = 'feeding'
            self.actions.append(dict(action=self.filament_action,
                                     length=self.filament_length,
                                     temperature=self.filament_temperature))
            self.enter('filament_feeding')

    def _cancel_filament(self):
        self.filament_phase = None
        self.actions.append(dict(action='filament_stop',
                                 target=self.filament_previous_target))
        self.tell('Cancelling; nothing fed', back='printer')

    def finished_key(self):
        return (self.print_file, round(self.print_duration))

    def finished(self):
        return (self.print_state == 'complete' and not self.printer_data.get('paused')
                and self.finished_dismissed != self.finished_key())

    def status_items(self):
        if self.printer_data.get('paused'):
            items = [('Resume print', 'resume'), ('Menu', 'menu')]
            if self.runout:
                items.insert(0, ('Load filament', 'load'))
            return items
        if self.finished():
            items = [('Print again', 'again')] if self.print_file else []
            if abs(self.z_offset) >= .001:
                items.append(('Save Z offset', 'zsave'))
            return items + [('Done', 'done')]
        if self.print_state == 'printing':
            return [('Menu', 'menu'), ('Adjust Z offset', 'zoffset')]
        return [('Menu', 'menu')]

    def printing_now(self):
        return self.printer_data.get('paused') or self.print_state in ('printing', 'paused')

    def items(self):
        if self.mode == 'status':
            items = [label for label, _ in self.status_items()]
            return items if len(items) > 1 else []
        if self.mode == 'main':
            return ['Print a file', 'Printer controls', 'Network', 'Help',
                    'Text brightness', 'Shut down', 'Back']
        if self.mode == 'files':
            return [f['name'] for f in self.files] + ['Back']
        if self.mode == 'printer':
            return ['Motion / Homing', 'Heat / Part fan', 'Adjust Z offset',
                    'Calibrate bed', 'Pause print', 'Resume print', 'Cancel print',
                    'Emergency stop', 'Load filament', 'Unload filament', 'Back']
        if self.mode == 'zoffset_menu':
            return ['Save for future prints', 'Adjust again', 'Back']
        if self.mode == 'filament':
            return ['Heat and ' + self.filament_action,
                    'Temperature: %d C' % self.filament_temperature,
                    'Feed length: %d mm' % self.filament_length, 'Back']
        if self.mode == 'filament_done':
            return ['Extrude more (%d mm)' % self.filament_length,
                    'Retract more (%d mm)' % self.filament_length,
                    'Turn nozzle off', 'Back']
        if self.mode == 'motion':
            return ['Jog X', 'Jog Y', 'Jog Z', 'Step: %g mm' % self.jog_step,
                    'Home XY', 'Home Z', 'Home All', 'Motors off', 'Back']
        if self.mode == 'heat':
            return ['Preheat %s %d/%d' % preset for preset in self.PRESETS] + [
                    'Cool down', 'Nozzle target', 'Bed target', 'Part fan %', 'Back']
        if self.mode == 'action_confirm':
            return ['Confirm', 'Back']
        if self.mode == 'network':
            return ['Scan Wi-Fi', 'IPv4 settings', 'Connection status', 'Back']
        if self.mode == 'wifi':
            return [('%s %d%%' % (n['ssid'], n['signal']))
                    for n in self.network.get('networks', [])] + ['Back']
        if self.mode == 'wifi_choice':
            return (['Use saved profile'] if self.saved else []) + [
                'Enter password' if self.security != 'open' else 'Use open network',
                'Back']
        if self.mode == 'ip':
            return ['DHCP (automatic)', 'Manual IPv4', 'Apply to current Wi-Fi', 'Back']
        if self.mode == 'manual':
            return ['Address: ' + self.draft['address'],
                    'Subnet: /%d' % self.draft['prefix'],
                    'Gateway: ' + self.draft['gateway'],
                    'DNS: ' + self.draft['dns'], 'Review', 'Back']
        if self.mode in ('ip_review', 'octet_review'):
            return ['Confirm', 'Edit', 'Change', 'Back']
        if self.mode == 'apply_review':
            return ['Confirm connection', 'Edit IPv4', 'Change Wi-Fi', 'Back']
        if self.mode == 'keep':
            return ['Keep this connection', 'Roll back now']
        if self.mode == 'shutdown_confirm':
            return ['Confirm shutdown', 'Back']
        return []

    def rotate(self, delta):
        if self.jog_pending:
            return
        if self.mode == 'filament_temp':
            minimum = self.printer_data.get('nozzle_min', 170)
            maximum = self.printer_data.get('nozzle_max', 245)
            self.filament_temperature = max(minimum, min(
                maximum, self.filament_temperature + delta * 5))
        elif self.mode == 'filament_length':
            self.filament_length = max(5, min(50, self.filament_length + delta * 5))
        elif self.mode == 'zoffset':
            current = self.z_offset + self.z_sent + self.z_queued
            wanted = max(-self.Z_LIMIT, min(self.Z_LIMIT, current + delta * self.Z_STEP))
            self.z_queued = round(self.z_queued + wanted - current, 3)
            self._send_z()
        elif self.mode == 'jog':
            self.jog_delta = round(max(-50., min(50., self.jog_delta + delta * self.jog_step)), 3)
        elif self.mode == 'target':
            limit = (100 if self.target_kind == 'fan' else
                     self.printer_data.get(self.target_kind + '_max', 0))
            self.target_value = max(0, min(limit, self.target_value + delta * 5))
        elif self.mode == 'octets':
            self.octets[self.octet] = (self.octets[self.octet] + delta) % 256
        elif self.mode == 'prefix':
            self.draft['prefix'] = 1 + (self.draft['prefix'] - 1 + delta) % 32
        elif self.mode == 'brightness':
            self.brightness = max(10, min(100, self.brightness + delta * 10))
        elif self.mode == 'password':
            self.selection = (self.selection + delta) % len(self.PASSWORD_KEYS)
        elif self.items():
            self.selection = (self.selection + delta) % len(self.items())

    def click(self):
        if self.jog_pending:
            return
        mode, selected = self.mode, self.selection
        if mode == 'status':
            items = self.status_items()
            key = items[selected % len(items)][1]
            if key == 'resume':
                self.confirm('Resume print?', dict(action='resume'), back='status')
            elif key == 'load':
                self._start_filament('load', back='status')
            elif key == 'zoffset':
                self._enter_zoffset('status')
            elif key == 'again':
                self.confirm('Print it again?', dict(action='print', filename=self.print_file),
                             back='status', note=[file_label(self.print_file),
                                                  'Remove the old print first'])
            elif key == 'zsave':
                self._confirm_zsave('status')
            elif key == 'done':
                self.finished_dismissed = self.finished_key()
                self.selection = 0
            else:
                self.enter('main')
        elif mode == 'main':
            choice = self.items()[selected]
            if choice == 'Print a file':
                self.files = []
                self.actions.append(dict(action='files'))
                self.enter('files_loading')
            elif choice == 'Help':
                self.tell(*self.help_lines(), back='main')
            else:
                self.enter({'Printer controls': 'printer', 'Network': 'network',
                            'Text brightness': 'brightness',
                            'Shut down': 'shutdown_confirm'}.get(choice, 'status'))
        elif mode == 'files_loading':
            self.enter('main')
        elif mode == 'files':
            if selected >= len(self.files):
                self.enter('main')
            else:
                chosen = self.files[selected]
                self.confirm('Print this file?', dict(action='print', filename=chosen['path']),
                             back='files', note=[chosen['name'], 'Remove anything on the bed'])
        elif mode == 'printer':
            choice = self.items()[selected]
            if choice in ('Load filament', 'Unload filament'):
                self._start_filament('load' if choice == 'Load filament' else 'unload')
            elif choice == 'Adjust Z offset':
                self._enter_zoffset('printer')
            elif choice == 'Calibrate bed':
                self.confirm('Calibrate bed? ~10 min', dict(action='calibrate'),
                             note=['Bed must be empty', 'Heats bed to 60 C first',
                                   'Printer restarts when done'])
            elif choice in ('Pause print', 'Resume print', 'Cancel print', 'Emergency stop'):
                name = {'Pause print': 'pause', 'Resume print': 'resume',
                        'Cancel print': 'cancel', 'Emergency stop': 'emergency'}[choice]
                self.confirm(name.capitalize() + ' print?', dict(action=name))
            else:
                self.enter({'Motion / Homing': 'motion', 'Heat / Part fan': 'heat'}.get(choice, 'main'))
        elif mode == 'zoffset':
            if self.printing_now():
                self.enter('status')
            else:
                self.enter('zoffset_menu')
        elif mode == 'zoffset_menu':
            if selected == 0:
                self._confirm_zsave(self.zoffset_back)
            elif selected == 1:
                self.enter('zoffset')
            else:
                self.enter(self.zoffset_back)
        elif mode == 'job':
            self.enter('status')
        elif mode == 'filament':
            if selected == 3:
                self.enter(self.filament_back)
            elif selected == 0:
                if self.waiting or self.filament_phase:
                    return
                self.filament_previous_target = self.printer_data.get('nozzle_target', 0)
                self.filament_phase = 'heating'
                self.filament_heat_confirmed = False
                self.filament_deadline = time.monotonic() + 600
                self.waiting = True
                self.action_back = 'printer'
                self.actions.append(dict(action='filament_heat',
                                         target=self.filament_temperature))
                self.enter('filament_heating')
            elif selected == 1:
                self.enter('filament_temp')
            elif selected == 2:
                self.enter('filament_length')
        elif mode == 'filament_heating':
            self._cancel_filament()
        elif mode == 'filament_done':
            if selected in (0, 1):
                if self.waiting or self.filament_phase:
                    return
                # Nozzle is already hot: feed again right away; the host
                # still refuses if it has cooled below the feed temperature.
                self.filament_action = 'load' if selected == 0 else 'unload'
                self.filament_phase = 'feeding'
                self.waiting = True
                self.actions.append(dict(action=self.filament_action,
                                         length=self.filament_length,
                                         temperature=self.filament_temperature))
                self.enter('filament_feeding')
            elif selected == 2:
                self.action_back = 'status' if self.printer_data.get('paused') else 'printer'
                self.actions.append(dict(action='heater', heater='nozzle', target=0))
                self.tell('Turning nozzle off...', back=self.action_back)
            else:
                self.enter('status' if self.printer_data.get('paused') else 'printer')
        elif mode in ('filament_temp', 'filament_length'):
            self.enter('filament')
            self.selection = 1 if mode == 'filament_temp' else 2
        elif mode == 'motion':
            if selected < 3:
                self.jog_axis = 'XYZ'[selected]
                self.jog_delta = 0.
                self.enter('jog')
            elif selected == 3:
                steps = [.1, 1., 10.]
                self.jog_step = steps[(steps.index(self.jog_step) + 1) % len(steps)]
            elif selected in (4, 5, 6):
                axes = {4: 'XY', 5: 'Z', 6: 'XYZ'}[selected]
                self.confirm('Home %s? Clear the bed.' % axes, dict(action='home', axes=axes))
            elif selected == 7:
                self.confirm('Motors off? Bed may drop.', dict(action='motors_off'))
            else:
                self.enter('printer')
        elif mode == 'jog':
            if self.jog_delta:
                self.confirm('Move %s %+g mm?' % (self.jog_axis, self.jog_delta),
                             dict(action='jog', axis=self.jog_axis, distance=self.jog_delta),
                             back='jog')
            else:
                self.enter('motion')
        elif mode == 'heat':
            presets = len(self.PRESETS)
            if selected < presets:
                material, nozzle, bed = self.PRESETS[selected]
                self.confirm('Preheat for %s?' % material,
                             dict(action='preheat', nozzle=nozzle, bed=bed), back='status',
                             note=['Nozzle %d C, bed %d C' % (nozzle, bed)])
            elif selected == presets:
                self.confirm('Turn all heaters off?', dict(action='heater', heater='all', target=0))
            elif selected < presets + 4:
                self.target_kind = ['nozzle', 'bed', 'fan'][selected - presets - 1]
                self.target_value = round(self.printer_data.get(self.target_kind + '_target', 0))
                self.enter('target')
            else:
                self.enter('printer')
        elif mode == 'target':
            if self.target_kind == 'fan':
                action = dict(action='fan', percent=self.target_value)
            else:
                action = dict(action='heater', heater=self.target_kind, target=self.target_value)
            self.confirm('Set %s to %d?' % (self.target_kind, self.target_value), action)
        elif mode == 'action_confirm':
            if selected == 0:
                if self.confirm_action['action'] == 'jog':
                    self.jog_pending = True
                if self.confirm_action['action'] in ('load', 'unload'):
                    if self.waiting:
                        self.tell('Filament command in progress', back='filament')
                        self.confirm_action = None
                        return
                    self.waiting = True
                self.actions.append(self.confirm_action)
                self.tell('Sending command...', back=self.action_back)
            else:
                self.enter(self.action_back)
            self.confirm_action = None
        elif mode == 'network':
            if selected == 0:
                self.actions.append(dict(action='scan'))
                self.waiting = True
                self.enter('scanning')
            elif selected == 1:
                self.enter('ip')
            elif selected == 2:
                self.tell(self.network.get('ssid', 'No Wi-Fi connection'),
                          'IP ' + self.network.get('ip', 'none'),
                          'DHCP' if self.network.get('ipv4', {}).get('method', 'auto') == 'auto'
                          else 'Manual IPv4',
                          'Changes enabled' if self.network.get('changes_allowed')
                          else 'Changes locked: need recovery')
            else:
                self.enter('main')
        elif mode == 'wifi':
            networks = self.network.get('networks', [])
            if selected == len(networks):
                self.enter('network')
            else:
                n = networks[selected]
                self.ssid, self.security, self.saved = n['ssid'], n['security'], n['saved']
                self.secret = ''
                self.enter('wifi_choice')
        elif mode == 'wifi_choice':
            choice = self.items()[selected]
            if choice == 'Back':
                self.enter('wifi')
            elif choice == 'Enter password':
                if self.security not in ('wpa-psk', 'sae'):
                    self.tell('Enterprise/WEP unsupported', 'Use a personal WPA network')
                else:
                    self.secret = ''
                    self.enter('password')
            else:
                self.secret = None if choice == 'Use saved profile' else ''
                self.enter('apply_review')
        elif mode == 'password':
            key = self.PASSWORD_KEYS[selected]
            if key == '<DELETE>':
                self.secret = self.secret[:-1]
            elif key == '<BACK>':
                self.secret = ''
                self.enter('wifi_choice')
            elif key == '<DONE>':
                if 8 <= len(self.secret) <= 63 or (len(self.secret) == 64
                        and all(c in string.hexdigits for c in self.secret)):
                    self.enter('apply_review')
                else:
                    self.tell('WPA password: 8-63 chars', 'or 64 hex characters', back='password')
            elif len(self.secret) < 64:
                self.secret += key
        elif mode == 'ip':
            if selected == 0:
                self.draft['method'] = 'auto'
                self.enter('ip_review')
            elif selected == 1:
                self.draft['method'] = 'manual'
                self.enter('manual')
            elif selected == 2:
                self.ssid = self.network.get('ssid', '')
                self.secret = None
                self.enter('apply_review')
            else:
                self.enter('network')
        elif mode == 'manual':
            if selected in (0, 2, 3):
                self.edit_field = {0: 'address', 2: 'gateway', 3: 'dns'}[selected]
                self.octets = [int(n) for n in self.draft[self.edit_field].split('.')]
                self.octet = 0
                self.enter('octets')
            elif selected == 1:
                self.enter('prefix')
            elif selected == 4:
                self.enter('ip_review')
            else:
                self.enter('ip')
        elif mode == 'octets':
            self.octet += 1
            if self.octet == 4:
                self.enter('octet_review')
        elif mode == 'octet_review':
            if selected == 0:
                self.draft[self.edit_field] = '.'.join(map(str, self.octets))
                self.enter('manual')
            elif selected in (1, 2):
                self.octet = 0
                if selected == 2:
                    self.octets = [0] * 4
                self.enter('octets')
            else:
                self.enter('manual')
        elif mode == 'prefix':
            self.enter('manual')
        elif mode == 'ip_review':
            self.enter(['ip', 'manual', 'ip', 'network'][selected])
        elif mode == 'apply_review':
            if selected == 0:
                if not self.ssid:
                    self.tell('Select a Wi-Fi network first')
                elif not self.network.get('changes_allowed'):
                    self.secret = ''
                    self.tell('Network changes locked', 'Need Ethernet or approved', 'serial recovery for testing')
                else:
                    try:
                        validate_ipv4(self.draft)
                    except ValueError:
                        self.tell('Invalid manual IPv4', 'Check address/gateway/DNS')
                        return
                    self.actions.append(dict(action='apply', ssid=self.ssid,
                                             password=self.secret, ipv4=dict(self.draft)))
                    self.secret = ''
                    self.waiting = True
                    self.enter('connecting')
            else:
                if selected == 3:
                    self.secret = ''
                self.enter({1: 'manual', 2: 'wifi', 3: 'network'}[selected])
        elif mode == 'keep':
            self.actions.append(dict(action='keep' if selected == 0 else 'rollback'))
            self.waiting = True
            self.enter('connecting')
        elif mode in ('scanning', 'connecting'):
            if self.network.get('checkpoint'):
                self.actions.append(dict(action='rollback'))
            self.secret = ''
            self.enter('network')
        elif mode == 'brightness':
            self.actions.append(dict(action='brightness', value=self.brightness))
            self.enter('main')
        elif mode == 'shutdown_confirm':
            if selected == 0:
                self.enter('shutting_down')
                self.actions.append(dict(action='poweroff'))
            else:
                self.enter('main')
        elif mode == 'message':
            self.enter(self.previous_mode)

    def lines(self, snapshot):
        # Use Klipper's direct pause state for the status shortcut, including
        # runout pauses; helper polling may be a few seconds behind.
        if 'pause_resume' in snapshot or 'print_stats' in snapshot:
            self.printer_data['paused'] = (
                snapshot.get('pause_resume', {}).get('is_paused', False)
                or snapshot.get('print_stats', {}).get('state') == 'paused')
        stats = snapshot.get('print_stats')
        if stats is not None:
            self.print_state = stats.get('state', 'standby')
            self.print_file = stats.get('filename', '')
            self.print_duration = stats.get('print_duration', 0.)
        if 'gcode_move' in snapshot:
            self.z_offset = snapshot['gcode_move'].get('homing_origin', [0, 0, 0])[2]
        if 'heater_bed' in snapshot:
            self.bed_temperature = snapshot['heater_bed'].get('temperature', 0.)
        sensors = [v for k, v in snapshot.items() if k.startswith('filament_switch_sensor')]
        self.runout = bool(self.printer_data.get('paused') and any(
            v.get('enabled', True) and not v.get('filament_detected', True) for v in sensors))
        if self.mode == 'status':
            items = self.status_items()
            keys = tuple(key for _, key in items)
            if keys != self.status_keys:
                self.status_keys, self.selection = keys, 0
            if self.finished():
                bed = round(self.bed_temperature)
                return ['Print complete!', file_label(self.print_file),
                        'Took ' + duration(self.print_duration),
                        ('Bed %d C: let it cool' % bed) if bed > self.BED_COOL
                        else 'Bed is cool: remove print', ''] + [
                        ('> ' if n == self.selection else '  ') + label
                        for n, (label, _) in enumerate(items)]
            if len(items) == 1:
                footer = 'Click: ' + items[0][0]
            else:
                def join(short):
                    return '  '.join(('> ' if n == self.selection else '  ')
                                     + (self.STATUS_SHORT.get(label, label) if short else label)
                                     for n, (label, _) in enumerate(items))
                footer = join(False) if len(join(False)) <= 30 else join(True)
            return status_lines(snapshot, self.network, footer, self.runout)
        if self.mode == 'zoffset':
            shown = self.z_offset + self.z_sent + self.z_queued
            return ['Z offset (live)', '%+.3f mm' % shown,
                    'Right: nozzle up', '  (less squished)',
                    'Left: nozzle down', '  (more squished)',
                    'Each click %.3f mm' % self.Z_STEP, 'Click: done']
        if self.mode == 'files_loading':
            return ['Loading files...', 'Click: back']
        if self.mode == 'job':
            title = 'Calibrating bed...' if self.job_name == 'calibrate' else 'Saving Z offset...'
            return [title, 'Do not touch the printer',
                    'About 10 minutes' if self.job_name == 'calibrate' else '',
                    'Printer restarts when done', '', 'Click: status screen']
        if self.mode == 'filament_temp':
            return ['Filament temperature', '%d C' % self.filament_temperature,
                    'Rotate: change by 5', 'Click: accept']
        if self.mode == 'filament_length':
            return ['Filament feed length', '%d mm' % self.filament_length,
                    'Rotate: change by 5 mm', 'Click: accept',
                    'Repeat if more feed is needed']
        if self.mode == 'filament':
            items = self.items()
            return [self.filament_action.capitalize() + ' filament',
                    'Insert into extruder first' if self.filament_action == 'load' else
                    'Wait; do not pull yet',
                    'Heats, then feeds %d mm' % self.filament_length] + [
                    ('> ' if n == self.selection else '  ') + item
                    for n, item in enumerate(items)] + ['Print stays paused']
        if self.mode in ('filament_heating', 'filament_feeding'):
            hot = snapshot.get('extruder', {})
            return ['Heating nozzle...' if self.mode == 'filament_heating' else
                    ('Loading filament...' if self.filament_action == 'load' else 'Unloading filament...'),
                    'Nozzle %d/%d C' % (round(hot.get('temperature', 0)), self.filament_temperature),
                    'Please wait; feeds when hot' if self.mode == 'filament_heating' else
                    'Keep hands clear; please wait',
                    'Print stays paused',
                    'Click: cancel' if self.mode == 'filament_heating' else 'Finishing this feed...']
        if self.mode == 'filament_done':
            return ['Filament loaded' if self.filament_action == 'load' else 'Ready to remove filament',
                    'Plastic coming out? Clear it.' if self.filament_action == 'load' else
                    'Release lever; gently pull out',
                    'Nozzle stays HOT', 'Print stays paused'] + [
                    ('> ' if n == self.selection else '  ') + item
                    for n, item in enumerate(self.items())]
        if self.mode == 'password':
            return ['Wi-Fi password', self.ssid[:30],
                    '*' * min(26, len(self.secret)),
                    '%d characters' % len(self.secret),
                    'Character: ' + self.PASSWORD_KEYS[self.selection],
                    'Rotate: choose; click: add', 'Find <DONE> to finish',
                    '<DELETE> or <BACK>']
        if self.mode == 'jog':
            return ['Jog ' + self.jog_axis, '%+g mm (step %g)' % (self.jog_delta, self.jog_step),
                    'Rotate: set distance', 'Click: review, then confirm',
                    'After move: rotate to jog again', 'Set zero + click to go back']
        if self.mode == 'target':
            return ['Set ' + self.target_kind, str(self.target_value),
                    'Rotate: change by 5', 'Click: review, then confirm']
        if self.mode == 'octets':
            address = '.'.join(map(str, self.octets))
            return ['Edit ' + self.edit_field, address,
                    'Octet %d of 4: [%d]' % (self.octet + 1, self.octets[self.octet]),
                    'Rotate +/-; wraps 0..255', 'Click: next octet']
        if self.mode == 'prefix':
            mask = str(ipaddress.IPv4Network('0.0.0.0/%d' % self.draft['prefix']).netmask)
            return ['Subnet mask', mask + ' /%d' % self.draft['prefix'],
                    'Rotate: change 1..32', 'Click: accept']
        if self.mode == 'brightness':
            return ['Text brightness: %d%%' % self.brightness,
                    'Rotate: change; click: save',
                    'Backlight is fixed on V1.0']
        if self.mode == 'message':
            return self.message[:7] + ['Click: back']
        if self.mode == 'shutting_down':
            return ['Shutting down...', 'Wait at least 30 seconds',
                    'before switching off.', '', 'If shutdown stalls, wait.',
                    'Check serial if uncertain.']
        if self.mode == 'scanning':
            return ['Scanning Wi-Fi...', 'Click: back']
        if self.mode == 'connecting':
            return ['Connecting...', self.ssid,
                    'Automatic rollback: 90 sec', 'Click: roll back']
        titles = {'main': 'Menu', 'printer': 'Printer controls', 'motion': 'Motion / Homing',
                  'heat': 'Heat / Part fan', 'action_confirm': self.confirm_title,
                  'network': 'Network', 'wifi': 'Nearby Wi-Fi',
                  'wifi_choice': self.ssid, 'ip': 'IPv4 settings',
                  'manual': 'Manual IPv4', 'ip_review': 'Review IPv4',
                  'octet_review': '.'.join(map(str, self.octets)),
                  'apply_review': self.ssid, 'keep': 'Connection working?',
                  'shutdown_confirm': 'Shut down host?', 'files': 'Print a file',
                  'zoffset_menu': 'Z offset %+.3f mm' % self.z_offset}
        items = self.items()
        note = self.confirm_note if self.mode == 'action_confirm' else []
        visible = 6 - len(note)
        start = max(0, self.selection - visible + 1)
        lines = [titles.get(self.mode, self.mode)] + note
        lines += [('> ' if n == self.selection else '  ') + item
                  for n, item in enumerate(items) if start <= n < start + visible]
        if self.mode == 'shutdown_confirm':
            lines += ['Only when idle and cooled']
        elif self.mode in ('ip_review', 'apply_review'):
            lines += [('DHCP' if self.draft['method'] == 'auto' else
                       self.draft['address'] + '/%d' % self.draft['prefix'])]
            if self.mode == 'apply_review':
                lines += ['Apply may disconnect SSH']
        return lines[:8]

    def confirm(self, title, action, back='printer', note=()):
        self.confirm_title, self.confirm_action = title, action
        self.confirm_note = list(note)
        self.action_back = back
        self.enter('action_confirm')

    def _start_filament(self, action, back='printer'):
        self.filament_action = action
        self.filament_back = back
        minimum = self.printer_data.get('nozzle_min', 170)
        maximum = self.printer_data.get('nozzle_max', 245)
        self.filament_temperature = max(minimum, min(maximum,
            round(self.printer_data.get('nozzle_target') or 210)))
        self.enter('filament')

    def _enter_zoffset(self, back):
        self.zoffset_back = back
        self.enter('zoffset')

    def _confirm_zsave(self, back):
        self.confirm('Save Z offset %+.3f?' % self.z_offset, dict(action='z_save'),
                     back=back, note=['Used for all future prints', 'Printer restarts (~30 s)'])

    def _send_z(self):
        # One adjustment in flight; extra rotation accumulates and follows.
        if self.z_pending or abs(self.z_queued) < .0005:
            return
        self.z_sent, self.z_queued = self.z_queued, 0.
        self.z_pending = True
        self.actions.append(dict(action='z_adjust', delta=self.z_sent))


def validate_ipv4(settings):
    if settings.get('method') == 'auto':
        return
    if settings.get('method') != 'manual':
        raise ValueError('Invalid method')
    interface = ipaddress.IPv4Interface('%s/%d' % (
        settings['address'], int(settings['prefix'])))
    if not 1 <= interface.network.prefixlen <= 32:
        raise ValueError('Invalid prefix')
    if interface.ip.is_unspecified or interface.ip.is_multicast or interface.ip.is_loopback:
        raise ValueError('Invalid host address')
    if interface.network.prefixlen < 31 and interface.ip in (
            interface.network.network_address, interface.network.broadcast_address):
        raise ValueError('Not a host address')
    gateway = ipaddress.IPv4Address(settings['gateway'])
    if not gateway.is_unspecified and gateway not in interface.network:
        raise ValueError('Gateway outside subnet')
    ipaddress.IPv4Address(settings['dns'])

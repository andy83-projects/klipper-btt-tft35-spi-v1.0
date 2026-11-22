# Experimental BTT TFT35-SPI V1.0 bring-up through a Klipper MCU.
# Distributed under the GNU GPLv3 license.
# ST7796 sequence adapted from Marlin src/lcd/tft_io/st7796s.h (GPLv3).
import collections
import fcntl
import socket
import struct
import json
import queue as thread_queue
import threading
from .b1_panel_ui import PanelUI

from . import bus
from .display.font8x14 import VGA_FONT


def host_ipv4():
    """Read local interface addresses without network traffic or subprocesses."""
    result = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for _, name in socket.if_nameindex():
            if name == 'lo':
                continue
            try:
                data = fcntl.ioctl(sock, 0x8915,
                                   struct.pack('256s', name.encode()[:15]))
                address = socket.inet_ntoa(data[20:24])
                result.append((name, address))
            except OSError:
                continue
    return sorted(result)


def text_pixels(text, columns=30, brightness=100):
    """One 480x32 RGB565 text strip, 30 characters, doubled VGA font."""
    text = text[:columns].ljust(columns)
    level = max(0, min(100, brightness)) / 100.
    white = struct.pack('>H', (round(31 * level) << 11)
                        | (round(63 * level) << 5) | round(31 * level))
    glyphs = [VGA_FONT[ord(c) if 32 <= ord(c) < 127 else ord('?')]
              for c in text]
    pixels = bytearray()
    for row in range(16):
        line = bytearray()
        for glyph in glyphs:
            for bit in range(7, -1, -1):
                line.extend(white * 2 if glyph[row] & (1 << bit)
                            else b'\x00\x00' * 2)
        pixels.extend(line * 2)
    return pixels


def st7796_init(rotation):
    # No hardware RESET: EXP2 reset is shared with the printer mainboard.
    return [
        (0x01, b'', .150), (0x11, b'', .120),
        (0xF0, b'\xc3', 0), (0xF0, b'\x96', 0),
        (0x36, bytes([rotation]), 0), (0x3A, b'\x55', 0),
        (0xB4, b'\x01', 0), (0xB7, b'\xc6', 0),
        (0xC1, b'\x15', 0), (0xC2, b'\xaf', 0),
        (0xC5, b'\x22', 0), (0xC6, b'\x00', 0),
        (0xE8, bytes.fromhex('40 8a 00 00 29 19 a5 33'), 0),
        (0xE0, bytes.fromhex('f0 04 08 09 08 15 2f 42 46 28 15 16 29 2d'), 0),
        (0xE1, bytes.fromhex('f0 04 09 09 08 15 2e 46 46 28 15 15 29 2d'), 0),
        (0x13, b'', 0), (0x53, b'\x24', 0),
        (0xF0, b'\x3c', 0), (0xF0, b'\x69', 0),
        (0x29, b'', .020),
    ]


class B1Panel:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        # Explicit opt-in acknowledges verified panel/cable orientation.
        if not config.getboolean('wiring_verified', False):
            raise config.error('b1_panel requires wiring_verified: True')
        self.rotation = config.getint('madctl', 0x28)
        if self.rotation not in (0x28, 0xE8):
            raise config.error('b1_panel madctl must be 40 or 232')
        speed = config.getint('spi_speed', 2000000,
                              minval=100000, maxval=2000000)
        ppins = self.printer.lookup_object('pins')
        cs_name = config.get('cs_pin')
        # Parse through Klipper's resolver; pin ownership is registered below.
        cs_params = ppins.parse_pin(cs_name)
        mcu = cs_params['chip']
        self.spi = bus.MCU_SPI(mcu, config.get('spi_bus'), None, 0, speed)
        queue = self.spi.get_command_queue()
        self.cs = bus.MCU_bus_digital_out(mcu, cs_name, queue, value=1)
        self.dc = bus.MCU_bus_digital_out(mcu, config.get('dc_pin'), queue,
                                         value=1)
        self.other_cs = [bus.MCU_bus_digital_out(mcu, pin, queue, value=1)
                         for pin in config.getlist('other_cs_pins')]
        buttons = self.printer.load_object(config, 'buttons')
        a, b = config.getlist('encoder_pins', count=2)
        buttons.register_rotary_encoder(a, b, self._cw, self._ccw, 4)
        buttons.register_buttons([config.get('click_pin')], self._click)
        self.counts = dict(cw=0, ccw=0, press=0)
        self.last_press = -1.
        self.ready = self.initialized = self.dirty = False
        self.last_lines = {}
        self.ops = collections.deque()
        self.ui_mode = config.getboolean('status_menu', False)
        self.ui = PanelUI() if self.ui_mode else None
        self.brightness = 100
        self.backend_path = config.get('host_socket', '/run/b1-panel/host.sock')
        self.backend_queue = thread_queue.Queue()
        self.backend_stop = threading.Event()
        self.poweroff_pending = False
        self.timer = self.reactor.register_timer(self._tick)
        for name in ('ID', 'INIT', 'SHOW', 'STATUS', 'STOP', 'MENU'):
            self.gcode.register_command('BTT_PANEL_' + name,
                                        getattr(self, 'cmd_' + name))
        self.printer.register_event_handler('klippy:ready', self._ready)
        self.printer.register_event_handler('klippy:shutdown', self._shutdown)
        self.printer.register_event_handler('klippy:disconnect', self._disconnect)

    def _ready(self):
        self.ready = True
        if self.ui:
            threading.Thread(target=self._backend_worker, daemon=True).start()
            self._initialize()

    def _disconnect(self):
        self.backend_stop.set()

    def _shutdown(self):
        self.backend_stop.set()
        self.ready = self.initialized = False
        self.ops.clear()
        self.reactor.update_timer(self.timer, self.reactor.NEVER)

    def _idle(self):
        now = self.reactor.monotonic()
        for obj, states in [('print_stats', ('printing', 'paused')),
                            ('idle_timeout', ('Printing',))]:
            instance = self.printer.lookup_object(obj, None)
            if instance and instance.get_status(now).get('state') in states:
                return False
        return self.ready

    def _guard(self, gcmd):
        if not self._idle():
            raise gcmd.error('B1 panel test requires an idle, ready printer')

    def _write(self, command, data=b''):
        self.cs.update_digital_out(0)
        self.dc.update_digital_out(0)
        self.spi.spi_send(bytes([command]))
        if data:
            self.dc.update_digital_out(1)
            for offset in range(0, len(data), 48):
                self.spi.spi_send(data[offset:offset+48])
        self.cs.update_digital_out(1)

    def cmd_ID(self, gcmd):
        self._guard(gcmd)
        if self.ops:
            raise gcmd.error('Wait for panel transfer to finish')
        # Hold CS continuously through command and read; do not use hardware
        # RESET or initialize the controller. Keep raw bytes for diagnosis.
        self.cs.update_digital_out(0)
        try:
            self.dc.update_digital_out(0)
            self.spi.spi_send(b'\xd3')
            self.dc.update_digital_out(1)
            response = self.spi.spi_transfer(b'\x00' * 4)['response']
        finally:
            self.cs.update_digital_out(1)
        gcmd.respond_info('LCD D3 raw: %s (interpretation requires verification)'
                           % bytes(response).hex())

    def cmd_INIT(self, gcmd):
        self._guard(gcmd)
        if gcmd.get('CONTROLLER', '').upper() != 'ST7796':
            raise gcmd.error('Specify CONTROLLER=ST7796 after checking LCD ID')
        if self.ops:
            raise gcmd.error('Wait for panel transfer to finish')
        self._initialize()
        gcmd.respond_info('Initialization queued; visible text is not verified')

    def _initialize(self):
        self.initialized = False
        self.last_lines.clear()
        for command, data, delay in st7796_init(self.rotation):
            self.ops.append(('command', command, data, delay))
        # LCD RAM is undefined after reset. Clear every pixel, including the
        # gaps between text strips, before declaring initialization complete.
        self._queue_rectangle(0, 0, 479, 319, bytes(480 * 320 * 2))
        self.ops.append(('initialized',))
        self.reactor.update_timer(self.timer, self.reactor.NOW)

    def cmd_SHOW(self, gcmd):
        self._guard(gcmd)
        if not self.initialized:
            raise gcmd.error('Run BTT_PANEL_INIT first')
        self.dirty = True
        self.reactor.update_timer(self.timer, self.reactor.NOW)

    def cmd_STATUS(self, gcmd):
        gcmd.respond_info(str(self.get_status(self.reactor.monotonic())))

    def cmd_MENU(self, gcmd):
        self._guard(gcmd)
        if not self.ui:
            raise gcmd.error('Status/menu mode is not enabled')
        page = gcmd.get('PAGE', 'main').lower()
        # Read-only page selection for display QA. No clicks/actions can be
        # synthesized by this command, and password pages are excluded.
        if page not in ('status', 'main', 'printer', 'motion', 'heat', 'filament', 'network',
                        'ip', 'manual', 'brightness'):
            raise gcmd.error('Unsupported read-only menu page')
        self.ui.secret = ''
        self.ui.enter(page)
        self.dirty = True
        self.reactor.update_timer(self.timer, self.reactor.NOW)

    def cmd_STOP(self, gcmd):
        self.ops.clear()
        self.dirty = self.initialized = False
        self.last_lines.clear()
        self.cs.update_digital_out(1)
        self.reactor.update_timer(self.timer, self.reactor.NEVER)
        gcmd.respond_info('Panel transfers stopped; encoder counters retained')

    def _event(self, kind, eventtime):
        self.counts[kind] += 1
        if getattr(self, 'ui', None):
            if kind == 'press':
                self.ui.click()
            else:
                self.ui.rotate(1 if kind == 'cw' else -1)
            self._ui_actions()
        self.dirty = True
        if self.initialized:
            self.reactor.update_timer(self.timer, self.reactor.NOW)

    def _cw(self, eventtime):
        self._event('cw', eventtime)

    def _ccw(self, eventtime):
        self._event('ccw', eventtime)

    def _click(self, eventtime, state):
        if state and eventtime - self.last_press >= .050:
            self.last_press = eventtime
            self._event('press', eventtime)

    def _render(self):
        addresses = host_ipv4()
        lines = ['B1 PANEL HARDWARE TEST',
                 '%s %s' % addresses[0] if addresses else 'NO IPv4 ADDRESS',
                 'CW %d  CCW %d' % (self.counts['cw'], self.counts['ccw']),
                 'PRESS %d' % self.counts['press'],
                 'TEXT: confirm visually',
                 'Klipper-dependent test']
        if getattr(self, 'ui', None):
            snapshot = {}
            for name in ('print_stats', 'pause_resume', 'display_status', 'extruder', 'heater_bed',
                         'fan', 'heater_fan heatbreak_fan1', 'temperature_fan SoC_fan'):
                instance = self.printer.lookup_object(name, None)
                if instance:
                    snapshot[name] = instance.get_status(self.reactor.monotonic())
            lines = self.ui.lines(snapshot)
            lines = (lines + [''] * 8)[:8]
            if self.brightness != self.ui.brightness:
                self.brightness = self.ui.brightness
                self.last_lines.clear()
        for index, line in enumerate(lines):
            line = line[:30].ljust(30)
            if self.last_lines.get(index) == line:
                continue
            old = self.last_lines.get(index)
            first, last = 0, 29
            if old is not None:
                changes = [i for i in range(30) if old[i] != line[i]]
                first, last = changes[0], changes[-1]
            self.last_lines[index] = line
            y = index * 40
            self._queue_rectangle(first * 16, y, (last+1) * 16-1, y+31,
                                  text_pixels(line[first:last+1], last-first+1,
                                              getattr(self, 'brightness', 100)))
        self.dirty = False

    def _ui_actions(self):
        while self.ui.actions:
            action = self.ui.actions.pop(0)
            if action['action'] == 'poweroff':
                self.poweroff_pending = True
            else:
                self.backend_queue.put(action)

    def _backend_worker(self):
        while not self.backend_stop.is_set():
            try:
                request = self.backend_queue.get(timeout=2.)
            except thread_queue.Empty:
                request = dict(action='snapshot')
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(45. if request['action'] in ('load', 'unload', 'jog') else 12.)
                    client.connect(self.backend_path)
                    client.sendall(json.dumps(request).encode() + b'\n')
                    data = b''
                    while b'\n' not in data and len(data) < 65536:
                        block = client.recv(4096)
                        if not block:
                            break
                        data += block
                    result = json.loads(data.split(b'\n')[0])
            except Exception:
                result = dict(ok=False, error='Local host helper unavailable')
            # Do not capture passwords in callbacks or report them in status.
            name = request['action']
            request.clear()
            if self.backend_stop.is_set():
                return
            self.reactor.register_async_callback(
                lambda et, n=name, r=result: self._backend_result(n, r))

    def _backend_result(self, name, result):
        if not self.ready or not self.ui:
            return
        if name == 'snapshot':
            if result.get('ok'):
                self.ui.update_network(result)
        else:
            self.ui.result(name, result)
        # Heating completion can queue a feed without another physical click.
        self._ui_actions()
        self.dirty = True
        if self.initialized:
            self.reactor.update_timer(self.timer, self.reactor.NOW)

    def _queue_rectangle(self, x0, y0, x1, y1, pixels):
        self.ops.append(('command', 0x2A, struct.pack('>HH', x0, x1), 0))
        self.ops.append(('command', 0x2B, struct.pack('>HH', y0, y1), 0))
        self.ops.append(('command', 0x2C, b'', 0))
        # Hold data mode and CS across this rectangle's SPI packets instead
        # of sending three redundant GPIO commands for every 48-byte packet.
        self.ops.append(('pixel_begin',))
        for offset in range(0, len(pixels), 48):
            self.ops.append(('pixels', pixels[offset:offset+48]))
        self.ops.append(('pixel_end',))

    def _tick(self, eventtime):
        if not self.ready or (not getattr(self, 'ui_mode', False) and not self._idle()):
            self.ops.clear()
            self.initialized = False
            self.last_lines.clear()
            if self.ready:
                self.cs.update_digital_out(1)
            return self.reactor.NEVER
        if not self.ops and self.dirty and self.initialized:
            self._render()
        if (getattr(self, 'poweroff_pending', False) and not self.ops
                and not self.dirty):
            # Wait for the message to reach the physical LCD before poweroff.
            self.spi.spi_transfer(b'')
            self.poweroff_pending = False
            self.backend_queue.put(dict(action='poweroff'))
        # Bound USB traffic and yield to motion/other reactor work.
        printing = getattr(self, 'ui_mode', False) and not self._idle()
        packet_budget = 2 if printing else 32
        for _ in range(packet_budget):
            if not self.ops:
                break
            op = self.ops.popleft()
            if op[0] == 'initialized':
                self.initialized = self.dirty = True
            elif op[0] == 'command':
                self._write(op[1], op[2])
                if op[3]:
                    # Fence the queued SPI write before starting its delay.
                    # Empty query uses the same queue, emits no SPI clocks,
                    # and acknowledges execution of all preceding writes.
                    self.spi.spi_transfer(b'')
                    return self.reactor.monotonic() + op[3]
            elif op[0] == 'pixel_begin':
                self.cs.update_digital_out(0)
                self.dc.update_digital_out(1)
            elif op[0] == 'pixel_end':
                self.cs.update_digital_out(1)
            else:
                self.spi.spi_send(op[1])
        if self.ops or self.dirty:
            return eventtime + (.020 if printing else .010)
        if getattr(self, 'ui_mode', False) and self.initialized:
            self.dirty = True
            return eventtime + 2.
        return self.reactor.NEVER

    def get_status(self, eventtime):
        status = dict(self.counts, initialized=self.initialized,
                    transfer_pending=bool(self.ops),
                    text_verified=False, backlight_verified=False)
        if getattr(self, 'ui', None):
            status.update(self.ui.public_status())
        return status


def load_config(config):
    return B1Panel(config)

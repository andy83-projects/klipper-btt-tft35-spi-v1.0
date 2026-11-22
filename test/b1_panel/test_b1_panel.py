import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / 'klippy'))
# Bus helpers are real; stub the unused MCU module to avoid importing a live
# serial stack or requiring printer dependencies for these offline tests.
with patch.dict(sys.modules, {'mcu': Mock()}):
    from extras import b1_panel
from extras.buttons import FullStepRotaryEncoder
from gcode import GCodeDispatch


class Pin:
    def __init__(self, name, trace):
        self.name, self.trace = name, trace

    def update_digital_out(self, value):
        self.trace.append((self.name, value))


class SPI:
    def __init__(self, trace):
        self.trace = trace

    def spi_send(self, data):
        self.trace.append(('send', bytes(data)))

    def spi_transfer(self, data):
        self.trace.append(('transfer', bytes(data)))
        return {'response': b'\x00\x77\x96\x00'}


def make_panel():
    panel = b1_panel.B1Panel.__new__(b1_panel.B1Panel)
    panel.trace = []
    panel.cs, panel.dc = Pin('cs', panel.trace), Pin('dc', panel.trace)
    panel.spi = SPI(panel.trace)
    panel.reactor = Mock(NOW=0., NEVER=1.e30)
    panel.reactor.monotonic.return_value = 5.
    panel.timer = object()
    panel.printer = Mock()
    panel.printer.lookup_object.return_value = None
    panel.ready = True
    panel.initialized = panel.dirty = False
    panel.counts = dict(cw=0, ccw=0, press=0)
    panel.last_press = -1.
    panel.last_lines = {}
    panel.ops = b1_panel.collections.deque()
    panel.rotation = 0x28
    return panel


class PanelTests(unittest.TestCase):
    def test_heating_snapshot_dispatches_feed_without_another_click(self):
        panel = make_panel()
        panel.ui = b1_panel.PanelUI()
        panel.backend_queue = b1_panel.thread_queue.Queue()
        panel.ui.enter('printer'); panel.ui.selection = 6; panel.ui.click()
        panel.ui.selection = 0; panel.ui.click()
        panel.ui.actions.clear()
        panel.ui.result('filament_heat', {'ok': True})
        panel._backend_result('snapshot', dict(ok=True, printer=dict(
            state='paused', paused=True, nozzle_target=210,
            nozzle_temperature=210, can_extrude=True)))
        self.assertEqual(panel.backend_queue.get_nowait(),
                         dict(action='load', length=20, temperature=210))
        self.assertEqual(panel.ui.mode, 'filament_feeding')
        self.assertFalse(panel.ui.actions)

    def test_commands_register_with_real_klipper_parser(self):
        printer = Mock()
        printer.config_error.side_effect = ValueError
        dispatch = GCodeDispatch(printer)
        config = Mock()
        config.get_printer.return_value = printer
        config.getboolean.return_value = True
        config.getint.side_effect = lambda name, default, **kw: default
        config.get.side_effect = lambda name, default=None: {
            'cs_pin': 'EXP2_4', 'dc_pin': 'EXP2_7',
            'spi_bus': 'spi2', 'click_pin': '^!EXP1_2'}.get(name, default)
        config.getlist.side_effect = lambda name, **kw: {
            'encoder_pins': ['^EXP2_3', '^EXP2_5'],
            'other_cs_pins': ['EXP1_8', 'EXP1_4']}[name]
        pins = Mock()
        pins.parse_pin.return_value = {'chip': Mock()}
        printer.lookup_object.side_effect = lambda name: {
            'gcode': dispatch, 'pins': pins}[name]
        spi = Mock()
        spi.get_command_queue.return_value = object()
        with patch.object(b1_panel.bus, 'MCU_SPI', return_value=spi), \
                patch.object(b1_panel.bus, 'MCU_bus_digital_out'):
            b1_panel.B1Panel(config)
        for name in ('ID', 'INIT', 'SHOW', 'STATUS', 'STOP'):
            self.assertIn('BTT_PANEL_' + name, dispatch.ready_gcode_handlers)

    def test_unverified_wiring_rejected_before_hardware_setup(self):
        config = Mock()
        config.getboolean.return_value = False
        config.error.side_effect = ValueError
        with patch.object(b1_panel.bus, 'MCU_SPI') as factory:
            with self.assertRaises(ValueError):
                b1_panel.B1Panel(config)
            factory.assert_not_called()

    def test_pixels_are_complete_rgb565_strips(self):
        pixels = b1_panel.text_pixels('10.0.0.225')
        self.assertEqual(len(pixels), 480 * 32 * 2)
        self.assertIn(b'\xff\xff', pixels)
        self.assertIn(b'\x00\x00', pixels)
        self.assertEqual(pixels[:960], pixels[960:1920])
        self.assertEqual(b1_panel.text_pixels('A' * 31),
                         b1_panel.text_pixels('A' * 30))
        self.assertEqual(b1_panel.text_pixels('\u2603'),
                         b1_panel.text_pixels('?'))

    def test_command_keeps_cs_asserted_and_limits_packets(self):
        panel = make_panel()
        panel._write(0xE0, bytes(range(100)))
        self.assertEqual(panel.trace[:4], [('cs', 0), ('dc', 0),
                                          ('send', b'\xe0'), ('dc', 1)])
        self.assertEqual(panel.trace[-1], ('cs', 1))
        self.assertEqual([len(data) for kind, data in panel.trace
                          if kind == 'send'], [1, 48, 48, 4])

    def test_id_read_has_continuous_cs_and_no_reset(self):
        panel = make_panel()
        gcmd = Mock()
        panel.cmd_ID(gcmd)
        self.assertEqual(panel.trace, [
            ('cs', 0), ('dc', 0), ('send', b'\xd3'), ('dc', 1),
            ('transfer', b'\x00' * 4), ('cs', 1)])
        self.assertFalse(panel.initialized)
        self.assertIn('00779600', gcmd.respond_info.call_args[0][0])

    def test_id_failure_releases_cs(self):
        panel = make_panel()
        panel.spi.spi_transfer = Mock(side_effect=RuntimeError('offline'))
        with self.assertRaises(RuntimeError):
            panel.cmd_ID(Mock())
        self.assertEqual(panel.trace[-1], ('cs', 1))

    def test_initialization_requires_explicit_controller(self):
        panel = make_panel()
        gcmd = Mock()
        gcmd.get.return_value = ''
        gcmd.error.side_effect = ValueError
        with self.assertRaises(ValueError):
            panel.cmd_INIT(gcmd)
        self.assertFalse(panel.trace)
        self.assertFalse(panel.ops)

    def test_init_fences_before_starting_delay(self):
        panel = make_panel()
        gcmd = Mock()
        gcmd.get.return_value = 'ST7796'
        panel.cmd_INIT(gcmd)
        self.assertFalse(panel.trace)  # merely queues work
        self.assertAlmostEqual(panel._tick(4.), 5.150)
        self.assertEqual(panel.trace[-1], ('transfer', b''))
        self.assertFalse(panel.initialized)
        self.assertEqual(panel.ops[0][1], 0x11)

    def test_init_clears_entire_lcd_before_text(self):
        panel = make_panel()
        gcmd = Mock()
        gcmd.get.return_value = 'ST7796'
        panel.cmd_INIT(gcmd)
        operations = list(panel.ops)
        pixels = b''.join(op[1] for op in operations if op[0] == 'pixels')
        self.assertEqual(pixels, bytes(480 * 320 * 2))
        self.assertIn(('command', 0x2A, b'\x00\x00\x01\xdf', 0), operations)
        self.assertIn(('command', 0x2B, b'\x00\x00\x01\x3f', 0), operations)
        self.assertEqual(operations[-1], ('initialized',))

    def test_encoder_both_directions_and_press_suppression(self):
        panel = make_panel()
        decoder = FullStepRotaryEncoder(panel._cw, panel._ccw)
        for state in [3, 1, 0, 2, 3]:
            decoder.encoder_callback(1., state)
        for state in [3, 2, 0, 1, 3]:
            decoder.encoder_callback(2., state)
        for time, state in [(3., 1), (3.01, 0), (3.02, 1),
                            (3.03, 0), (4., 1), (4.1, 0)]:
            panel._click(time, state)
        self.assertEqual(panel.counts, dict(cw=1, ccw=1, press=2))
        self.assertFalse(panel.trace)  # input works without LCD initialization

    def test_printing_or_paused_prevents_hardware_writes(self):
        for state in ('printing', 'paused'):
            panel = make_panel()
            stats = Mock()
            stats.get_status.return_value = {'state': state}
            panel.printer.lookup_object.side_effect = (
                lambda name, default: stats if name == 'print_stats' else None)
            panel.ops.append(('command', 0x01, b'', .15))
            panel.initialized = True
            self.assertEqual(panel._tick(5.), panel.reactor.NEVER)
            self.assertFalse(panel.ops)
            self.assertFalse(panel.initialized)
            self.assertEqual(panel.trace, [('cs', 1)])

    def test_render_coalesces_updates_and_samples_actual_ip(self):
        panel = make_panel()
        with patch.object(b1_panel, 'host_ipv4',
                          return_value=[('wlan0', '192.0.2.25')]):
            panel._render()
            self.assertEqual(panel.last_lines[1].rstrip(), 'wlan0 192.0.2.25')
            # Each strip has 3 commands and bounded pixel packets.
            self.assertEqual(sum(len(op[1]) for op in panel.ops if op[0] == 'pixels'),
                             6 * 480 * 32 * 2)
            panel.ops.clear()
            panel._render()
            self.assertFalse(panel.ops)
            panel.counts['press'] += 1
            panel._render()
            self.assertEqual(sum(len(op[1]) for op in panel.ops
                                 if op[0] == 'pixels'), 16 * 32 * 2)

    def test_no_address_is_explicit_and_visuals_never_self_verify(self):
        panel = make_panel()
        with patch.object(b1_panel, 'host_ipv4', return_value=[]):
            panel._render()
        self.assertEqual(panel.last_lines[1].rstrip(), 'NO IPv4 ADDRESS')
        panel.initialized = True
        status = panel.get_status(5.)
        self.assertFalse(status['text_verified'])
        self.assertFalse(status['backlight_verified'])

    def test_stop_clears_pending_drawing_but_retains_counts(self):
        panel = make_panel()
        panel.counts['cw'] = 12
        panel.ops.append(('command', 0x01, b'', .15))
        panel.initialized = True
        panel.cmd_STOP(Mock())
        self.assertFalse(panel.ops)
        self.assertFalse(panel.initialized)
        self.assertEqual(panel.counts['cw'], 12)
        self.assertEqual(panel.trace, [('cs', 1)])

    def test_rectangle_stream_holds_cs_across_pixel_packets(self):
        panel = make_panel()
        payload = bytes(range(60))
        panel._queue_rectangle(0, 0, 29, 0, payload)
        panel._tick(5.)
        start = panel.trace.index(('send', b'\x2c'))
        self.assertEqual(panel.trace[start+1:], [
            ('cs', 1), ('cs', 0), ('dc', 1),
            ('send', payload[:48]), ('send', payload[48:]), ('cs', 1)])
        self.assertFalse(panel.ops)


if __name__ == '__main__':
    unittest.main()

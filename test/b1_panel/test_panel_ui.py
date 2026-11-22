import importlib.util
import json
import pathlib
import sys
import unittest
from unittest.mock import Mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'klippy'))
from extras.b1_panel_ui import PanelUI, status_lines, validate_ipv4

spec = importlib.util.spec_from_file_location('panel_host', ROOT / 'scripts/b1-panel-host.py')
host_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host_module)


class UITests(unittest.TestCase):
    def test_status_states_temperatures_fans_and_eta(self):
        sample = {'print_stats': {'state': 'printing', 'print_duration': 600},
                  'display_status': {'progress': .25},
                  'extruder': {'temperature': 200, 'target': 200},
                  'heater_bed': {'temperature': 60, 'target': 60},
                  'fan': {'speed': .5}, 'heater_fan heatbreak_fan1': {'speed': 1.},
                  'temperature_fan SoC_fan': {'speed': .25}}
        lines = status_lines(sample, {'ip': '10.0.0.225'})
        self.assertEqual(lines[0], 'Printing')
        self.assertIn('0h10m', lines[1]); self.assertIn('~0h30m', lines[1])
        self.assertIn('P:50% H:100% C:25%', lines[3])
        self.assertEqual(lines[5], 'http://10.0.0.225:81/')
        sample['extruder']['temperature'] = 100
        self.assertEqual(status_lines(sample, {})[0], 'Warming')
        sample['print_stats']['state'] = 'complete'
        self.assertEqual(status_lines(sample, {})[0], 'Complete')
        self.assertEqual(status_lines({}, {})[0], 'Idle')

    def test_eta_is_unknown_until_progress_is_meaningful(self):
        for progress in (0, .001):
            lines = status_lines({'print_stats': {'state': 'printing', 'print_duration': 10},
                                  'display_status': {'progress': progress}}, {})
            self.assertIn('--:--', lines[1])

    def test_octets_wrap_both_directions_click_advance_and_review(self):
        ui = PanelUI(); ui.enter('manual'); ui.click()
        ui.octets = [255, 0, 0, 0]
        ui.rotate(1); self.assertEqual(ui.octets[0], 0)
        ui.rotate(-1); self.assertEqual(ui.octets[0], 255)
        ui.click(); self.assertEqual(ui.octet, 1)
        ui.rotate(-1); self.assertEqual(ui.octets[1], 255)
        ui.click(); ui.click(); ui.click()
        self.assertEqual(ui.mode, 'octet_review')
        self.assertEqual(ui.items(), ['Confirm', 'Edit', 'Change', 'Cancel'])
        ui.click(); self.assertEqual(ui.draft['address'], '255.255.0.0')
        self.assertFalse(ui.actions)  # editing alone never changes networking

    def test_password_masking_status_and_cancel(self):
        ui = PanelUI(); ui.enter('password'); ui.secret = 'SeCrEt!12345'
        self.assertNotIn(ui.secret, json.dumps(ui.lines({})))
        self.assertNotIn(ui.secret, json.dumps(ui.public_status()))
        ui.selection = ui.PASSWORD_KEYS.index('<CANCEL>'); ui.click()
        self.assertEqual(ui.secret, '')
        self.assertFalse(ui.actions)

    def test_password_entry_done_and_delete(self):
        ui = PanelUI(); ui.enter('password')
        for char in 'example123!':
            ui.selection = ui.PASSWORD_KEYS.index(char); ui.click()
        ui.selection = ui.PASSWORD_KEYS.index('<DELETE>'); ui.click()
        self.assertEqual(ui.secret, 'example123')
        ui.selection = ui.PASSWORD_KEYS.index('<DONE>'); ui.click()
        self.assertEqual(ui.mode, 'apply_review')

    def test_locked_network_refuses_apply_and_erases_secret(self):
        ui = PanelUI(); ui.ssid = 'Example'; ui.secret = 'secret123'
        ui.network = {'changes_allowed': False}
        ui.enter('apply_review'); ui.click()
        self.assertEqual(ui.secret, '')
        self.assertFalse(ui.actions)
        self.assertIn('locked', ui.lines({})[0])

    def test_apply_keep_and_timeout(self):
        ui = PanelUI(); ui.ssid = 'Example'; ui.secret = 'secret123'
        ui.network = {'changes_allowed': True}
        ui.enter('apply_review'); ui.click()
        self.assertEqual(ui.actions[0]['action'], 'apply')
        self.assertEqual(ui.secret, '')
        ui.actions.clear(); ui.result('apply', {'ok': True})
        ui.update_network({'checkpoint': True, 'state': 100, 'ip': '192.0.2.3'})
        self.assertEqual(ui.mode, 'keep')
        ui.update_network({'checkpoint': False})
        self.assertEqual(ui.mode, 'message')
        self.assertIn('rolled back', ui.lines({})[0])

    def test_shutdown_requires_deliberate_confirm_default_cancel(self):
        ui = PanelUI(); ui.enter('main'); ui.selection = 3; ui.click()
        ui.click(); self.assertEqual(ui.mode, 'main'); self.assertFalse(ui.actions)
        ui.selection = 3; ui.click(); ui.selection = 1; ui.click()
        self.assertEqual(ui.actions, [{'action': 'poweroff'}])
        self.assertIn('Wait at least 30 seconds', ui.lines({}))
        self.assertNotIn('Safe to switch off', ui.lines({}))

    def test_brightness_limited_and_saved_separately(self):
        ui = PanelUI(); ui.enter('brightness'); ui.rotate(-100)
        self.assertEqual(ui.brightness, 10)
        ui.rotate(100); self.assertEqual(ui.brightness, 100)
        ui.click(); self.assertEqual(ui.actions, [{'action': 'brightness', 'value': 100}])

    def test_static_validation(self):
        valid = dict(method='manual', address='192.168.1.10', prefix=24,
                     gateway='192.168.1.1', dns='1.1.1.1')
        validate_ipv4(valid)
        for update in ({'address': '192.168.1.0'}, {'address': '127.0.0.1'},
                       {'gateway': '10.0.0.1'}, {'prefix': 33}, {'dns': '300.1.1.1'}):
            with self.assertRaises(ValueError):
                validate_ipv4(dict(valid, **update))

    def test_host_gate_blocks_before_any_network_operation(self):
        host = host_module.Host.__new__(host_module.Host)
        host.config = {'allow_network_changes': False}
        host.nm = Mock(); host.settings = Mock()
        with self.assertRaises(ValueError):
            host.apply({'password': 'DoNotLogMe'})
        self.assertFalse(host.nm.mock_calls)
        self.assertFalse(host.settings.mock_calls)

    def test_recovery_required_even_if_changes_enabled(self):
        host = host_module.Host.__new__(host_module.Host)
        host.config = {'allow_network_changes': True}
        host.ethernet_recovery = Mock(return_value=False)
        self.assertFalse(host.changes_allowed())
        host.config['serial_recovery_approved'] = True
        self.assertTrue(host.changes_allowed())

    def test_explicit_deferred_recovery_unlock(self):
        host = host_module.Host.__new__(host_module.Host)
        host.config = {'allow_network_changes': True, 'require_recovery': False}
        host.ethernet_recovery = Mock(return_value=False)
        self.assertTrue(host.changes_allowed())
        host.config['allow_network_changes'] = False
        self.assertFalse(host.changes_allowed())

    def test_jog_menu_never_moves_on_rotation_or_cancel(self):
        ui = PanelUI(); ui.enter('motion'); ui.click()
        self.assertEqual(ui.mode, 'jog')
        ui.rotate(3); self.assertEqual(ui.jog_delta, 3.)
        self.assertFalse(ui.actions)
        ui.click(); self.assertEqual(ui.mode, 'action_confirm')
        ui.click(); self.assertFalse(ui.actions)  # default Cancel

    def test_home_requires_confirmation(self):
        ui = PanelUI(); ui.enter('motion'); ui.selection = 4; ui.click()
        self.assertFalse(ui.actions)
        ui.selection = 1; ui.click()
        self.assertEqual(ui.actions, [{'action': 'home', 'axes': 'XY'}])

    def test_motion_commands_require_homing_idle_and_bounds(self):
        data = dict(state='standby', busy=False, homed='xyz', position=[150, 150, 10],
                    minimum=[0, 0, 0], maximum=[300, 300, 300])
        command = host_module.build_printer_command(dict(action='jog', axis='Z', distance=-1), data)
        self.assertIn('G91\nG1 Z-1.000 F300', command)
        self.assertIn('RESTORE_GCODE_STATE NAME=BTT_PANEL_JOG', command)
        for change in ({'homed': ''}, {'state': 'printing'}, {'busy': True}, {'position': [150, 150, 0]}):
            with self.assertRaises(ValueError):
                host_module.build_printer_command(dict(action='jog', axis='Z', distance=-1), dict(data, **change))
        for distance in (float('nan'), float('inf'), 51):
            with self.assertRaises(ValueError):
                host_module.build_printer_command(dict(action='jog', axis='X', distance=distance), data)
        with self.assertRaises(ValueError):
            host_module.build_printer_command(dict(action='home', axes='Z'), dict(data, homed=''))

    def test_heaters_fan_and_print_actions_validated(self):
        data = dict(state='standby', nozzle_max=265, bed_max=105)
        self.assertEqual(host_module.build_printer_command(dict(action='heater', heater='bed', target=60), data), 'M140 S60.0')
        self.assertEqual(host_module.build_printer_command(dict(action='fan', percent=50), data), 'M106 S128')
        self.assertEqual(host_module.build_printer_command(dict(action='heater', heater='all', target=0), data), 'TURN_OFF_HEATERS')
        for request in [dict(action='heater', heater='nozzle', target=999),
                        dict(action='fan', percent=101), dict(action='pause')]:
            with self.assertRaises(ValueError):
                host_module.build_printer_command(request, data)

    def test_one_confirmation_heats_then_feeds_only_when_hot(self):
        ui = PanelUI(); ui.enter('printer')
        ui.selection = ui.items().index('Load filament'); ui.click()
        self.assertEqual(ui.mode, 'filament')
        self.assertFalse(ui.actions)
        ui.click()  # default Cancel never heats or feeds
        self.assertEqual(ui.mode, 'printer')
        ui.selection = ui.items().index('Load filament'); ui.click()
        ui.selection = 1; ui.click()
        self.assertEqual(ui.actions.pop(), dict(action='filament_heat', target=210))
        self.assertEqual(ui.mode, 'filament_heating')
        data = dict(state='paused', paused=True, nozzle_target=210,
                    nozzle_temperature=200, can_extrude=True)
        ui.update_network(dict(printer=data))
        self.assertFalse(ui.actions)  # no feeding until heater command acknowledged
        ui.result('filament_heat', {'ok': True})
        ui.update_network(dict(printer=data))
        self.assertFalse(ui.actions)
        ui.update_network(dict(printer=dict(data, nozzle_temperature=210)))
        self.assertEqual(ui.actions, [dict(action='load', length=20, temperature=210)])
        ui.update_network(dict(printer=dict(data, nozzle_temperature=211)))
        ui.click(); ui.click()  # cannot queue more feed while feeding
        self.assertEqual(len(ui.actions), 1)
        self.assertTrue(ui.waiting)
        ui.result('load', {'ok': True})
        self.assertEqual(ui.mode, 'filament_done')
        self.assertIn('Nozzle stays HOT', ui.lines({}))
        ui.click(); self.assertEqual(ui.mode, 'status')
        self.assertNotIn(dict(action='resume'), ui.actions)

    def test_unload_length_temperature_limits_and_no_accidental_feed(self):
        ui = PanelUI(); ui.printer_data = dict(nozzle_min=170, nozzle_max=245, nozzle_target=230)
        ui.enter('printer'); ui.selection = ui.items().index('Unload filament'); ui.click()
        self.assertEqual(ui.filament_temperature, 230)
        ui.selection = 2; ui.click(); ui.rotate(1000); self.assertEqual(ui.filament_temperature, 245)
        ui.rotate(-1000); self.assertEqual(ui.filament_temperature, 170)
        ui.click(); ui.selection = 3; ui.click(); ui.rotate(-1000)
        self.assertEqual(ui.filament_length, 5)
        ui.rotate(1000); self.assertEqual(ui.filament_length, 50)
        self.assertFalse(ui.actions)
        ui.click(); ui.selection = 1; ui.click()
        self.assertEqual(ui.actions.pop(), dict(action='filament_heat', target=170))
        ui.result('filament_heat', dict(ok=True))
        ui.update_network(dict(printer=dict(state='standby', paused=False,
            nozzle_target=170, nozzle_temperature=170, can_extrude=True)))
        self.assertEqual(ui.actions, [dict(action='unload', length=50, temperature=170)])
        ui.result('unload', dict(ok=True))
        self.assertIn('Release lever; gently pull out', ui.lines({}))

    def test_paused_dashboard_resume_shortcut_and_menu_access(self):
        snapshot = dict(print_stats=dict(state='printing'), pause_resume=dict(is_paused=True))
        ui = PanelUI()
        self.assertEqual(ui.lines(snapshot)[0], 'Paused')
        self.assertIn('> Resume print', ui.lines(snapshot)[-1])
        ui.rotate(1); self.assertIn('> Menu', ui.lines(snapshot)[-1])
        ui.click(); self.assertEqual(ui.mode, 'main')
        ui.enter('status'); ui.lines(snapshot); ui.click()
        self.assertEqual(ui.mode, 'action_confirm'); self.assertFalse(ui.actions)
        ui.click(); self.assertEqual(ui.mode, 'status')
        ui.click(); ui.selection = 1; ui.click()
        self.assertEqual(ui.actions, [dict(action='resume')])
        ui.result('resume', {'ok': True}); ui.click()
        ui.lines(dict(print_stats=dict(state='printing'), pause_resume=dict(is_paused=False)))
        self.assertEqual(ui.lines({})[-1], 'Click: Menu')
        ui.click(); self.assertEqual(ui.mode, 'main')

    def test_resume_obeys_actual_pause_state_after_idle_timeout(self):
        self.assertEqual(host_module.build_printer_command(dict(action='resume'),
                         dict(state='printing', paused=True)), 'RESUME')
        with self.assertRaises(ValueError):
            host_module.build_printer_command(dict(action='resume'), dict(state='paused', paused=False))

    def test_filament_feed_hot_idle_or_paused_preserves_state_and_limits(self):
        data = dict(state='standby', busy=False, paused=False, nozzle_min=170,
                    nozzle_max=245, nozzle_temperature=210, nozzle_target=210,
                    can_extrude=True, extrude_limit=20)
        for action, sign, rate in [('load', '', 300), ('unload', '-', 600)]:
            command = host_module.build_printer_command(
                dict(action=action, length=50, temperature=210), data)
            self.assertIn('M83\nG1 E%s20.000 F%d' % (sign, rate), command)
            self.assertIn('G1 E%s10.000 F%d' % (sign, rate), command)
            self.assertTrue(command.endswith('RESTORE_GCODE_STATE NAME=BTT_PANEL_FILAMENT\nM400'))
            self.assertNotIn('MOVE=', command)
            self.assertNotIn('RESUME', command)
            self.assertNotIn('G28', command)
            paused = dict(data, state='paused', busy=True, paused=True)
            self.assertEqual(command, host_module.build_printer_command(
                dict(action=action, length=50, temperature=210), paused))
        for change in [dict(state='printing', busy=True), dict(busy=True),
                       dict(can_extrude=False), dict(nozzle_temperature=190),
                       dict(nozzle_target=0), dict(extrude_limit=0)]:
            with self.assertRaises(ValueError):
                host_module.build_printer_command(dict(action='load', length=50, temperature=210),
                                                 dict(data, **change))
        for change in [dict(length=0), dict(length=51), dict(length=float('nan')),
                       dict(temperature=0), dict(temperature=250), dict(temperature=float('inf'))]:
            with self.assertRaises(ValueError):
                host_module.build_printer_command(dict(dict(action='unload', length=50, temperature=210),
                                                      **change), data)

    def test_duplicate_filament_confirmation_is_not_queued(self):
        ui = PanelUI(); ui.waiting = True
        ui.confirm('Load?', dict(action='load', length=50, temperature=210), back='filament')
        ui.selection = 1; ui.click()
        self.assertFalse(ui.actions)
        self.assertIn('in progress', ui.lines({})[0])

    def test_cancel_heating_ignores_late_ack_and_never_feeds(self):
        ui = PanelUI(); ui.enter('printer'); ui.selection = 6; ui.click()
        ui.selection = 1; ui.click(); ui.actions.clear()
        ui.click()
        self.assertEqual(ui.actions, [dict(action='filament_stop', target=0)])
        ui.result('filament_heat', dict(ok=True))
        ui.update_network(dict(printer=dict(state='standby', nozzle_target=210,
            nozzle_temperature=210, can_extrude=True)))
        self.assertEqual(len(ui.actions), 1)
        ui.result('filament_stop', dict(ok=True))
        self.assertIn('No filament was fed', ui.lines({}))

    def test_heat_failure_timeout_changed_target_or_started_print_never_feeds(self):
        for scenario in ('failure', 'timeout', 'target', 'printing'):
            with self.subTest(scenario=scenario):
                ui = PanelUI(); ui.enter('printer'); ui.selection = 6; ui.click()
                ui.selection = 1; ui.click(); ui.actions.clear()
                if scenario == 'failure':
                    ui.result('filament_heat', dict(ok=False, error='No heater'))
                else:
                    ui.result('filament_heat', dict(ok=True))
                data = dict(state='standby', paused=False, nozzle_target=210,
                            nozzle_temperature=210, can_extrude=True)
                if scenario == 'timeout': ui.filament_deadline = 0
                if scenario == 'target': data['nozzle_target'] = 0
                if scenario == 'printing': data['state'] = 'printing'
                ui.update_network(dict(printer=data))
                self.assertIsNone(ui.filament_phase)
                self.assertFalse(any(a['action'] in ('load', 'unload') for a in ui.actions))

    def test_filament_heat_uses_nonblocking_command_and_validates_state(self):
        data = dict(state='standby', busy=False, paused=False, nozzle_min=170, nozzle_max=245)
        self.assertEqual(host_module.build_printer_command(dict(action='filament_heat', target=210), data), 'M104 S210.0')
        self.assertEqual(host_module.build_printer_command(dict(action='filament_stop', target=0), dict(data, busy=True)), 'M104 S0.0')
        for action in ('filament_heat', 'filament_stop'):
            with self.assertRaises(ValueError):
                host_module.build_printer_command(dict(action=action, target=210), dict(data, state='printing'))
        for target in (0, 250, float('nan')):
            with self.assertRaises(ValueError):
                host_module.build_printer_command(dict(action='filament_heat', target=target), data)


if __name__ == '__main__':
    unittest.main()

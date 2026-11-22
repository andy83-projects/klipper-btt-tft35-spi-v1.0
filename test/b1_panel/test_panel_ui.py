import importlib.util
import io
import json
import pathlib
import sys
import unittest
import unittest.mock
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
        self.assertEqual(ui.items(), ['Confirm', 'Edit', 'Change', 'Back'])
        ui.click(); self.assertEqual(ui.draft['address'], '255.255.0.0')
        self.assertFalse(ui.actions)  # editing alone never changes networking

    def test_password_masking_status_and_cancel(self):
        ui = PanelUI(); ui.enter('password'); ui.secret = 'SeCrEt!12345'
        self.assertNotIn(ui.secret, json.dumps(ui.lines({})))
        self.assertNotIn(ui.secret, json.dumps(ui.public_status()))
        ui.selection = ui.PASSWORD_KEYS.index('<BACK>'); ui.click()
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
        ui = PanelUI(); ui.enter('main'); ui.selection = ui.items().index('Shut down'); ui.click()
        ui.click(); self.assertEqual(ui.mode, 'main'); self.assertFalse(ui.actions)
        ui.selection = ui.items().index('Shut down'); ui.click(); ui.selection = 0; ui.click()
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
        ui.click(); self.assertFalse(ui.actions)  # default Back
        self.assertEqual(ui.mode, 'jog')

    def test_jog_success_returns_to_same_axis_ready_for_another_move(self):
        ui = PanelUI(); ui.enter('motion'); ui.selection = 2; ui.click()
        ui.rotate(3); ui.click(); ui.selection = 0; ui.click()
        self.assertTrue(ui.jog_pending)
        self.assertEqual(ui.actions.pop(), dict(action='jog', axis='Z', distance=3))
        ui.click(); ui.rotate(20)
        self.assertFalse(ui.actions)  # no repeat while the first move is running
        ui.result('jog', dict(ok=True))
        self.assertEqual((ui.mode, ui.jog_axis, ui.jog_delta), ('jog', 'Z', 0))
        ui.rotate(-2); ui.click(); ui.selection = 0; ui.click()
        self.assertEqual(ui.actions.pop(), dict(action='jog', axis='Z', distance=-2))
        ui.result('jog', dict(ok=True)); ui.click()
        self.assertEqual(ui.mode, 'motion')  # zero + click still leaves jog

    def test_failed_jog_returns_to_edit_the_proposed_distance(self):
        ui = PanelUI(); ui.enter('motion'); ui.click(); ui.rotate(-10)
        ui.click(); ui.selection = 0; ui.click(); ui.actions.clear()
        ui.result('jog', dict(ok=False, error='Jog exceeds configured axis limits'))
        self.assertFalse(ui.jog_pending)
        ui.click(); self.assertEqual(ui.mode, 'jog')
        self.assertEqual(ui.jog_delta, -10)
        ui.rotate(9); self.assertEqual(ui.jog_delta, -1)
        self.assertFalse(ui.actions)

    def test_home_requires_confirmation(self):
        ui = PanelUI(); ui.enter('motion'); ui.selection = 4; ui.click()
        self.assertFalse(ui.actions)
        ui.selection = 0; ui.click()
        self.assertEqual(ui.actions, [{'action': 'home', 'axes': 'XY'}])

    def test_motion_commands_require_homing_idle_and_bounds(self):
        data = dict(state='standby', busy=False, homed='xyz', position=[150, 150, 10],
                    minimum=[0, 0, 0], maximum=[300, 300, 300])
        command = host_module.build_printer_command(dict(action='jog', axis='Z', distance=-1), data)
        self.assertIn('G91\nG1 Z-1.000 F300', command)
        self.assertIn('RESTORE_GCODE_STATE NAME=BTT_PANEL_JOG', command)
        self.assertTrue(command.endswith('\nM400'))
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
        ui.click()  # default Back never heats or feeds
        self.assertEqual(ui.mode, 'printer')
        ui.selection = ui.items().index('Load filament'); ui.click()
        ui.selection = 0; ui.click()
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
        ui.selection = 3; ui.click(); self.assertEqual(ui.mode, 'status')
        self.assertNotIn(dict(action='resume'), ui.actions)

    def test_filament_controls_allow_repeated_extrude_and_retract_until_done(self):
        hot = dict(state='paused', paused=True, nozzle_target=210,
                   nozzle_temperature=210, can_extrude=True)
        ui = PanelUI(); ui.printer_data = dict(hot)
        ui.enter('printer'); ui.selection = ui.items().index('Load filament'); ui.click()
        self.assertEqual(ui.items()[-1], 'Back')
        self.assertEqual(ui.selection, 3)  # cursor starts on Back
        ui.selection = 0; ui.click()
        self.assertEqual(ui.actions.pop(), dict(action='filament_heat', target=210))
        ui.result('filament_heat', dict(ok=True))
        ui.update_network(dict(printer=dict(hot)))
        self.assertEqual(ui.actions.pop(), dict(action='load', length=20, temperature=210))
        ui.result('load', dict(ok=True))
        # Extrude more / Retract more feed again straight away, no menu detour.
        for action in ('load', 'unload', 'unload', 'load'):
            self.assertEqual(ui.mode, 'filament_done')
            self.assertEqual(ui.items(), ['Extrude more (20 mm)', 'Retract more (20 mm)',
                                          'Turn nozzle off', 'Back'])
            self.assertFalse(ui.waiting)
            self.assertIsNone(ui.filament_phase)
            ui.selection = 0 if action == 'load' else 1; ui.click()
            self.assertEqual(ui.mode, 'filament_feeding')
            self.assertEqual(ui.actions.pop(), dict(action=action, length=20, temperature=210))
            ui.click(); self.assertFalse(ui.actions)  # no second feed while feeding
            ui.result(action, dict(ok=True))
            self.assertEqual(ui.selection, 0 if action == 'load' else 1)
        ui.selection = 3; ui.click()
        self.assertEqual(ui.mode, 'status')
        self.assertFalse(ui.actions)

    def test_repeat_feed_after_cooling_reports_error_and_returns_to_setup(self):
        ui = PanelUI(); ui.filament_action = 'load'; ui.enter('filament_done')
        ui.selection = 1; ui.click()
        self.assertEqual(ui.actions.pop(), dict(action='unload', length=20, temperature=210))
        ui.result('unload', dict(ok=False, error='Heat nozzle first; wait for temp'))
        self.assertIn('Heat nozzle first', ui.lines({})[0])
        ui.click(); self.assertEqual(ui.mode, 'filament')
        self.assertEqual(ui.filament_action, 'unload')

    def test_confirm_screens_put_back_last_and_start_on_it(self):
        ui = PanelUI(); ui.confirm('Emergency print?', dict(action='emergency'))
        self.assertEqual(ui.items(), ['Confirm', 'Back'])
        self.assertEqual(ui.selection, 1)
        ui.click(); self.assertFalse(ui.actions); self.assertEqual(ui.mode, 'printer')
        ui.enter('shutdown_confirm')
        self.assertEqual(ui.items(), ['Confirm shutdown', 'Back'])
        self.assertEqual(ui.selection, 1)
        for mode in ('ip_review', 'octet_review', 'apply_review'):
            ui.enter(mode); self.assertEqual(ui.items()[-1], 'Back')
        self.assertEqual(ui.PASSWORD_KEYS[-1], '<BACK>')
        self.assertFalse(any('Cancel' == item for mode in (
            'filament', 'filament_done', 'action_confirm', 'shutdown_confirm',
            'ip_review', 'octet_review', 'apply_review')
            for item in (ui.enter(mode) or ui.items())))

    def test_filament_feed_failure_keeps_the_controls_reachable(self):
        ui = PanelUI(); ui.filament_action = 'unload'; ui.waiting = True
        ui.filament_phase = 'feeding'; ui.enter('filament_feeding')
        ui.result('unload', dict(ok=False, error='Heat nozzle first'))
        self.assertFalse(ui.waiting)
        self.assertIsNone(ui.filament_phase)
        ui.click(); self.assertEqual(ui.mode, 'filament')
        self.assertEqual(ui.filament_action, 'unload')
        self.assertFalse(ui.actions)

    def test_unload_length_temperature_limits_and_no_accidental_feed(self):
        ui = PanelUI(); ui.printer_data = dict(nozzle_min=170, nozzle_max=245, nozzle_target=230)
        ui.enter('printer'); ui.selection = ui.items().index('Unload filament'); ui.click()
        self.assertEqual(ui.filament_temperature, 230)
        ui.selection = 1; ui.click(); ui.rotate(1000); self.assertEqual(ui.filament_temperature, 245)
        ui.rotate(-1000); self.assertEqual(ui.filament_temperature, 170)
        ui.click(); ui.selection = 2; ui.click(); ui.rotate(-1000)
        self.assertEqual(ui.filament_length, 5)
        ui.rotate(1000); self.assertEqual(ui.filament_length, 50)
        self.assertFalse(ui.actions)
        ui.click(); ui.selection = 0; ui.click()
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
        ui.click(); ui.selection = 0; ui.click()
        self.assertEqual(ui.actions, [dict(action='resume')])
        ui.result('resume', {'ok': True}); ui.click()
        ui.lines(dict(print_stats=dict(state='printing'), pause_resume=dict(is_paused=False)))
        self.assertEqual(ui.lines({})[-1], '> Menu    Adjust Z offset')
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
        ui.selection = 0; ui.click()
        self.assertFalse(ui.actions)
        self.assertIn('in progress', ui.lines({})[0])

    def test_cancel_heating_ignores_late_ack_and_never_feeds(self):
        ui = PanelUI(); ui.enter('printer'); ui.selection = ui.items().index('Load filament'); ui.click()
        ui.selection = 0; ui.click(); ui.actions.clear()
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
                ui = PanelUI(); ui.enter('printer'); ui.selection = ui.items().index('Load filament'); ui.click()
                ui.selection = 0; ui.click(); ui.actions.clear()
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


class EasyUseTests(unittest.TestCase):
    HOST = dict(state='standby', busy=False, paused=False, nozzle_max=245, bed_max=125,
                homed='xyz', z_offset=0., has_probe=True, has_mesh=True,
                has_z_tilt=True, mesh_command='_BED_MESH_CALIBRATE')

    def test_print_a_file_lists_confirms_and_starts(self):
        ui = PanelUI(); ui.enter('main')
        self.assertEqual(ui.items()[0], 'Print a file')
        ui.click()
        self.assertEqual(ui.actions.pop(), dict(action='files'))
        self.assertIn('Loading', ui.lines({})[0])
        files = [dict(path='BB1ABL_Boost cup.gcode', name='BB1ABL_Boost cup'),
                 dict(path='toys/egg.gcode', name='egg')]
        ui.result('files', dict(ok=True, files=files))
        self.assertEqual(ui.items(), ['BB1ABL_Boost cup', 'egg', 'Back'])
        ui.selection = 1; ui.click()
        self.assertEqual(ui.mode, 'action_confirm')
        lines = ui.lines({})
        self.assertEqual(lines[:3], ['Print this file?', 'egg', 'Remove anything on the bed'])
        self.assertEqual(lines[-1], '> Back')  # stray click stays harmless
        ui.click(); self.assertFalse(ui.actions); self.assertEqual(ui.mode, 'files')
        ui.selection = 1; ui.click(); ui.selection = 0; ui.click()
        self.assertEqual(ui.actions.pop(), dict(action='print', filename='toys/egg.gcode'))
        ui.result('print', dict(ok=True))
        self.assertIn('Print starting', ui.lines({})[0])
        ui.click(); self.assertEqual(ui.mode, 'status')

    def test_empty_file_list_explains_what_to_do(self):
        ui = PanelUI(); ui.enter('main'); ui.click(); ui.actions.clear()
        ui.result('files', dict(ok=True, files=[]))
        self.assertIn('No files', ui.lines({})[0])
        ui.click(); self.assertEqual(ui.mode, 'main')

    def test_host_lists_newest_gcode_and_only_starts_listed_files(self):
        host = host_module.Host.__new__(host_module.Host); host.job = None
        listing = [dict(path='old.gcode', modified=1), dict(path='notes.txt', modified=9),
                   dict(path='sub/new.gcode', modified=5)]
        host.require_idle = Mock()
        with unittest.mock.patch.object(host_module.urllib.request, 'urlopen') as urlopen:
            urlopen.return_value = io.BytesIO(json.dumps(dict(result=listing)).encode())
            result = host.dispatch(dict(action='files'))
            self.assertEqual(result['files'], [dict(path='sub/new.gcode', name='new'),
                                               dict(path='old.gcode', name='old')])
            urlopen.side_effect = [io.BytesIO(json.dumps(dict(result=listing)).encode())]
            with self.assertRaises(ValueError):
                host.start_print(dict(filename='../../etc/passwd.gcode'))
            urlopen.side_effect = [io.BytesIO(json.dumps(dict(result=listing)).encode()),
                                   io.BytesIO(b'{"result": "ok"}')]
            host.start_print(dict(filename='sub/new.gcode'))
            request = urlopen.call_args[0][0]
            self.assertEqual(request.full_url,
                             'http://127.0.0.1:7125/printer/print/start?filename=sub/new.gcode')
            self.assertEqual(request.get_method(), 'POST')

    def test_preheat_presets_and_cool_down(self):
        ui = PanelUI(); ui.enter('heat')
        self.assertEqual(ui.items()[:3], ['Preheat PLA 210/60', 'Preheat PETG 220/65', 'Cool down'])
        self.assertEqual(ui.items()[-1], 'Back')
        ui.selection = 1; ui.click(); ui.selection = 0; ui.click()
        self.assertEqual(ui.actions.pop(), dict(action='preheat', nozzle=220, bed=65))
        ui.enter('heat'); ui.selection = 2; ui.click(); ui.selection = 0; ui.click()
        self.assertEqual(ui.actions.pop(), dict(action='heater', heater='all', target=0))
        command = host_module.build_printer_command(dict(action='preheat', nozzle=210, bed=60), self.HOST)
        self.assertEqual(command, 'M104 S210.0\nM140 S60.0')
        for change, request in [(dict(state='printing'), dict(nozzle=210, bed=60)),
                                (dict(busy=True), dict(nozzle=210, bed=60)),
                                ({}, dict(nozzle=300, bed=60)), ({}, dict(nozzle=210, bed=200)),
                                ({}, dict(nozzle=float('nan'), bed=60))]:
            with self.assertRaises(ValueError):
                host_module.build_printer_command(dict(action='preheat', **request),
                                                  dict(self.HOST, **change))

    def test_live_z_offset_from_printing_status(self):
        ui = PanelUI()
        ui.lines(dict(print_stats=dict(state='printing'), gcode_move=dict(homing_origin=[0, 0, 0])))
        ui.rotate(1); ui.click()
        self.assertEqual(ui.mode, 'zoffset')
        ui.rotate(1)
        self.assertEqual(ui.actions.pop(), dict(action='z_adjust', delta=.025))
        ui.rotate(1); ui.rotate(1)  # accumulates while the first is in flight
        self.assertFalse(ui.actions)
        self.assertIn('+0.075', ui.lines({})[1])
        ui.result('z_adjust', dict(ok=True))
        self.assertEqual(ui.actions.pop(), dict(action='z_adjust', delta=.05))
        ui.result('z_adjust', dict(ok=True))
        self.assertIn('+0.075', ui.lines({})[1])
        ui.click(); self.assertEqual(ui.mode, 'status')  # printing: straight back
        self.assertEqual(host_module.build_printer_command(dict(action='z_adjust', delta=-.025), self.HOST),
                         'SET_GCODE_OFFSET Z_ADJUST=-0.025 MOVE=1')
        self.assertEqual(host_module.build_printer_command(dict(action='z_adjust', delta=.025),
                         dict(self.HOST, homed='')), 'SET_GCODE_OFFSET Z_ADJUST=0.025')
        for delta, offset in ((.5, 0), (float('inf'), 0), (.025, .99)):
            with self.assertRaises(ValueError):
                host_module.build_printer_command(dict(action='z_adjust', delta=delta),
                                                  dict(self.HOST, z_offset=offset))

    def test_z_offset_is_capped_and_saved_only_when_idle(self):
        ui = PanelUI(); ui.enter('printer')
        ui.selection = ui.items().index('Adjust Z offset'); ui.click()
        for _ in range(60):
            ui.rotate(-1)
            if ui.actions:
                ui.actions.pop(); ui.result('z_adjust', dict(ok=True))
        self.assertAlmostEqual(ui.z_offset, -1.0)
        ui.click(); self.assertEqual(ui.mode, 'zoffset_menu')
        self.assertEqual(ui.items(), ['Save for future prints', 'Adjust again', 'Back'])
        ui.click(); self.assertEqual(ui.mode, 'action_confirm')
        self.assertIn('Printer restarts (~30 s)', ui.lines({}))
        ui.selection = 0; ui.click()
        self.assertEqual(ui.actions.pop(), dict(action='z_save'))
        ui.result('z_save', dict(ok=True)); self.assertEqual(ui.mode, 'job')
        self.assertEqual(host_module.build_job_scripts(dict(action='z_save'), dict(self.HOST, z_offset=-.1)),
                         ['Z_OFFSET_APPLY_PROBE', 'SAVE_CONFIG'])
        for change in (dict(z_offset=0), dict(state='printing', z_offset=.1),
                       dict(has_probe=False, z_offset=.1)):
            with self.assertRaises(ValueError):
                host_module.build_job_scripts(dict(action='z_save'), dict(self.HOST, **change))

    def test_runout_pause_offers_load_then_resume(self):
        ui = PanelUI()
        snapshot = dict(print_stats=dict(state='paused'), pause_resume=dict(is_paused=True),
                        **{'filament_switch_sensor material_0': dict(enabled=True, filament_detected=False)})
        lines = ui.lines(snapshot)
        self.assertEqual(lines[0], 'Out of filament')
        self.assertIn('Load new filament,', lines)
        self.assertEqual(lines[-1], '> Load    Resume    Menu')
        ui.click(); self.assertEqual((ui.mode, ui.filament_action), ('filament', 'load'))
        ui.selection = 3; ui.click(); self.assertEqual(ui.mode, 'status')
        ui.lines(snapshot); ui.rotate(1); ui.click()
        self.assertEqual(ui.confirm_action, dict(action='resume'))
        # Normal (non-runout) pause keeps the two-choice layout.
        snapshot['filament_switch_sensor material_0']['filament_detected'] = True
        ui.enter('status'); self.assertEqual(ui.lines(snapshot)[-1], '> Resume print    Menu')

    def test_finished_print_screen_waits_for_cool_bed_and_offers_reprint(self):
        ui = PanelUI()
        snapshot = dict(print_stats=dict(state='complete', filename='toys/egg.gcode',
                                         print_duration=3725), heater_bed=dict(temperature=58),
                        gcode_move=dict(homing_origin=[0, 0, .05]))
        lines = ui.lines(snapshot)
        self.assertEqual(lines[:4], ['Print complete!', 'egg', 'Took 1h02m', 'Bed 58 C: let it cool'])
        self.assertEqual(lines[5:], ['> Print again', '  Save Z offset', '  Done'])
        snapshot['heater_bed']['temperature'] = 30
        self.assertEqual(ui.lines(snapshot)[3], 'Bed is cool: remove print')
        ui.click(); self.assertEqual(ui.confirm_action, dict(action='print', filename='toys/egg.gcode'))
        self.assertIn('Remove the old print first', ui.lines({}))
        ui.click(); self.assertEqual(ui.mode, 'status')  # Back by default
        ui.lines(snapshot); ui.selection = 2; ui.click()
        self.assertEqual(ui.lines(snapshot)[-1], 'Click: Menu')  # dismissed
        snapshot['gcode_move']['homing_origin'] = [0, 0, 0]
        ui.finished_dismissed = None
        self.assertEqual(ui.lines(snapshot)[5:], ['> Print again', '  Done'])

    def test_calibrate_bed_runs_as_background_job(self):
        ui = PanelUI(); ui.enter('printer')
        ui.selection = ui.items().index('Calibrate bed'); ui.click()
        self.assertIn('Bed must be empty', ui.lines({}))
        self.assertEqual(ui.selection, 1)
        ui.selection = 0; ui.click()
        self.assertEqual(ui.actions.pop(), dict(action='calibrate'))
        ui.result('calibrate', dict(ok=True))
        self.assertEqual(ui.lines({})[0], 'Calibrating bed...')
        ui.update_network(dict(job=dict(name='calibrate', state='running')))
        self.assertEqual(ui.mode, 'job')
        ui.update_network(dict(job=dict(name='calibrate', state='failed', error='Probe triggered early')))
        self.assertEqual(ui.lines({})[:3], ['Calibration failed', 'Probe triggered early',
                                            'Nothing was saved'])
        scripts = host_module.build_job_scripts(dict(action='calibrate'), self.HOST)
        self.assertEqual(scripts, ['M140 S60\nG28\nM190 S60\nZ_TILT_ADJUST\nG28 Z\n'
                                   '_BED_MESH_CALIBRATE\nBED_MESH_PROFILE SAVE=default\nM140 S0',
                                   'SAVE_CONFIG'])
        self.assertNotIn('Z_TILT', host_module.build_job_scripts(
            dict(action='calibrate'), dict(self.HOST, has_z_tilt=False))[0])
        for change in (dict(state='printing'), dict(busy=True), dict(has_probe=False),
                       dict(has_mesh=False)):
            with self.assertRaises(ValueError):
                host_module.build_job_scripts(dict(action='calibrate'), dict(self.HOST, **change))

    def test_host_job_reports_failure_and_tolerates_save_config_restart(self):
        host = host_module.Host.__new__(host_module.Host)
        calls = []
        def gcode(script, timeout):
            calls.append(script)
            if script == 'SAVE_CONFIG':
                raise OSError('Klippy disconnected')
        host.gcode = gcode
        job = dict(name='calibrate', state='running', error='')
        host._run_job(job, ['G28', 'SAVE_CONFIG'])
        self.assertEqual((job['state'], calls), ('done', ['G28', 'SAVE_CONFIG']))
        error = host_module.urllib.error.HTTPError('u', 400, 'bad', {}, io.BytesIO(
            b'{"error": {"message": "Probe triggered prior to movement"}}'))
        host.gcode = Mock(side_effect=error)
        job = dict(name='calibrate', state='running', error='')
        host._run_job(job, ['G28', 'SAVE_CONFIG'])
        self.assertEqual(job, dict(name='calibrate', state='failed',
                                   error='Probe triggered prior to movement'))
        self.assertEqual(host.gcode.call_count, 1)  # never saves after a failure
        host.job = dict(state='running'); host.require_idle = Mock()
        with self.assertRaises(ValueError):
            host.start_job(dict(action='calibrate'))


if __name__ == '__main__':
    unittest.main()

"""
Z-PHOENIX — Frontend Local Preview Server
Run this script to preview and test the complete frontend UI in your browser:
    python preview_server.py

Port: http://localhost:8080 (or http://127.0.0.1:8080)
Note: This is a lightweight preview runner for Member 5 (Nandan) to test the UI.
      The official production app.py will be delivered by Member 2 (Kiran).
"""

from flask import Flask, render_template, jsonify, request
import time
import random

app = Flask(__name__, static_folder='static', template_folder='templates')

# ── 1. Page Routes ────────────────────────────────────────────────────────────

@app.route('/')
@app.route('/dashboard')
@app.route('/command-center')
def dashboard():
    return render_template('command_center.html', active_page='dashboard')

@app.route('/experiments')
@app.route('/chaos-lab')
def experiments():
    return render_template('chaos_lab.html', active_page='experiments')

@app.route('/agents')
def agents():
    return render_template('agents.html', active_page='agents')

@app.route('/playbooks')
def playbooks():
    return render_template('playbooks.html', active_page='playbooks')

@app.route('/incidents')
def incidents():
    return render_template('incidents.html', active_page='incidents')

@app.route('/audit')
def audit():
    return render_template('audit.html', active_page='audit')

# ── 2. Mock API Endpoints for Interactive Preview ─────────────────────────────

_state = {
    'system_state': 'healthy',
    'fault_active': False,
    'fault_scenario': None,
    'incident_id': 'INC-20261017-001'
}

@app.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'host': 'IBM LinuxONE s390x (Preview Mode)',
        'version': '1.0'
    })

@app.route('/api/telemetry')
def telemetry():
    if _state['fault_active']:
        cpu = min(98.5, 75.0 + random.uniform(5, 20))
        tps = max(5.0, 115.0 - random.uniform(90, 105))
        locks = int(220 + random.uniform(20, 90))
        sys_state = 'incident'
    else:
        cpu = 22.0 + random.uniform(1, 5)
        tps = 112.0 + random.uniform(-5, 10)
        locks = int(4 + random.uniform(0, 4))
        sys_state = 'healthy'

    return jsonify({
        'cpu': round(cpu, 1),
        'ram': 2.1,
        'ram_total': 4.0,
        'tps': round(tps, 1),
        'db2_locks': locks,
        'system_state': sys_state,
        'incident_text': 'Threshold breached: CPU high & Db2 locks spiking' if sys_state == 'incident' else '',
        'incident_ts': time.strftime('%Y-%m-%dT%H:%M:%SZ') if sys_state == 'incident' else None,
        'golden_path_stage': 'chaos_inject' if sys_state == 'incident' else 'baseline'
    })

@app.route('/api/attack', methods=['POST'])
def attack():
    data = request.get_json() or {}
    _state['fault_active'] = True
    _state['fault_scenario'] = data.get('scenario', 'cpu_spike')
    return jsonify({
        'status': 'ok',
        'message': f"Fault '{_state['fault_scenario']}' started in sandbox.",
        'incident_id': _state['incident_id']
    })

@app.route('/api/attack/stop', methods=['POST'])
def attack_stop():
    _state['fault_active'] = False
    return jsonify({
        'status': 'ok',
        'message': 'Kill switch executed. Fault processes terminated.'
    })

@app.route('/api/attack/status')
def attack_status():
    return jsonify({
        'active': _state['fault_active'],
        'result': 'Fault active' if _state['fault_active'] else 'System healthy'
    })

@app.route('/api/analyze', methods=['POST'])
def analyze():
    time.sleep(1.0)
    return jsonify({
        'incident_id': _state['incident_id'],
        'root_cause': 'Runaway batch job (PID 4821) consuming 94% CPU, creating Db2 lock chain on DSNDB07.SYSLGRNX, cascading to 312 lock waits.',
        'evidence': 'CPU 94% for 47s | TPS dropped from 118 to 8 | Db2 locks 312 (threshold 50)',
        'confidence_score': 0.91,
        'risk_level': 'medium',
        'blast_radius': 'Single runaway process + 5 Db2 transactions; host OS stable.',
        'recovery_steps': [
            { 'action': 'kill -15 4821', 'description': 'Gracefully terminate runaway batch job (SIGTERM).' },
            { 'action': 'SET CURRENT DEGREE = 1', 'description': 'Reduce Db2 query parallelism to release locks.' },
            { 'action': 'ROLLBACK', 'description': 'Roll back uncommitted transactions holding locks.' },
            { 'action': 'systemctl restart cics-workload-sim', 'description': 'Restart CICS workload simulator at baseline TPS.' }
        ],
        'rollback_conditions': 'If CPU does not drop below 40% within 30s, revert to checkpoint.',
        'allowlisted_action_ids': ['kill-15', 'db2-rollback', 'cics-restart'],
        'is_fallback': False
    })

@app.route('/api/validate', methods=['POST'])
def validate():
    time.sleep(1.2)
    return jsonify({
        'incident_id': _state['incident_id'],
        'passed': True,
        'result': 'pass',
        'details': 'Sandbox replay: CPU normalised to 23% within 18s. TPS recovered to 112. Db2 locks cleared.'
    })

@app.route('/api/approve', methods=['POST'])
def approve():
    return jsonify({
        'status': 'ok',
        'message': 'Human operator approval recorded in governance log.'
    })

@app.route('/api/recover', methods=['POST'])
def recover():
    time.sleep(1.0)
    _state['fault_active'] = False
    return jsonify({
        'status': 'ok',
        'success': True,
        'message': 'Recovery plan executed successfully. Telemetry returning to baseline.'
    })

@app.route('/api/playbooks')
def playbooks_api():
    return jsonify({
        'playbooks': [
            {
                'id': 'PB-001',
                'name': 'CPU Runaway Job',
                'version': 3,
                'trigger_signature': 'cpu_spike',
                'status': 'APPROVED',
                'validation_result': 'pass',
                'approved_by': 'operator',
                'match_score': 0.97,
                'steps_count': 4,
                'description': 'Terminates runaway process and resets Db2 contention.',
                'created_at': '2026-10-17T10:15:00Z'
            },
            {
                'id': 'PB-002',
                'name': 'Db2 Lock Contention',
                'version': 2,
                'trigger_signature': 'db2_lock_contention',
                'status': 'VALIDATED',
                'validation_result': 'pass',
                'approved_by': 'operator',
                'match_score': 0.82,
                'steps_count': 3,
                'description': 'Rolls back uncommitted transactions and reduces parallelism.',
                'created_at': '2026-10-17T11:42:00Z'
            },
            {
                'id': 'PB-003',
                'name': 'Memory Pressure',
                'version': 1,
                'trigger_signature': 'memory_pressure',
                'status': 'DRAFT',
                'validation_result': None,
                'approved_by': None,
                'match_score': 0.61,
                'steps_count': 2,
                'description': 'Clears page cache and reduces workload intensity.',
                'created_at': '2026-10-17T13:00:00Z'
            }
        ]
    })

@app.route('/api/replay', methods=['POST'])
def replay():
    time.sleep(0.8)
    _state['fault_active'] = False
    return jsonify({
        'status': 'ok',
        'success': True,
        'message': 'Playbook replay completed in 1.8s. All telemetry restored.'
    })

@app.route('/api/incidents')
def incidents_api():
    return jsonify({
        'incidents': [
            {
                'id': 'INC-20261017-001',
                'fault_type': 'cpu_spike',
                'status': 'resolved',
                'started_at': '2026-10-17T10:02:10Z',
                'resolved_at': '2026-10-17T10:04:52Z',
                'diagnosis': 'Runaway batch job PID 4821 consuming 94% CPU + Db2 lock chain.',
                'validation_result': 'pass',
                'approved_by': 'operator',
                'recovery_result': 'success',
                'playbook_id': 'PB-001',
                'recovery_passport_id': 'RP-001',
                'baseline': { 'cpu': 24.2, 'tps': 118, 'db2_locks': 6, 'ram': 1.9 },
                'during':   { 'cpu': 94.0, 'tps': 8,   'db2_locks': 312, 'ram': 2.8 },
                'after':    { 'cpu': 22.5, 'tps': 112, 'db2_locks': 4,  'ram': 1.9 }
            },
            {
                'id': 'INC-20261017-002',
                'fault_type': 'db2_lock_contention',
                'status': 'resolved',
                'started_at': '2026-10-17T11:30:00Z',
                'resolved_at': '2026-10-17T11:32:20Z',
                'diagnosis': 'Db2 lock chain on DSNDB07, 201 lock waits, TPS collapse.',
                'validation_result': 'pass',
                'approved_by': 'operator',
                'recovery_result': 'success',
                'playbook_id': 'PB-002',
                'recovery_passport_id': 'RP-002',
                'baseline': { 'cpu': 26.0, 'tps': 115, 'db2_locks': 8, 'ram': 2.0 },
                'during':   { 'cpu': 68.0, 'tps': 18,  'db2_locks': 201, 'ram': 2.4 },
                'after':    { 'cpu': 24.0, 'tps': 109, 'db2_locks': 5,  'ram': 2.0 }
            }
        ]
    })

@app.route('/api/audit')
def audit_api():
    return jsonify({
        'events': [
            { 'ts': '2026-10-17T10:02:10Z', 'actor': 'system', 'action': 'chaos.inject', 'target': 'cpu_spike · PID 4821', 'risk': 'medium', 'details': 'Fault started: cpu_spike, 30s, intensity=high' },
            { 'ts': '2026-10-17T10:02:57Z', 'actor': 'system', 'action': 'anomaly.detect', 'target': 'CPU 94% for 47s', 'risk': 'high', 'details': 'Threshold breached: CPU > 80%' },
            { 'ts': '2026-10-17T10:03:10Z', 'actor': 'agent', 'action': 'diagnosis.done', 'target': 'Runaway batch PID 4821 + Db2 chain', 'risk': 'medium', 'details': 'Confidence: 91%, fallback: false' },
            { 'ts': '2026-10-17T10:03:28Z', 'actor': 'agent', 'action': 'recovery.proposed', 'target': 'kill -15 + Db2 rollback', 'risk': 'medium', 'details': '4 steps proposed, allowlisted' },
            { 'ts': '2026-10-17T10:03:45Z', 'actor': 'agent', 'action': 'validation.pass', 'target': 'Sandbox replay OK', 'risk': 'low', 'details': 'CPU normalised to 23% in 18s' },
            { 'ts': '2026-10-17T10:03:59Z', 'actor': 'user', 'action': 'playbook.approve', 'target': 'INC-20261017-001', 'risk': 'medium', 'details': 'Human approval: operator' },
            { 'ts': '2026-10-17T10:04:11Z', 'actor': 'system', 'action': 'recovery.execute', 'target': 'PB-001 v1', 'risk': 'medium', 'details': 'kill -15 4821 OK; Db2 rollback OK' },
            { 'ts': '2026-10-17T10:04:52Z', 'actor': 'system', 'action': 'incident.resolved', 'target': 'INC-20261017-001', 'risk': 'low', 'details': 'CPU 22%, TPS 112, Db2 locks 4' },
            { 'ts': '2026-10-17T10:05:00Z', 'actor': 'system', 'action': 'playbook.saved', 'target': 'PB-001 v1', 'risk': 'low', 'details': 'Playbook saved to library' }
        ]
    })

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8080))
    host = os.environ.get('HOST', '0.0.0.0')
    print("=" * 60)
    print("  Z-PHOENIX Frontend Local Preview Server")
    print(f"  Available at: http://{host}:{port} (or http://localhost:{port})")
    print("  Press Ctrl+C to stop")
    print("=" * 60)
    try:
        app.run(host=host, port=port, debug=True)
    except OSError:
        port = 5000
        print(f"  Port 8080 busy, falling back to: http://{host}:{port}")
        app.run(host=host, port=port, debug=True)

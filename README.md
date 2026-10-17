# Z-Phoenix
 AI-driven resilience platform that combines controlled chaos engineering, telemetry analysis and agentic AI. Instead of waiting for a production failure it
# Z-PHOENIX Project Structure

```text
z-phoenix/
├── app.py                 # Flask app, SocketIO setup, API routes & page routers
├── config.py              # Ports, DB path, Granite/Groq API keys & fallback configs
├── agents.py              # Diagnosis, Recovery, and Validation agent logic with fallbacks
├── attack.py              # Chaos simulation subprocesses and safety limits
├── governance.py          # Approval gates, execution handler, playbook versioning
├── workload.py            # Simulated CICS/Db2 telemetry generator & socket streamer
├── database.py            # SQLite schema initialization and query helpers
├── requirements.txt       # flask, flask-socketio, requests, eventlet, etc.
├── static/
│   ├── css/
│   │   └── style.css      # Enterprise dark theme, status badges, layout grids
│   └── js/
│       ├── socket.js      # WebSocket connection and metric event bindings
│       ├── charts.js      # Chart.js initialization and live 1 Hz stream updating
│       └── app.js         # Navigation, button bindings, agent console rendering
└── templates/
    ├── base.html          # Shell layout (sidebar, topbar, status indicator)
    ├── command_center.html
    ├── chaos_lab.html
    ├── playbooks.html
    └── audit.html

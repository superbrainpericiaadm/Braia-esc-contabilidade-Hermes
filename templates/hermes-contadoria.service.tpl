[Unit]
Description=Hermes - Braia Comunidade ContadorIA (Telegram)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=hermes-contadoria
Group=hermes-contadoria
ExecStart=/opt/hermes-contadoria-runtime/venv/bin/python -m hermes_cli.main gateway run
WorkingDirectory=/home/hermes-contadoria/.hermes
Environment="HOME=/home/hermes-contadoria"
Environment="USER=hermes-contadoria"
Environment="LOGNAME=hermes-contadoria"
Environment="HERMES_HOME=/home/hermes-contadoria/.hermes"
Environment="VIRTUAL_ENV=/opt/hermes-contadoria-runtime/venv"
Environment="PATH=/opt/hermes-contadoria-runtime/venv/bin:/opt/hermes-contadoria-runtime/node_modules/.bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"
Restart=always
RestartSec=5
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=90
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=false
ReadWritePaths=/home/hermes-contadoria/.hermes /home/hermes-contadoria/workspace
MemoryHigh=1500M
MemoryMax=2G
CPUQuota=100%
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target


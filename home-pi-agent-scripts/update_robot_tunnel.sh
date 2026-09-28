#!/bin/bash
sshpass -p bastet ssh -p 49022 -o StrictHostKeyChecking=no bastet@localhost << 'SUBEOF'
echo bastet | sudo -S sed -i 's|-R 0.0.0.0:49022:localhost:22|-R 0.0.0.0:49022:localhost:22 -R 127.0.0.1:48880:localhost:8888|g' /etc/systemd/system/bastet-tunnel.service
echo bastet | sudo -S systemctl daemon-reload
echo bastet | sudo -S systemctl restart bastet-tunnel.service
SUBEOF

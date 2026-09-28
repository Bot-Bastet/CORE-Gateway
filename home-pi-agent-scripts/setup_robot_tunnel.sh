#!/bin/bash
set -e

echo '=== Installation du Reverse SSH Tunnel Bastet (cote Robot) ==='

# 1. Installer autossh
echo '[1/4] Installation de autossh...'
sudo apt update -qq && sudo apt install -y autossh

# 2. Recuperer la cle privee depuis la Gateway
echo '[2/4] Recuperation de la cle privee depuis la Gateway...'
ROBOT_USER=$(whoami)
mkdir -p ~/.ssh
scp tealo@79.94.238.213:/home/tealo/.ssh/bastet_robot_key ~/.ssh/gateway_tunnel_key
chmod 600 ~/.ssh/gateway_tunnel_key
echo "[OK] Cle privee copiee dans ~/.ssh/gateway_tunnel_key"

# 3. Tester la connexion
echo '[3/4] Test de connexion vers la Gateway...'
ssh -i ~/.ssh/gateway_tunnel_key -o StrictHostKeyChecking=no -o ConnectTimeout=10 tealo@79.94.238.213 "echo '[OK] Connexion SSH vers la Gateway reussie!'"

# 4. Installer le service systemd
echo '[4/4] Installation du service systemd...'

# Adapter le fichier service avec le bon utilisateur
sudo tee /etc/systemd/system/bastet-tunnel.service > /dev/null << UNIT
[Unit]
Description=Bastet Reverse SSH Tunnel to Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$ROBOT_USER
ExecStart=/usr/bin/autossh -M 0 -N -o "ServerAliveInterval=30" -o "ServerAliveCountMax=3" -o "ExitOnForwardFailure=yes" -o "StrictHostKeyChecking=no" -i /home/$ROBOT_USER/.ssh/gateway_tunnel_key -R 0.0.0.0:49022:localhost:22 tealo@79.94.238.213
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable bastet-tunnel.service
sudo systemctl start bastet-tunnel.service

echo ''
echo '=== Installation terminee ==='
echo "Le tunnel est actif. Pour te connecter au robot depuis n'importe ou :"
echo '  ssh '$ROBOT_USER'@79.94.238.213 -p 49022'
echo ''
echo 'Statut du tunnel :'
sudo systemctl status bastet-tunnel.service --no-pager

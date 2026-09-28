#!/bin/bash
set -e

echo '=== Configuration du Reverse SSH Tunnel pour Bastet ==='

SSHD_CONF=/etc/ssh/sshd_config

# Backup
sudo cp "$SSHD_CONF" "${SSHD_CONF}.bak"

# Supprimer les lignes existantes (commentees ou pas)
sudo sed -i '/^#\?GatewayPorts/d' "$SSHD_CONF"
sudo sed -i '/^#\?AllowTcpForwarding/d' "$SSHD_CONF"

# Ajouter les bonnes valeurs
echo '' | sudo tee -a "$SSHD_CONF" > /dev/null
echo '# Bastet Reverse Tunnel' | sudo tee -a "$SSHD_CONF" > /dev/null
echo 'GatewayPorts clientspecified' | sudo tee -a "$SSHD_CONF" > /dev/null
echo 'AllowTcpForwarding yes' | sudo tee -a "$SSHD_CONF" > /dev/null

echo '[OK] sshd_config mis a jour.'

# Redemarrer sshd
sudo systemctl restart sshd
echo '[OK] sshd redemarre.'

# Verifier
echo '=== Verification ==='
grep -E 'GatewayPorts|AllowTcpForwarding' "$SSHD_CONF" | grep -v '^#'
echo '=== Termine ==='

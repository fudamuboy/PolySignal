#!/bin/bash
# ==============================================================================
# PRODUCTION DEPLOYMENT & PROVISIONING SCRIPT (Ubuntu 22.04 LTS)
# ==============================================================================

set -e

echo "=== [1/6] Updating Ubuntu System Packages ==="
sudo apt-get update -y
sudo apt-get upgrade -y

echo "=== [2/6] Installing PostgreSQL & Redis Server ==="
sudo apt-get install -y postgresql postgresql-contrib redis-server python3-pip python3-venv supervisor git build-essential libpq-dev

echo "=== [3/6] Configuring Redis Service ==="
sudo systemctl enable redis-server.service
sudo systemctl start redis-server.service
echo "Redis status:"
sudo redis-cli ping

echo "=== [4/6] Configuring PostgreSQL Database ==="
sudo systemctl enable postgresql
sudo systemctl start postgresql

# Create postgres database and credentials if they do not exist
sudo -u postgres psql -c "CREATE USER postgres WITH PASSWORD 'postgres';" || echo "User postgres already exists"
sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD 'postgres';"
sudo -u postgres psql -c "CREATE DATABASE polysignal;" || echo "Database polysignal already exists"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE polysignal TO postgres;"

echo "=== [5/6] Setting Up Project Virtual Environment ==="
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "Created virtual environment 'venv'."
fi

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "=== [6/6] Provisioning Complete ==="
echo ""
echo "Next steps:"
echo "1. Configure your keys in .env.production"
echo "2. Copy deploy/supervisor.conf to /etc/supervisor/conf.d/polysignal.conf"
echo "3. Run 'sudo supervisorctl reload' to start the trading engine processes."
echo "4. Monitor logs using 'tail -f storage/logs/bot.log'"

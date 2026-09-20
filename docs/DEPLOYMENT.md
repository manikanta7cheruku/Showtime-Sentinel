# Deployment Guide — Oracle Cloud Always Free

This guide walks through deploying Showtime Sentinel to a free Oracle Cloud VM for 24/7 operation.

---

## Prerequisites

- Oracle Cloud account (free signup, no credit card charged for Always Free resources)
- SSH client (Windows: built into PowerShell 7+, or use PuTTY)
- Your Telegram bot token and chat ID

---

## Phase 1: Create Oracle Cloud VM

1. Sign up at https://cloud.oracle.com (choose "Always Free" region)
2. Navigate to **Compute → Instances → Create Instance**
3. Configure:
   - **Name**: `showtime-sentinel`
   - **Image**: Ubuntu 22.04 (Canonical, aarch64)
   - **Shape**: `VM.Standard.A1.Flex` — 1 OCPU, 6 GB RAM
   - **Networking**: Assign public IPv4
   - **SSH keys**: Upload your public key (`~/.ssh/id_rsa.pub`) or let Oracle generate one
4. Click **Create**. Wait 2–3 minutes for provisioning.
5. Note the **Public IP address** displayed after creation.

## Phase 2: Configure Firewall

1. In Oracle Cloud console, go to **Virtual Cloud Networks → your VCN → Security Lists → Default**
2. Add ingress rule: **Port 22 (SSH)** — should already exist
3. No other ports needed (bot uses outbound polling only)

## Phase 3: Initial Server Setup

SSH into the server from Windows PowerShell:

```powershell
ssh -i ~\.ssh\oracle_key ubuntu@YOUR_PUBLIC_IP
```

Once connected, run these commands on the server:

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and dependencies
sudo apt install -y python3.11 python3.11-venv python3-pip git xvfb \
    chromium-browser fonts-liberation libnss3 libatk-bridge2.0-0

# Clone repository
cd ~
git clone https://github.com/manikanta7cheruku/Showtime-Sentinel.git showtime-sentinel
cd showtime-sentinel

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Install Playwright browsers (ARM64 build)
playwright install chromium
playwright install-deps

# Create .env from template
cp .env.example .env
nano .env
```

In `.env`, set:
```
TELEGRAM_BOT_TOKEN=your_new_revoked_token_here
TELEGRAM_CHAT_ID=6133756106
ALLOWED_TELEGRAM_USER_IDS=6133756106
BOOKMYSHOW_HEADLESS=true
RESPECT_ROBOTS_TXT=false
```

Save (Ctrl+O, Enter, Ctrl+X).

Initialize the database:
```bash
python scripts/init_db.py
```

Test manually:
```bash
xvfb-run --server-args="-screen 0 1280x1024x24" python -m app.main run
```

If you see "Bot started polling" — success. Press Ctrl+C to stop.

## Phase 4: Install systemd Service

Copy the service file:
```bash
sudo cp deploy/showtime-sentinel.service /etc/systemd/system/
```

Reload systemd to pick up the new file:
```bash
sudo systemctl daemon-reload
```

Enable auto-start on boot:
```bash
sudo systemctl enable showtime-sentinel
```

Start the service now:
```bash
sudo systemctl start showtime-sentinel
```

Verify it's running:
```bash
sudo systemctl status showtime-sentinel
```

You should see `active (running)` in green.

## Phase 5: Monitoring and Operations

### Check status
```bash
sudo systemctl status showtime-sentinel
```

### View live logs
```bash
sudo journalctl -u showtime-sentinel -f
```

### View last 100 log lines
```bash
sudo journalctl -u showtime-sentinel -n 100 --no-pager
```

### Restart after code update
```bash
cd ~/showtime-sentinel
git pull
sudo systemctl restart showtime-sentinel
```

### Stop the service
```bash
sudo systemctl stop showtime-sentinel
```

### Disable auto-start
```bash
sudo systemctl disable showtime-sentinel
```

### Check memory/CPU usage
```bash
htop
# or
sudo systemctl status showtime-sentinel | grep Memory
```

## Phase 6: Ongoing Maintenance

### Weekly
- Check logs for errors: `sudo journalctl -u showtime-sentinel --since "1 week ago" | grep ERROR`
- Verify disk space: `df -h`

### Monthly
- Log into Oracle Cloud console (prevents reclamation of idle resources)
- Update system packages: `sudo apt update && sudo apt upgrade -y`
- Restart service after updates: `sudo systemctl restart showtime-sentinel`

### On code changes
```bash
cd ~/showtime-sentinel
git pull
source venv/bin/activate
pip install -r requirements.txt  # if requirements changed
sudo systemctl restart showtime-sentinel
sudo journalctl -u showtime-sentinel -f  # watch it start
```

---

## Troubleshooting

### Service won't start
```bash
sudo journalctl -u showtime-sentinel -n 50 --no-pager
```
Look for Python tracebacks or missing environment variables.

### Chromium fails to launch
```bash
sudo apt install -y libnss3 libatk-bridge2.0-0 libgtk-3-0
playwright install-deps
```

### Out of memory
```bash
free -h
# If needed, add swap:
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### Cannot SSH
Check Oracle Cloud console → your instance → verify public IP hasn't changed.

---

**End of Deployment Guide**
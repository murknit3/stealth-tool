# Stealth — Kali helper tool

**Stealth** is a compact, menu-driven Python script for Kali Linux that helps automate a few common operations:

- Put a wireless adapter into **monitor mode** (via `airmon-ng` or `iw`)
- **Spoof MAC** addresses (via `macchanger` or `ip link`)
- **Clear/truncate common logs** and vacuum the systemd journal
- **Interactive restore** flows, including an option to keep monitor mode and restore it later

> ⚠️ **Important:** This tool performs privileged and potentially destructive operations. **Only run on machines and networks you own or have explicit written permission to test.**

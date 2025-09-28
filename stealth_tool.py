#!/usr/bin/env python3
"""
stealth_tool.py

Ready-to-use, menu-driven Kali helper tool (requires root).
Options:
 1) stealth_mode: stop NetworkManager and put chosen wireless adapter into monitor mode (uses airmon-ng)
 2) mac_spoof: spoof MAC address for the adapter currently in monitor mode
 3) clear_logs: truncates common log files and vacuums systemd journal (with confirmation)
 4) restore_changes: interactive restore (asks whether to stop monitor mode). If you choose to KEEP monitor mode, state is preserved so you can later run option 5.
 5) restore_monitor_only: stop monitor mode only (if you previously chose to keep monitor mode) and fully clear saved state
 0) exit

State is saved in /var/lib/stealth_tool/state.json so restore options can revert actions.

Safety: This script performs privileged operations (stopping NetworkManager, changing interfaces, truncating logs).
Only run on systems and networks you own or where you have explicit permission.

Dependencies: aircrack-ng (airmon-ng), macchanger (optional), iproute2, iw, systemd (journalctl)

Save as /usr/local/bin/stealth, chmod +x /usr/local/bin/stealth
"""

import os
import sys
import subprocess
import shutil
import random
import re
import json
from pathlib import Path

# ----- configuration -----
LOG_FILES = [
    "/var/log/auth.log",
    "/var/log/syslog",
    "/var/log/kern.log",
    "/var/log/messages",
    "/var/log/wtmp",
    "/var/log/lastlog",
]

STATE_DIR = Path('/var/lib/stealth_tool')
STATE_FILE = STATE_DIR / 'state.json'

# ----- helpers -----

def ensure_state_dir():
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[!] Could not create state dir {STATE_DIR}: {e}")


def load_state():
    ensure_state_dir()
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: dict):
    ensure_state_dir()
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        print(f"[!] Could not write state file: {e}")


def clear_state():
    try:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
    except Exception as e:
        print(f"[!] Could not remove state file: {e}")


def check_root():
    if os.geteuid() != 0:
        print("[!] This tool must be run as root. Use sudo or run as root.")
        sys.exit(1)


def run(cmd, capture=False):
    try:
        if capture:
            return subprocess.run(cmd, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        else:
            return subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[!] Command failed: {e}")
        return None

# ----- network/wifi helpers -----

def detect_wireless_interfaces():
    """Return a list of wireless interfaces detected via `iw dev` or ip."""
    out = run('iw dev', capture=True)
    if not out:
        return []
    text = out.stdout
    ifaces = re.findall(r"Interface\s+(\S+)", text)
    if not ifaces:
        out2 = run('ip -brief link', capture=True)
        if out2:
            for line in out2.stdout.splitlines():
                name = line.split()[0]
                if name.startswith(('wlan','wlp','wl')):
                    ifaces.append(name)
    return list(dict.fromkeys(ifaces))


def choose_interface(prompt="Select interface"):
    ifaces = detect_wireless_interfaces()
    if not ifaces:
        print("[!] No wireless interfaces detected. Plug in your Alfa adapter and try again.")
        return None
    print(f"Detected wireless interfaces: {', '.join(ifaces)}")
    for i, iface in enumerate(ifaces, 1):
        print(f"{i}. {iface}")
    choice = input(f"{prompt} (number): ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(ifaces):
            return ifaces[idx]
    except ValueError:
        pass
    print("[!] Invalid selection.")
    return None


def generate_random_mac():
    mac = [0x02, random.randint(0x00, 0xff), random.randint(0x00, 0xff),
           random.randint(0x00, 0xff), random.randint(0x00, 0xff), random.randint(0x00, 0xff)]
    return ':'.join(f"{b:02x}" for b in mac)


def get_current_mac(iface):
    out = run(f'ip link show {iface}', capture=True)
    if not out:
        return None
    m = re.search(r"link/ether\s+([0-9a-f:]{17})", out.stdout)
    return m.group(1) if m else None

# ----- main features -----

def stealth_mode():
    state = load_state()
    print("\n=== Stealth mode ===")
    print("This will stop NetworkManager and put your chosen adapter into monitor mode (airmon-ng preferred).")
    confirm = input("Type YES to continue: ").strip()
    if confirm != "YES":
        print("Aborted by user.")
        return

    iface = choose_interface("Choose adapter to put into monitor mode")
    if not iface:
        return

    before_ifaces = detect_wireless_interfaces()

    print('[+] Stopping NetworkManager...')
    res = run('systemctl stop NetworkManager')
    if res is None:
        print('[!] Failed to stop NetworkManager (or command returned error). Continuing but state may be inconsistent.')
    else:
        state['networkmanager_stopped'] = True
        save_state(state)

    if shutil.which('airmon-ng'):
        print('[+] Running: airmon-ng check kill')
        run('airmon-ng check kill')
    else:
        print('[i] airmon-ng not installed; attempting manual monitor mode.')

    print(f'[+] Bringing down {iface}...')
    run(f'ip link set {iface} down')

    if shutil.which('airmon-ng'):
        print(f'[+] Starting monitor mode via airmon-ng on {iface}...')
        run(f'airmon-ng start {iface}')
        after_ifaces = detect_wireless_interfaces()
        mon_iface = None
        for a in after_ifaces:
            if a not in before_ifaces:
                mon_iface = a
                break
        if not mon_iface:
            mon_iface = iface + 'mon'
        print(f'[i] Monitor-mode interface likely: {mon_iface}')
        state['monitor_interface'] = mon_iface
        state['original_interface'] = iface
        save_state(state)
    else:
        print('[+] Attempting manual monitor mode using iw...')
        run(f'iw dev {iface} set type monitor')
        run(f'ip link set {iface} up')
        print(f'[i] {iface} should now be in monitor mode (verify with iw dev).')
        state['monitor_interface'] = iface
        state['original_interface'] = iface
        save_state(state)


def mac_spoof():
    state = load_state()
    print("\n=== MAC Spoof ===")
    iface = input("Enter interface to spoof (or press Enter to choose): ").strip()
    if not iface:
        iface = choose_interface()
        if not iface:
            return

    if 'original_macs' not in state:
        state['original_macs'] = {}

    if iface not in state['original_macs']:
        orig = get_current_mac(iface)
        if orig:
            state['original_macs'][iface] = orig
            save_state(state)
            print(f"[i] Saved original MAC for {iface}: {orig}")
        else:
            print('[i] Could not determine original MAC for this interface.')

    new_mac = generate_random_mac()
    print(f'[+] Setting new MAC {new_mac} on {iface}...')

    if shutil.which('macchanger'):
        run(f'ip link set {iface} down')
        run(f'macchanger -m {new_mac} {iface}')
        run(f'ip link set {iface} up')
    else:
        run(f'ip link set {iface} down')
        run(f'ip link set dev {iface} address {new_mac}')
        run(f'ip link set {iface} up')

    state.setdefault('spoofed_macs', {})[iface] = new_mac
    save_state(state)
    print(f'[+] MAC changed. Verify with: ip link show {iface}')


def clear_logs():
    print("\n=== Clear Logs ===")
    print("This will TRUNCATE common log files and vacuum systemd journal. Destructive: proceed only on your systems.")
    confirm = input("Type CLEAR_LOGS to proceed: ").strip()
    if confirm != "CLEAR_LOGS":
        print("Aborted by user.")
        return

    for lf in LOG_FILES:
        if os.path.exists(lf):
            try:
                with open(lf, 'r+') as f:
                    f.truncate(0)
                print(f"[+] Truncated {lf}")
            except Exception as e:
                print(f"[!] Failed to truncate {lf}: {e}")
        else:
            print(f"[i] Log file not present: {lf}")

    if shutil.which('journalctl'):
        print('[+] Rotating and vacuuming systemd journal...')
        run('journalctl --rotate')
        run('journalctl --vacuum-time=1s')
        print('[+] Journal vacuumed (older entries removed).')
    else:
        print('[!] journalctl not found; skipping journal vacuum.')

    print('[i] Note: some services recreate logs; some logs may reappear over time.')


def restore_changes():
    """Interactive restore — asks whether to stop monitor mode. If user keeps monitor mode, state is preserved for later restoration."""
    print("\n=== Restore Changes (interactive) ===")
    state = load_state()
    if not state:
        print('[i] No saved state; nothing to restore.')
        return

    # Restore MACs first
    spoofed = state.get('spoofed_macs', {})
    origs = state.get('original_macs', {})

    for iface, _ in spoofed.items():
        original = origs.get(iface)
        print(f"[+] Restoring MAC for {iface}: {original if original else 'using macchanger -p (if available)'}")
        if original:
            if shutil.which('macchanger'):
                run(f'ip link set {iface} down')
                run(f'macchanger -m {original} {iface}')
                run(f'ip link set {iface} up')
            else:
                run(f'ip link set {iface} down')
                run(f'ip link set dev {iface} address {original}')
                run(f'ip link set {iface} up')
        else:
            if shutil.which('macchanger'):
                run(f'ip link set {iface} down')
                run(f'macchanger -p {iface}')
                run(f'ip link set {iface} up')
            else:
                print(f"[!] No original MAC known for {iface} and macchanger missing. Manual fix required.")

    # Ask about monitor mode
    mon = state.get('monitor_interface')
    orig_iface = state.get('original_interface')
    clear_after = True
    if mon:
        ans = input(f"Monitor interface detected ({mon}). Stop monitor mode and restore managed mode now? [y/N]: ").strip().lower()
        if ans in ('y', 'yes'):
            print(f"[+] Stopping monitor mode for {mon}...")
            if shutil.which('airmon-ng'):
                run(f'airmon-ng stop {mon}')
            else:
                if orig_iface:
                    run(f'ip link set dev {orig_iface} down')
                    run(f'iw dev {orig_iface} set type managed')
                    run(f'ip link set dev {orig_iface} up')
                else:
                    print('[!] Could not determine original iface to restore managed type.')
            clear_after = True
        else:
            # preserve state so user can restore monitor later with option 5
            print('[i] Leaving interface in monitor mode. You can run option 5 to stop monitor mode later.')
            state['monitor_left'] = True
            save_state(state)
            clear_after = False

    # Restart NetworkManager if it was stopped earlier
    if state.get('networkmanager_stopped'):
        print('[+] Starting NetworkManager...')
        run('systemctl start NetworkManager')

    if clear_after:
        clear_state()
        print('[+] Restore finished; saved state cleared.')
    else:
        print('[+] Restore finished; saved state preserved for later monitor-only restore.')


def restore_monitor_only():
    """Stop monitor mode only (if you previously chose to keep it). Then clear state."""
    print("\n=== Restore Monitor Only ===")
    state = load_state()
    if not state:
        print('[i] No saved state found; nothing to do.')
        return

    mon = state.get('monitor_interface')
    orig_iface = state.get('original_interface')
    if not mon:
        print('[i] No monitor interface recorded in state; clearing state and exiting.')
        clear_state()
        return

    print(f"[+] Attempting to stop monitor mode interface: {mon}")
    if shutil.which('airmon-ng'):
        run(f'airmon-ng stop {mon}')
    else:
        if orig_iface:
            run(f'ip link set dev {orig_iface} down')
            run(f'iw dev {orig_iface} set type managed')
            run(f'ip link set dev {orig_iface} up')
        else:
            print('[!] Could not determine original iface to restore managed type.')

    # Start NetworkManager if tool had stopped it earlier
    if state.get('networkmanager_stopped'):
        print('[+] Starting NetworkManager...')
        run('systemctl start NetworkManager')

    clear_state()
    print('[+] Monitor stopped (if possible) and state cleared.')


# ----- CLI menu -----

def menu():
    check_root()
    while True:
        print('\n=== Stealth Tool ===')
        print('1. Stealth Mode (stop NetworkManager & enable monitor mode)')
        print('2. Spoof MAC for adapter (works with monitor-mode interfaces)')
        print('3. Clear logs (truncate common logs & vacuum journal)')
        print('4. Restore changes (interactive: ask to stop monitor mode)')
        print('5. Restore monitor only (stop monitor left active earlier)')
        print('0. Exit')
        choice = input('Select an option: ').strip()
        if choice == '1':
            stealth_mode()
        elif choice == '2':
            mac_spoof()
        elif choice == '3':
            clear_logs()
        elif choice == '4':
            restore_changes()
        elif choice == '5':
            restore_monitor_only()
        elif choice == '0':
            print('Exiting...')
            sys.exit(0)
        else:
            print('Invalid choice. Try again.')


if __name__ == '__main__':
    try:
        menu()
    except KeyboardInterrupt:
        print('\nExiting (Ctrl+C)')
        sys.exit(0)

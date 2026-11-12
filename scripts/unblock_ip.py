#!/usr/bin/env python3
"""
unblock_ip.py
=============

Recovery tool for the brute-force blocklist. If you (or someone) got
permanently blocked after too many wrong logins, use this to clear it.

The web server stores blocked IPs in a JSON file (default:
~/.robot_web_server_blocklist.json). This script lists or removes entries.

Usage:
    # Show currently blocked IPs
    python3 scripts/unblock_ip.py --list

    # Remove one IP
    python3 scripts/unblock_ip.py 203.0.113.7

    # Remove ALL blocks (wipe the list)
    python3 scripts/unblock_ip.py --all

    # Point at a custom blocklist file
    python3 scripts/unblock_ip.py --file /path/to/blocklist.json 203.0.113.7

After editing, restart the web server for the change to take effect.
"""

import argparse
import json
import sys
from pathlib import Path

DEFAULT_FILE = Path.home() / '.robot_web_server_blocklist.json'


def load(path: Path) -> set:
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text()).get('blocked', []))
    except Exception as exc:  # noqa: BLE001
        print(f'Error reading {path}: {exc}', file=sys.stderr)
        sys.exit(1)


def save(path: Path, blocked: set):
    path.write_text(json.dumps({'blocked': sorted(blocked)}, indent=2))


def main():
    ap = argparse.ArgumentParser(description='Manage the login blocklist.')
    ap.add_argument('ip', nargs='?', help='IP address to unblock')
    ap.add_argument('--file', default=str(DEFAULT_FILE),
                    help='Path to the blocklist JSON file')
    ap.add_argument('--list', action='store_true', help='List blocked IPs')
    ap.add_argument('--all', action='store_true', help='Remove all blocks')
    args = ap.parse_args()

    path = Path(args.file)
    blocked = load(path)

    if args.list:
        if blocked:
            print('Blocked IPs:')
            for ip in sorted(blocked):
                print(f'  {ip}')
        else:
            print('No IPs are currently blocked.')
        return

    if args.all:
        save(path, set())
        print(f'Cleared all {len(blocked)} blocked IP(s) from {path}.')
        print('Restart the web server for the change to take effect.')
        return

    if not args.ip:
        ap.error('Provide an IP to unblock, or use --list / --all.')

    if args.ip in blocked:
        blocked.discard(args.ip)
        save(path, blocked)
        print(f'Unblocked {args.ip}.')
        print('Restart the web server for the change to take effect.')
    else:
        print(f'{args.ip} is not in the blocklist.')


if __name__ == '__main__':
    main()

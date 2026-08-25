#!/usr/bin/env python3
"""Restore the last Stage62 NetworkManager backup from local console/SSH."""
import argparse, json, subprocess
p=argparse.ArgumentParser(); p.add_argument('--interface',default='eth0'); a=p.parse_args()
cp=subprocess.run(['sudo','-n','/usr/local/libexec/ptz-network-helper'], input=json.dumps({'operation':'restore','interface':a.interface}), text=True)
raise SystemExit(cp.returncode)

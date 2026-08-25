#!/usr/bin/env python3
"""Root-only, narrowly scoped NetworkManager helper. Reads one JSON request on stdin."""
from __future__ import annotations
import ipaddress, json, os, subprocess, sys
from pathlib import Path

BACKUP=Path('/var/lib/ptz-joystick-controller/network-backup.json')
ALLOWED_INTERFACES={'eth0'}

def run(args, *, check=True):
    cp=subprocess.run(args, text=True, capture_output=True, timeout=20, check=False)
    if check and cp.returncode: raise RuntimeError((cp.stderr or cp.stdout).strip())
    return cp.stdout.strip()

def connection_for(interface):
    out=run(['nmcli','-t','-e','yes','-f','GENERAL.CONNECTION','device','show',interface])
    value=out.split(':',1)[1] if ':' in out else ''
    return value.replace('\\:',':').replace('\\\\','\\')

def snapshot(interface, connection):
    fields=('ipv4.method','ipv4.addresses','ipv4.gateway','ipv4.dns','ipv4.ignore-auto-dns')
    return {'interface':interface,'connection':connection, **{f:run(['nmcli','-g',f,'connection','show',connection]) for f in fields}}

def save_backup(data):
    BACKUP.parent.mkdir(parents=True, exist_ok=True); tmp=BACKUP.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,indent=2)+'\n'); os.chmod(tmp,0o600); os.replace(tmp,BACKUP)

def validate(req):
    if set(req)-{'operation','interface','address','prefix','gateway','dns'}: raise ValueError('Unsupported helper argument')
    op=req.get('operation'); interface=req.get('interface')
    if op not in {'dhcp','static','restore'}: raise ValueError('Unsupported network operation')
    if interface not in ALLOWED_INTERFACES: raise ValueError('Unsupported network interface')
    if op=='static':
        ip=ipaddress.IPv4Address(req['address']); prefix=int(req['prefix']); net=ipaddress.IPv4Network(f'{ip}/{prefix}',strict=False)
        gw=ipaddress.IPv4Address(req['gateway']); dns=[ipaddress.IPv4Address(x) for x in req.get('dns',[])]
        if not 0<=prefix<=32 or ip in {net.network_address,net.broadcast_address} or gw not in net or not dns: raise ValueError('Invalid static IPv4 configuration')
    return op,interface

def main():
    req=json.load(sys.stdin); op,interface=validate(req); connection=connection_for(interface)
    if not connection or connection=='--': raise RuntimeError('No active NetworkManager connection for interface')
    if op!='restore': save_backup(snapshot(interface,connection))
    if op=='dhcp':
        run(['nmcli','connection','modify',connection,'ipv4.method','auto','ipv4.addresses','','ipv4.gateway','','ipv4.dns','','ipv4.ignore-auto-dns','no'])
    elif op=='static':
        run(['nmcli','connection','modify',connection,'ipv4.method','manual','ipv4.addresses',f"{req['address']}/{req['prefix']}",'ipv4.gateway',req['gateway'],'ipv4.dns',','.join(req['dns']),'ipv4.ignore-auto-dns','yes'])
    else:
        data=json.loads(BACKUP.read_text());
        if data.get('interface')!=interface: raise RuntimeError('Backup interface mismatch')
        connection=data['connection']
        run(['nmcli','connection','modify',connection,'ipv4.method',data['ipv4.method'],'ipv4.addresses',data['ipv4.addresses'],'ipv4.gateway',data['ipv4.gateway'],'ipv4.dns',data['ipv4.dns'],'ipv4.ignore-auto-dns',data['ipv4.ignore-auto-dns'] or 'no'])
    run(['nmcli','connection','up',connection])
if __name__=='__main__':
    try: main()
    except Exception as exc: print(f'Network helper error: {exc}',file=sys.stderr); sys.exit(1)

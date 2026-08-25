from __future__ import annotations
import json
from pathlib import Path
import subprocess
import pytest

from ptz_joystick_controller.network_manager import NetworkManagerBackend, NetworkManagerError, NetworkState, StaticIPv4, normalize_prefix, validate_static
from ptz_joystick_controller.constants import DEFAULT_WEB_PORT
from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.version import __stage__, __version__

class Runner:
    def __init__(self, outputs): self.outputs=outputs; self.calls=[]
    def __call__(self,args,**kwargs):
        self.calls.append((args,kwargs))
        key=tuple(args)
        rc,out,err=self.outputs.get(key,(1,'','unexpected'))
        return subprocess.CompletedProcess(args,rc,out,err)

def test_static_validation_and_masks():
    c=validate_static('192.168.1.50','255.255.255.0','192.168.1.31','192.168.1.31','1.1.1.1')
    assert c == StaticIPv4('192.168.1.50',24,'192.168.1.31',('192.168.1.31','1.1.1.1'))
    assert normalize_prefix('/24') == 24
    for args in [
        ('bad','24','192.168.1.1','1.1.1.1',''),
        ('192.168.1.50','33','192.168.1.1','1.1.1.1',''),
        ('192.168.1.50','255.0.255.0','192.168.1.1','1.1.1.1',''),
        ('192.168.1.50','24','bad','1.1.1.1',''),
        ('192.168.1.50','24','192.168.1.1','bad',''),
        ('192.168.1.50','24','192.168.1.1','1.1.1.1','bad'),
        ('192.168.1.0','24','192.168.1.1','1.1.1.1',''),
        ('192.168.1.255','24','192.168.1.1','1.1.1.1',''),
        ('192.168.1.50','24','192.168.2.1','1.1.1.1',''),
    ]:
        with pytest.raises(ValueError): validate_static(*args)

def test_read_dhcp_state_and_connection_with_spaces():
    outputs={
      ('nmcli','-t','-f','RUNNING','general'):(0,'running\n',''),
      ('nmcli','-t','-e','yes','-f','GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS','device','show','eth0'):(0,'GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:Wired connection 1\nIP4.ADDRESS[1]:192.168.1.60/24\nIP4.GATEWAY:192.168.1.31\nIP4.DNS[1]:192.168.1.31\n',''),
      ('nmcli','-g','ipv4.method','connection','show','Wired connection 1'):(0,'auto\n',''),
    }
    b=NetworkManagerBackend(runner=Runner(outputs)); s=b.read_state()
    assert (s.connected,s.connection,s.mode,s.address,s.prefix,s.gateway,s.dns)==(True,'Wired connection 1','dhcp','192.168.1.60',24,'192.168.1.31',('192.168.1.31',))

def test_static_state_and_unavailable():
    outputs={
      ('nmcli','-t','-f','RUNNING','general'):(0,'running\n',''),
      ('nmcli','-t','-e','yes','-f','GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS','device','show','eth0'):(0,'GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:LAN Profile\nIP4.ADDRESS[1]:10.0.0.5/24\nIP4.GATEWAY:10.0.0.1\n',''),
      ('nmcli','-g','ipv4.method','connection','show','LAN Profile'):(0,'manual\n',''),
    }
    assert NetworkManagerBackend(runner=Runner(outputs)).read_state().mode=='static'
    with pytest.raises(NetworkManagerError): NetworkManagerBackend(runner=Runner({})).read_state()

def test_apply_uses_fixed_helper_no_shell_and_normalized_payload():
    r=Runner({('sudo','-n','/usr/local/libexec/ptz-network-helper'):(0,'','')}); b=NetworkManagerBackend(runner=r)
    b.apply_dhcp(); b.apply_static(StaticIPv4('192.168.1.50',24,'192.168.1.1',('1.1.1.1',)))
    for args,kw in r.calls:
        assert args == ['sudo','-n','/usr/local/libexec/ptz-network-helper']
        assert 'shell' not in kw or kw['shell'] is False
        req=json.loads(kw['input']); assert set(req) <= {'operation','interface','address','prefix','gateway','dns'}
    assert json.loads(r.calls[0][1]['input'])['operation']=='dhcp'

def test_web_port_and_metadata_stage62():
    assert DEFAULT_WEB_PORT==80
    assert parse_config({'switcher':{'type':'vmix'}}).webui.listen_port==80
    assert parse_config({'switcher':{'type':'vmix'},'webui':{'listen_port':8080}}).webui.listen_port==8080
    assert __stage__=='Stage62'; assert __version__=='0.11.0'

def test_privilege_artifacts_are_narrow_and_non_root():
    root=Path(__file__).parents[1]
    service=(root/'deploy/ptz-joystick-controller.service').read_text()
    sudoers=(root/'deploy/ptz-network-helper.sudoers').read_text()
    helper=(root/'deploy/ptz-network-helper.py').read_text()
    assert 'CAP_NET_BIND_SERVICE' in service and 'User=root' not in service
    assert 'NOPASSWD: ALL' not in sudoers and '/usr/local/libexec/ptz-network-helper' in sudoers
    assert 'shell=True' not in helper and "{'dhcp','static','restore'}" in helper

def test_helper_dhcp_clears_stale_manual_values():
    text=Path('deploy/ptz-network-helper.py').read_text()
    assert "'ipv4.method','auto'" in text
    assert "'ipv4.addresses',''" in text and "'ipv4.gateway',''" in text and "'ipv4.dns',''" in text

def test_app_web_port_is_legacy_not_runtime_authority():
    root=Path(__file__).parents[1]
    consumers=[]
    for p in (root/'src').rglob('*.py'):
        if 'app.web_port' in p.read_text(): consumers.append(str(p))
    assert consumers == []
    assert 'self.config.webui.listen_port' in (root/'src/ptz_joystick_controller/runtime/application.py').read_text()

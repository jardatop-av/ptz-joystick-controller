from __future__ import annotations
import re
from pathlib import Path
from fastapi.testclient import TestClient
from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.config import load_config
from ptz_joystick_controller.network_manager import NetworkState, StaticIPv4
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app

class FakeNetwork:
    interface='eth0'
    def __init__(self): self.calls=[]
    def read_state(self): return NetworkState('eth0',True,'Wired connection 1','dhcp','192.168.1.60',24,'192.168.1.31',('192.168.1.31',))
    def apply_dhcp(self): self.calls.append(('dhcp',None))
    def apply_static(self,c): self.calls.append(('static',c))

def make(tmp_path, port=80):
    cfg=load_config('config.example.yaml',use_local=False); provider=RuntimeStatusProvider(AppState(cfg))
    # set requested runtime web port immutably through copied config
    if port != cfg.webui.listen_port:
        provider.state.config=cfg.model_copy(update={'webui':cfg.webui.model_copy(update={'listen_port':port})})
    ex=tmp_path/'config.example.yaml'; ex.write_text('switcher:\n  type: vmix\nwebui:\n  listen_port: 80\n')
    local=tmp_path/'config.local.yaml'; net=FakeNetwork()
    return TestClient(create_web_app(provider,config_example_path=ex,config_local_path=local,auth_enabled=False,network_backend=net)),net

def test_network_section_actual_state_and_static_visibility(tmp_path):
    c,_=make(tmp_path); text=c.get('/config').text
    assert 'Network / IPv4' in text and '192.168.1.60' in text and 'Wired connection 1' in text
    assert 'network-mode' in text and 'network-static-fields' in text and "mode.value!==\'static\'" in text

def test_static_requires_confirmation_and_uses_effective_web_port(tmp_path):
    c,n=make(tmp_path,8080)
    r=c.post('/config/network/validate',data={'mode':'static','address':'192.168.1.50','prefix':'24','gateway':'192.168.1.31','dns1':'192.168.1.31','dns2':''})
    assert r.status_code==200 and 'Confirm network change' in r.text and 'http://192.168.1.50:8080/' in r.text
    assert n.calls==[]
    token=re.search(r'name="network_token" value="([^"]+)"',r.text).group(1)
    r=c.post('/config/network/apply',data={'network_token':token})
    assert r.status_code==200 and n.calls[0][0]=='static'

def test_dhcp_confirmation_does_not_predict_address(tmp_path):
    c,n=make(tmp_path); r=c.post('/config/network/validate',data={'mode':'dhcp'})
    assert 'future DHCP address cannot be predicted' in r.text and n.calls==[]
    token=re.search(r'name="network_token" value="([^"]+)"',r.text).group(1)
    c.post('/config/network/apply',data={'network_token':token}); assert n.calls==[('dhcp',None)]

def test_invalid_static_never_reaches_apply(tmp_path):
    c,n=make(tmp_path); r=c.post('/config/network/validate',data={'mode':'static','address':'192.168.1.0','prefix':'24','gateway':'192.168.1.31','dns1':'1.1.1.1'})
    assert r.status_code==400 and n.calls==[]

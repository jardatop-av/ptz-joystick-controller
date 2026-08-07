from ptz_joystick_controller.models.switcher import SwitcherConfig, SwitcherType
from ptz_joystick_controller.switchers.factory import create_switcher, switcher_backend_name
from ptz_joystick_controller.switchers.atem import AtemSwitcher
from ptz_joystick_controller.switchers.atem_production import logical_to_native, native_to_logical
from ptz_joystick_controller.switchers.capabilities import get_source_ids
from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.discovery.network_probe import probe_atem, ProbeContext

class FakeClient:
    transition_state='idle'
    def __init__(self): self.connected=False; self.p='Input 5'; self.v='Input 2'; self.calls=[]
    def connect(self): self.connected=True
    def disconnect(self): self.connected=False
    def poll(self): return self.p,self.v
    def set_preview(self,s): self.calls.append(('preview',s)); self.v=s
    def cut(self): self.calls.append(('cut',)); self.p,self.v=self.v,self.p
    def auto(self): self.calls.append(('auto',)); self.p,self.v=self.v,self.p

def test_atem_4k8_capabilities_and_native_mapping():
    ids=get_source_ids('atem_television_studio_4k8')
    assert ids[:8] == tuple(f'Input {i}' for i in range(1,9))
    assert logical_to_native('Input 1') == 1 and logical_to_native('Input 8') == 8
    assert native_to_logical(1) == 'Input 1' and native_to_logical(8) == 'Input 8'

def test_factory_atem_alias_and_generic_commands():
    cfg=SwitcherConfig(type='atem',host='192.168.1.184',port=9910)
    assert cfg.type == 'atem_television_studio_4k8'
    client=FakeClient(); sw=create_switcher(cfg,offline=False,atem_client=client)
    assert isinstance(sw,AtemSwitcher)
    assert switcher_backend_name(cfg) == 'ATEM Television Studio 4K8'
    sw.connect(); assert sw.get_program_source()=='Input 5' and sw.get_preview_source()=='Input 2'
    sw.set_preview_source('Input 3'); assert client.calls[-1]==('preview','Input 3')
    sw.cut(); assert client.calls[-1]==('cut',)
    sw.auto(); assert client.calls[-1]==('auto',)

def test_atem_defaults_map_input_1_to_8_to_cameras():
    cfg=parse_config({'switcher':{'type':'atem','host':'x'}})
    assert len(cfg.ptz.cameras) >= 8
    for i in range(1,9): assert cfg.ptz_camera_for_source(f'Input {i}') == f'cam{i}'

def test_discovery_atem_is_readonly_by_construction(monkeypatch):
    import ptz_joystick_controller.discovery.network_probe as n
    class State: product_name='ATEM Television Studio 4K8'; protocol_version='2.32'
    class Client:
        confirmed=True
        def __init__(self,*a,**k): pass
        def connect(self): return State()
        def disconnect(self): pass
    monkeypatch.setattr(n,'AtemReadOnlyProbeClient',Client)
    r=probe_atem('192.168.1.184',ProbeContext())
    assert r and r.type=='ATEM' and r.port==9910 and 'Protocol 2.32' in r.details

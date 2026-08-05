from __future__ import annotations

import logging

import pytest

from ptz_joystick_controller.switchers.osee_duet_gsp import (
    DUET_GSP_ID_TO_SOURCE,
    DUET_SOURCE_TO_GSP_ID,
    OseeDuetGspController,
    OseeDuetSourceError,
    OseeDuetSourceMap,
)
from ptz_joystick_controller.switchers.osee_gsp import GspCommand, decode_gsp_packet, encode_gsp_command


class FakeTransport:
    def __init__(self) -> None:
        self.commands: list[GspCommand] = []

    def send_command(self, command: GspCommand) -> bytes:
        self.commands.append(command)
        return encode_gsp_command(command)


def test_full_source_mapping_both_directions() -> None:
    expected = {
        "Input 1": 1,
        "Input 2": 2,
        "Input 3": 3,
        "Input 4": 4,
        "Input 5": 4001,
        "Input 6": 4002,
        "Input 7": 4003,
        "Input 8": 4004,
        "MP1": 3010,
        "MP2": 3020,
        "M/SRC": 5001,
    }
    assert DUET_SOURCE_TO_GSP_ID == expected
    assert DUET_GSP_ID_TO_SOURCE == {value: key for key, value in expected.items()}
    for source, gsp_id in expected.items():
        assert OseeDuetSourceMap.to_gsp_id(source) == gsp_id
        ref = OseeDuetSourceMap.from_gsp_id(gsp_id)
        assert ref.canonical_id == source
        assert ref.known is True


@pytest.mark.parametrize("alias", ["5", "Input 5", "input5", " input 5 "])
def test_input_5_aliases(alias: str) -> None:
    assert OseeDuetSourceMap.normalize(alias) == "Input 5"
    assert OseeDuetSourceMap.to_gsp_id(alias) == 4001


def test_special_source_aliases() -> None:
    assert OseeDuetSourceMap.to_gsp_id("MP1") == 3010
    assert OseeDuetSourceMap.to_gsp_id("mp2") == 3020
    assert OseeDuetSourceMap.to_gsp_id("M/SRC") == 5001
    assert OseeDuetSourceMap.to_gsp_id("MSRC") == 5001


@pytest.mark.parametrize("source", ["0", "9", "Input 0", "Input 9", "Foo", ""])
def test_unknown_input_rejected_clearly(source: str) -> None:
    with pytest.raises(OseeDuetSourceError, match="source|range|empty"):
        OseeDuetSourceMap.to_gsp_id(source)


@pytest.mark.parametrize(
    ("source", "expected_id"),
    [("Input 1", 1), ("Input 5", 4001), ("MP1", 3010)],
)
def test_set_preview_packets(source: str, expected_id: int) -> None:
    transport = FakeTransport()
    controller = OseeDuetGspController(transport)
    packet = controller.set_preview(source)
    assert decode_gsp_packet(packet) == GspCommand(id="pvwIndex", type="set", value=(expected_id,))


def test_set_program_packet() -> None:
    transport = FakeTransport()
    controller = OseeDuetGspController(transport)
    packet = controller.set_program("Input 8")
    assert decode_gsp_packet(packet) == GspCommand(id="pgmIndex", type="set", value=(4004,))


def test_cut_packet_has_no_value() -> None:
    packet = OseeDuetGspController(FakeTransport()).cut()
    command = decode_gsp_packet(packet)
    assert command.id == "cutTransition"
    assert command.type == "set"
    assert command.value is None


def test_auto_packet_has_no_value() -> None:
    packet = OseeDuetGspController(FakeTransport()).auto()
    command = decode_gsp_packet(packet)
    assert command.id == "autoTransition"
    assert command.type == "set"
    assert command.value is None


def test_pgm_and_preview_push_update_state() -> None:
    controller = OseeDuetGspController(FakeTransport())
    assert controller.handle_command(GspCommand(id="pgmIndex", type="pus", value=(4001,))) is True
    assert controller.handle_command(GspCommand(id="pvwIndex", type="pus", value=(5001,))) is True
    assert controller.state.program is not None
    assert controller.state.program.canonical_id == "Input 5"
    assert controller.state.preview is not None
    assert controller.state.preview.canonical_id == "M/SRC"


def test_transition_status_push_updates_state() -> None:
    controller = OseeDuetGspController(FakeTransport())
    assert controller.handle_command(GspCommand(id="transitionStatus", type="pus", value=(1,))) is True
    assert controller.state.transition_status == (1,)


def test_unknown_gsp_source_id_does_not_crash(caplog: pytest.LogCaptureFixture) -> None:
    controller = OseeDuetGspController(FakeTransport())
    with caplog.at_level(logging.WARNING):
        assert controller.handle_command(GspCommand(id="pvwIndex", type="pus", value=(9999,))) is True
    assert controller.state.preview is not None
    assert controller.state.preview.gsp_id == 9999
    assert controller.state.preview.canonical_id is None
    assert controller.state.preview.display_name == "Unknown GSP source 9999"
    assert "unknown preview GSP source id=9999" in caplog.text


def test_tally_does_not_override_selected_source() -> None:
    controller = OseeDuetGspController(FakeTransport())
    controller.handle_command(GspCommand(id="pvwIndex", type="pus", value=(2,)))
    assert controller.handle_command(GspCommand(id="pvwTally", type="pus", value=(1, 2, 3))) is False
    assert controller.state.preview is not None
    assert controller.state.preview.canonical_id == "Input 2"

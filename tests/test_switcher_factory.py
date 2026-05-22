from __future__ import annotations

import pytest

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.models.source_types import SourceType
from ptz_joystick_controller.models.sources import UnsupportedSourceError
from ptz_joystick_controller.models.switcher import SwitcherType
from ptz_joystick_controller.switchers import FakeSwitcher, create_switcher, get_available_sources, get_source_ids
from ptz_joystick_controller.switchers.factory import create_switcher as factory_create_switcher


@pytest.mark.parametrize(
    ("switcher_type", "expected_ids"),
    [
        (SwitcherType.OSEE_GOSTREAM_DECK, ("CH1", "CH2", "CH3", "CH4", "AUX", "STILL1", "STILL2", "BLACK")),
        (
            SwitcherType.OSEE_GOSTREAM_DUET,
            ("CH1", "CH2", "CH3", "CH4", "CH5", "CH6", "CH7", "CH8", "MP1", "MP2", "M/SRC"),
        ),
        (SwitcherType.ATEM_MINI_PRO, ("CH1", "CH2", "CH3", "CH4")),
        (
            SwitcherType.ATEM_TV_STUDIO_PRO_4K,
            ("CH1", "CH2", "CH3", "CH4", "CH5", "CH6", "CH7", "CH8", "CH9", "CH10"),
        ),
    ],
)
def test_static_source_lists_for_fixed_input_switchers(switcher_type: SwitcherType, expected_ids: tuple[str, ...]) -> None:
    assert get_source_ids(switcher_type) == expected_ids


def test_vmix_exposes_input_1_to_100() -> None:
    ids = get_source_ids(SwitcherType.VMIX)
    assert len(ids) == 100
    assert ids[0] == "Input 1"
    assert ids[-1] == "Input 100"


def test_source_metadata_marks_non_camera_sources() -> None:
    sources = {source.id: source for source in get_available_sources(SwitcherType.OSEE_GOSTREAM_DECK)}
    assert sources["CH1"].type == SourceType.CAMERA
    assert sources["AUX"].type == SourceType.AUX
    assert sources["STILL1"].type == SourceType.STILL
    assert sources["BLACK"].type == SourceType.BLACK


def test_switcher_factory_returns_offline_fake_switcher() -> None:
    config = parse_config({"switcher": {"type": "atem_mini_pro", "host": None}})
    switcher = create_switcher(config.switcher)

    assert isinstance(switcher, FakeSwitcher)
    assert get_source_ids(SwitcherType.ATEM_MINI_PRO) == tuple(source.id for source in switcher.get_available_sources())
    assert not switcher.is_connected()
    switcher.connect()
    assert switcher.is_connected()


def test_real_switcher_factory_mode_is_not_implemented() -> None:
    config = parse_config({"switcher": {"type": "vmix", "host": None}})

    with pytest.raises(NotImplementedError):
        factory_create_switcher(config.switcher, offline=False)


def test_fake_switcher_set_preview_rejects_unsupported_source() -> None:
    switcher = FakeSwitcher(SwitcherType.OSEE_GOSTREAM_DUET)

    with pytest.raises(UnsupportedSourceError):
        switcher.set_preview_source("Input 99")


def test_fake_switcher_cut_swaps_program_and_preview() -> None:
    switcher = FakeSwitcher(
        SwitcherType.ATEM_MINI_PRO,
        program_source_id="CH1",
        preview_source_id="CH2",
    )

    switcher.cut()

    assert switcher.get_program_source() == "CH2"
    assert switcher.get_preview_source() == "CH1"
    assert switcher.transition_log == ["cut"]

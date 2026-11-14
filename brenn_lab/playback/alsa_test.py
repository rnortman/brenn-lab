"""The ALSA output's parts that need no library: card selection and the USB id.
Nothing here loads libasound, and the last test holds that nothing did.
"""

from pathlib import Path

from brenn_lab.data import Failure
from brenn_lab.playback import alsa
from brenn_lab.playback.output import OUTPUTS

FLEX = alsa.Card(2, "Flex XVF3800 L16K6Ch", "reSpeaker Flex XVF3800 L16K6Ch at usb-0000:00:14.0-3")
HDA = alsa.Card(0, "HDA Intel PCH", "HDA Intel PCH at 0xf7f10000 irq 32")
TWIN = alsa.Card(3, "Flex XVF3800 L16K6Ch", "reSpeaker Flex XVF3800 L16K6Ch at usb-0000:00:14.0-4")


def test_one_card_matches_by_name_or_long_name_in_any_case() -> None:
    assert alsa.select([HDA, FLEX], "flex xvf3800") == FLEX
    assert alsa.select([HDA, FLEX], "USB-0000:00:14.0-3") == FLEX
    assert alsa.select([HDA, FLEX, TWIN], "14.0-4") == TWIN


def test_no_matching_card_is_refused_with_the_cards_listed() -> None:
    failure = alsa.select([HDA], "Flex")
    assert isinstance(failure, Failure)
    assert failure.reason == "no_card"
    assert failure.data == {
        "selector": "Flex",
        "cards": [{"index": 0, "name": HDA.name, "long_name": HDA.long_name}],
    }
    assert "HDA Intel PCH" in failure.detail


def test_no_card_at_all_is_refused() -> None:
    failure = alsa.select([], "Flex")
    assert isinstance(failure, Failure) and failure.reason == "no_card"


def test_several_matching_cards_are_refused() -> None:
    failure = alsa.select([HDA, FLEX, TWIN], "Flex")
    assert isinstance(failure, Failure)
    assert failure.reason == "ambiguous_card"
    assert failure.data is not None and failure.data["matched"] == [2, 3]


def test_usb_id_is_the_kernels_file_or_none(tmp_path: Path) -> None:
    (tmp_path / "card2").mkdir()
    (tmp_path / "card2" / "usbid").write_text("2886:0022\n")
    (tmp_path / "card0").mkdir()
    assert alsa.usb_id(2, tmp_path) == "2886:0022"
    assert alsa.usb_id(0, tmp_path) is None


def test_the_alsa_output_kind_is_made_without_loading_the_library() -> None:
    output = OUTPUTS["alsa"]("Flex", {})
    assert isinstance(output, alsa.AlsaOutput)
    assert output.kind == "alsa"


def test_no_libasound_is_mapped() -> None:
    maps = Path("/proc/self/maps").read_text()
    assert "libasound" not in maps

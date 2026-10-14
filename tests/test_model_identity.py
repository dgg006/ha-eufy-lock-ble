"""Similar model codes must not be silently conflated in the device registry."""

from custom_components.eufy_lock_ble.const import MODELS, model_id_from_serial


def test_t8510_and_suffix_variant_are_distinct() -> None:
    assert model_id_from_serial("T8510TEST12345678") == "T8510"
    assert model_id_from_serial("T8510PTEST1234567") == "T8510P"
    assert MODELS["T8510"] == "Smart Lock Touch"
    assert MODELS["T8510P"] != MODELS["T8510"]


def test_unknown_model_keeps_its_code() -> None:
    assert model_id_from_serial("T8599TEST12345678") == "T8599"

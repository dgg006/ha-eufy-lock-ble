# Supported hardware and identification

## Confirmed hardware

| Item | Verified information |
|---|---|
| Manufacturer | eufy Security / Anker |
| Product | Smart Lock Touch |
| Model code | **T8510** |
| Power | **Four AA batteries** |
| Built-in Wi-Fi | **None; Bluetooth model** |
| Reference retail package | **Lock without Wi-Fi bridge; bridge sold separately** |
| Transport used by this integration | Bluetooth Low Energy |
| Tested firmware in this distribution | **2.0.2.3** |
| Tested Home Assistant release | **2026.10.1** |
| Bluetooth path tested | ESPHome Bluetooth proxy |
| Hardware actions verified | Lock and unlock |
| Reads verified | Bolt state, battery, settings and heartbeat |

The upstream project separately reports T8510 firmware 2.0.4.2. That statement
is retained as an upstream report, not a second hardware test performed here.

## Product appearance

[![T8510 Smart Lock Touch: fingerprint/keypad exterior and thumb-turn interior](https://cdn.shopify.com/s/files/1/0504/7094/4954/products/E130SmartLock.png?v=1698303947)](https://www.eufy.com/products/t8510)

Manufacturer product image, eufy/Anker. Linked from the manufacturer's T8510
product page and not included under this repository's MIT software license.
The image illustrates the product; check the **model label**, not appearance alone.

Manufacturer documentation identifies T8510 as Smart Lock Touch. Some product
and support pages use E130 for this model, while other documentation also uses
E130 for a different code. Therefore this integration uses **T8510** as its
primary identity. Do not assume every E130 product is supported.

The manufacturer's T8510 specification lists four AA batteries, a fingerprint
reader, a keypad, a mechanical key and Bluetooth, with no built-in Wi-Fi. The
standalone version was sold without a bridge, which could be purchased separately;
some retail bundles included one. A separate eufy Wi-Fi bridge is not required
by this integration. These are identifying details, not claims
that this integration manages fingerprints, PINs or all manufacturer features.

## Similar products are not interchangeable

| Code or label | Status in this distribution |
|---|---|
| T8510, Smart Lock Touch | Hardware-tested as above |
| T8510P, Smart Lock S230 | Different model; not hardware-tested here |
| T8520, Smart Lock Touch & Wi-Fi | Different model; not hardware-tested here |
| T8500, T8504, T8530 or other T85 devices | Not hardware-tested here |
| E130 with no model code supplied | Insufficient identification |

Bluetooth discovery of a name starting with T85 does not establish support.
If reporting another variant, provide its model code, firmware and outcome
without publishing its full serial number or Bluetooth address.

## Sources

- [eufy: Introducing Smart Lock Touch, T8510 specifications](https://service.eufy.com/article-description/Introducing-eufy-Smart-Lock-Touch)
- [eufy: T8510 product page, also titled Smart Lock E130](https://www.eufy.com/products/t8510)
- [eufy: Bluetooth models and Wi-Fi bridges](https://service.eufy.com/article-description/Which-eufy-Smart-Lock-Supports-WiFi-Bridge)
- [eufy: battery differences, including T8510 and T8520](https://service.eufy.com/article-description/How-to-Charge-eufy-Smart-Lock)
- [Upstream integration and its separate firmware report](https://github.com/Leicas/ha-eufy-lock-ble)

Model information reviewed on 2026-10-14. The manufacturer's naming can vary by
region and revision; a suffix such as P must not be silently discarded.

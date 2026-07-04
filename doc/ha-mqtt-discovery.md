# Home Assistant MQTT Discovery: distilled developer reference

Spec-grounded reference for the ekm-proxy HA-discovery source: parsing Home Assistant MQTT discovery messages so they can be republished as Homie 5 / eBus devices. Sourced from the official Home Assistant documentation (URLs and access date at the bottom). This focuses on the sensor component and on device-based discovery, which is the flavor we care about most.

## 1. Discovery topic layouts

MQTT discovery is enabled by default. The default discovery prefix is `homeassistant` (configurable in the MQTT integration options). Discovery subscriptions are done at QoS 0 by default. There are two topic layouts.

### Single-component discovery

```text
<discovery_prefix>/<component>/[<node_id>/]<object_id>/config
```

- `<discovery_prefix>`: defaults to `homeassistant`.
- `<component>`: one of the supported MQTT platforms (`sensor`, `binary_sensor`, `switch`, etc.).
- `<node_id>` (optional): a grouping segment that Home Assistant ignores for identity; it only exists to help structure topics. Character class `[a-zA-Z0-9_-]`.
- `<object_id>`: identifies the entity/device within the topic. Character class `[a-zA-Z0-9_-]`. Note: the `<object_id>` in the topic does NOT influence the resulting `entity_id`. Best practice is to set `<object_id>` to the entity's `unique_id` and omit `<node_id>`.

The payload is a single JSON dictionary configuring exactly one component. It may carry a shared `device` (`dev`) block so that multiple single-component messages can be tied to the same device via matching identifiers.

### Device-based discovery (the flavor we care about)

```text
<discovery_prefix>/device/<object_id>/config
```

Here the literal segment `device` replaces the component name, and a single JSON payload describes an entire device with many components at once. This is recommended when a device has multiple components: it sends the `device` info only once and reduces message count.

Payload structure (root level):

- `device` (`dev`): mapping, mandatory. Cannot be overridden per component.
- `origin` (`o`): mapping, mandatory (required for device discovery).
- `components` (`cmps`): mapping of component object id -> per-component config.
- Optional shared options at the root that apply to all components: `availability` options, `command_topic`, `state_topic`, `qos`, `encoding`.

The `cmps` (components) map is the heart of device discovery:

- Each KEY is a component object id (an arbitrary stable string chosen by the publisher, for example `some_unique_component_id1`). This key, combined with the topic's `<object_id>`, forms the discovery identity of that component (Home Assistant logs it as, for example, `0AFFD2 bla`).
- Each VALUE is a per-component config dictionary. It works just like a single-component payload body.
- Each component config MUST include `platform` (`p`), naming the component type, for example `"p": "sensor"`. This is required even when otherwise removing a component.
- Each entity component MUST include a `unique_id` (`uniq_id`) and MUST have a `device` context. For device discovery the `device` context is inherited from the root `dev` block.
- Each component config must have at least one component-specific config option beyond `platform`.

Removal semantics (useful to know for a robust parser): publishing an empty retained string to a device or component discovery topic removes it. Within `cmps`, a component whose value is just `{"p": "sensor"}` (platform only, no other keys) is an explicit removal of that component. A payload `{"migrate_discovery": true}` is a migration control message, not a real config.

Example device-based payload (abbreviated keys):

```json
{
  "dev": {
    "ids": "ea334450945afc",
    "name": "Kitchen",
    "mf": "Bla electronics",
    "mdl": "xya",
    "sw": "1.0",
    "sn": "ea334450945afc",
    "hw": "1.0rev2"
  },
  "o": {
    "name": "bla2mqtt",
    "sw": "2.1",
    "url": "https://bla2mqtt.example.com/support"
  },
  "cmps": {
    "some_unique_component_id1": {
      "p": "sensor",
      "device_class": "temperature",
      "unit_of_measurement": "°C",
      "value_template": "{{ value_json.temperature }}",
      "unique_id": "temp01ae_t"
    },
    "some_unique_id2": {
      "p": "sensor",
      "device_class": "humidity",
      "unit_of_measurement": "%",
      "value_template": "{{ value_json.humidity }}",
      "unique_id": "temp01ae_h"
    }
  },
  "state_topic": "sensorBedroom/state",
  "qos": 2
}
```

Note that both components share the root-level `state_topic` and each selects its own field via `value_template`. This shared-topic-plus-per-field-template pattern is exactly what an EKM-style multi-register device looks like.

## 2. The `dev` (device) block and the `o` (origin) block

The `device` block ties entities into Home Assistant's device registry. It requires at least one of `identifiers` or `connections`. Keys (full name / abbreviation):

- `identifiers` / `ids`: string or list of IDs that uniquely identify the device (for example a serial number).
- `connections` / `cns`: list of `[connection_type, connection_identifier]` tuples, for example `[["mac", "02:5b:26:a8:dc:12"]]`.
- `name`: device name.
- `manufacturer` / `mf`.
- `model` / `mdl`.
- `model_id` / `mdl_id`.
- `sw_version` / `sw`.
- `hw_version` / `hw`.
- `serial_number` / `sn`.
- `configuration_url` / `cu`.
- `suggested_area` / `sa`.
- `via_device`: identifier of a device that routes messages between this device and Home Assistant (hub or parent). Used for device topology.

The `origin` block records the software that produced the discovery message. It is required for device discovery. Keys (full name / abbreviation):

- `name`: name of the origin application. Required.
- `sw_version` / `sw`: software version of the origin application.
- `support_url` / `url`: support URL of the origin application.

## 3. Abbreviation key table (sensor-relevant)

Home Assistant lets discovery payloads use short keys to save memory. A parser must expand these. Full set relevant to a sensor plus the device and origin blocks:

| Short | Long |
| --- | --- |
| `p` | `platform` |
| `dev` | `device` |
| `o` | `origin` |
| `cmps` | `components` |
| `uniq_id` | `unique_id` |
| `dev_cla` | `device_class` |
| `unit_of_meas` | `unit_of_measurement` |
| `stat_cla` | `state_class` |
| `val_tpl` | `value_template` |
| `stat_t` | `state_topic` |
| `stat_tpl` | `state_template` |
| `stat_val_tpl` | `state_value_template` |
| `avty` | `availability` |
| `avty_t` | `availability_topic` |
| `avty_tpl` | `availability_template` |
| `avty_mode` | `availability_mode` |
| `pl_avail` | `payload_available` |
| `pl_not_avail` | `payload_not_available` |
| `json_attr_t` | `json_attributes_topic` |
| `json_attr_tpl` | `json_attributes_template` |
| `json_attr` | `json_attributes` |
| `name` | `name` |
| `qos` | `qos` |
| `ic` | `icon` |
| `en` | `enabled_by_default` |
| `ent_cat` | `entity_category` |
| `exp_aft` | `expire_after` |
| `frc_upd` | `force_update` |
| `sug_dsp_prc` | `suggested_display_precision` |
| `dsp_prc` | `display_precision` |
| `e` | `encoding` |
| `cmd_t` | `command_topic` |
| `lrst_t` | `last_reset_topic` |
| `lrst_val_tpl` | `last_reset_value_template` |
| `ops` | `options` |
| `t` | `topic` (availability list item) |
| `migr_discvry` | `migrate_discovery` |
| `~` | base-topic macro (see note below) |

Device-block abbreviations: `ids`/`identifiers`, `cns`/`connections`, `mf`/`manufacturer`, `mdl`/`model`, `mdl_id`/`model_id`, `sw`/`sw_version`, `hw`/`hw_version`, `sn`/`serial_number`, `cu`/`configuration_url`, `sa`/`suggested_area`, `name`/`name`.

Origin-block abbreviations: `name`/`name`, `sw`/`sw_version`, `url`/`support_url`.

Abbreviation quirks to watch for:

- The device block and the origin block reuse the short key `sw`, but it means `sw_version` in BOTH. `name` also appears in device, origin, and component scopes. Resolve abbreviations WITHIN the correct scope, not globally.
- `o.url` expands to `support_url` (origin), whereas a top-level `url_t` is `url_topic` (unrelated). Do not conflate.
- A base topic macro `~` may be defined in the payload. In any config value whose key ends in `_topic`, a leading or trailing `~` is replaced with the base topic value. Expand `~` before using any topic string.
- Unknown keys are allowed and ignored by Home Assistant (forward compatibility). A parser should tolerate unknown keys rather than reject the payload.
- Payloads may freely mix short and long keys in the same message.

## 4. Extracting a sensor value

A sensor's live value arrives on `state_topic` (`stat_t`). The payload is often a JSON document carrying many fields. `value_template` (`val_tpl`) is a Jinja2 template that selects and optionally transforms the value for this specific entity.

The canonical form is `{{ value_json.<FIELD> }}`, where `value_json` is the parsed JSON payload and `<FIELD>` is the key to pull out, for example `{{ value_json.kWh_Tot }}` or `{{ value_json.temperature }}`. Nested access appears as `{{ value_json.Timer1.Arm }}`. When the payload is a bare scalar (not JSON), the template uses `value` instead, for example `{{ as_datetime(value) }}`.

For our translator the key job is to recover the JSON field name referenced by `value_json.<FIELD>` in the template: that field name is the source key we read from the shared state-topic payload to feed the corresponding Homie property. In the multi-component EKM case, all components share one `state_topic` and differ only by their `value_template` field, so the field name is the discriminator. A parser should extract the `value_json.<path>` reference (including dotted nested paths) from the template string; be prepared for filters and expressions around it (`| round(2)`, `as_datetime(...)`, conditionals) that are not needed to recover the field name but must not confuse the extractor.

Related keys: `json_attributes_topic` (`json_attr_t`) plus `json_attributes_template` (`json_attr_tpl`) publish a JSON dictionary that becomes extra entity attributes rather than the primary state; `suggested_display_precision` (`sug_dsp_prc`) is display rounding only; `state_class` (`stat_cla`) marks measurement semantics (`measurement`, `total`, `total_increasing`) that matter for long-term statistics.

## 5. Availability

Availability tells Home Assistant whether the entity is online. Two mutually exclusive forms.

Single-topic form:

- `availability_topic` (`avty_t`): the MQTT topic carrying online/offline updates.
- `payload_available` (`pl_avail`): payload meaning available. Default `online`.
- `payload_not_available` (`pl_not_avail`): payload meaning unavailable. Default `offline`.
- `availability_template` (`avty_tpl`): optional Jinja2 template to extract availability from the topic payload; its rendered result is compared to `payload_available` / `payload_not_available`.

List form:

- `availability` (`avty`): a list of availability sources, each an object with `topic` (`t`) required, plus optional `payload_available`, `payload_not_available`, and `value_template`. Must NOT be combined with `availability_topic`.
- `availability_mode` (`avty_mode`): how multiple sources combine. Default `latest`. Values: `all` (every topic must report available), `any` (at least one), `latest` (the most recent message on any source wins).

Device-level availability may be set once at the root of a device-discovery payload and shared by all components.

## 6. Sensor `device_class` values for electricity metering

`device_class` (`dev_cla`) sets frontend semantics (icon, unit validation, statistics behavior). It may be `null`. The table below lists the classes relevant to electricity metering with their canonical Home Assistant units. Where multiple units are valid, the SI-scaled family is shown; the specific `unit_of_measurement` in the payload is authoritative for the actual value.

| device_class | Meaning | Canonical HA unit(s) |
| --- | --- | --- |
| `energy` | Energy (consumed/produced) | `Wh`, `kWh`, `MWh`, `GWh`, `MJ`, `GJ` |
| `energy_storage` | Stored energy | `Wh`, `kWh`, `MWh`, `MJ`, `GJ` |
| `power` | Real (active) power | `W`, `kW`, `MW`, `GW`, `TW`, `mW` |
| `apparent_power` | Apparent power | `VA` |
| `reactive_power` | Reactive power | `var` (and `kvar` in recent HA) |
| `reactive_energy` | Reactive energy | `varh`, `kvarh` |
| `current` | Electrical current | `A`, `mA` |
| `voltage` | Electrical voltage | `V`, `mV`, `µV`, `kV`, `MV` |
| `power_factor` | Power factor | `%`, or unitless (`null`) |
| `frequency` | Frequency | `Hz`, `kHz`, `MHz`, `GHz` |
| `temperature` | Temperature | `°C`, `°F`, `K` |
| `duration` | Elapsed time | `d`, `h`, `min`, `s`, `ms` |
| `timestamp` | Point in time | none (ISO 8601 datetime string) |

Notes: `energy` with `state_class: total_increasing` is the standard pattern for a cumulative meter register (for example lifetime kWh). `timestamp` sensors carry an ISO 8601 datetime and have no unit; the value template typically renders one via `as_datetime(value)`. `power_factor` is dimensionless and may be reported either as a fraction with no unit or as a percentage.

## 7. How this maps to Homie 5 / eBus

The translation is structurally clean:

- HA `device` (`dev`) block -> one Homie 5 device. Use `dev.identifiers`/`ids` (or a connection identifier) as the stable Homie device id source; carry `name`, `mf`/manufacturer, `mdl`/model, `sw`/`hw` versions, and `sn` into Homie device metadata. `via_device` maps to Homie device parent/topology if modeled.
- HA component (entity) in `cmps` -> a Homie node plus property. The `cmps` key and/or the entity `unique_id` is the stable identity for the node/property; `unique_id` is required and is the safest join key.
- `device_class` + `unit_of_measurement` carry the physical semantics: map them onto the Homie property `$datatype`, `$unit`, and any eBus type hints. The device_class tells you what the quantity IS (energy, power, voltage); the unit tells you the scale.
- `value_template` field name is the SOURCE key: the `value_json.<FIELD>` reference identifies which field of the shared `state_topic` payload feeds this Homie property's value. `state_class` (measurement / total / total_increasing) informs whether the Homie property is an instantaneous reading or a cumulative counter.
- Availability (device-level or per-component) maps onto Homie device/node `$state` (online -> ready, offline -> lost/disconnected), honoring non-default `payload_available` / `payload_not_available` strings.

## Sources

Accessed 2026-07-03:

- Home Assistant MQTT integration (Discovery, device-based discovery, abbreviations): <https://www.home-assistant.io/integrations/mqtt/>
- Home Assistant MQTT Sensor component (sensor config keys, availability, examples): <https://www.home-assistant.io/integrations/sensor.mqtt/>
- Home Assistant sensor device classes and units reference: <https://www.home-assistant.io/integrations/sensor/#device-class>

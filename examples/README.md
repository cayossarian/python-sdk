# Examples

Example scripts demonstrating the ebus-sdk.

## Available Examples

### simple-device

Basic Homie device that publishes sensor data (temperature, humidity, air pressure).

```bash
./simple-device --config /path/to/broker-cfg.json
```

### simple-controller

Controller that auto-discovers Homie devices and monitors property changes.

```bash
./simple-controller --config /path/to/broker-cfg.json
```

### utility-meter

Publishes a single eBus utility-meter device (`energy.ebus.device.utility-meter`, per the Electrification Bus `data-models/utility-meter.md`) with the standard `info` / `meter` / `status` / `doe` capabilities. The `doe` capability is two `json` properties (`doe/import-limit`, `doe/export-limit`), each a JSON array of time-windowed envelope objects. A local HTTP endpoint stands in for the utility's out-of-band backhaul for runtime DOE updates (`POST /doe/import-limit`, `POST /doe/export-limit`).

```bash
./utility-meter --config ./utility-meter-cfg.example.json --broker-config /path/to/broker-cfg.json
```

**Need a broker to run against?** [`broker-quickstart`](https://github.com/electrification-bus/broker-quickstart) brings up a local eBus broker on macOS with one command (`python -m laptop.run`), including the mDNS advertisement that `--discover` (below) resolves. Its `scripts/laptop-bench.sh` runs this simulator against that broker with `--discover`, end to end; see the [laptop quickstart](https://github.com/electrification-bus/broker-quickstart/blob/main/docs/laptop-quickstart.md).

Add `--discover` to find the broker over mDNS (`_secure-mqtt._tcp`) instead of using the `host`/`port` in the broker config; the broker config still supplies the TLS material. Needs the `mdns` extra (`pip install 'ebus-sdk[mdns]'`):

```bash
./utility-meter --config ./utility-meter-cfg.example.json --broker-config /path/to/broker-cfg.json --discover
```

Set a DOE envelope at runtime. The body is a single envelope object or an array of them (the retained schedule); it is set atomically as one `json` value:

```bash
curl -X POST http://localhost:8765/doe/import-limit \
  -H 'Content-Type: application/json' \
  -d '[{"power-limit": 12000, "source": "GRID", "start-time": "2026-07-01T16:00:00Z", "end-time": "2026-07-01T20:00:00Z"}]'
```

Envelope fields: `power-limit` (integer W) and/or `apparent-power-limit` (integer VA) — at least one required; `source` (one of `CONTRACT` / `REGULATOR` / `EQUIPMENT` / `GRID` / `UNKNOWN`, optional); `start-time` / `end-time` (ISO-8601 UTC, optional). POST an empty body or `null` to clear the signal for that direction. The same shape is served at `/doe/export-limit`.

### simple-span-controller

SPAN Panel controller with mDNS discovery. Connects via MQTTS and monitors power flow properties.

Requires the `mdns` extra:

```bash
pip install ebus-sdk[mdns]
```

**Basic usage (requires password):**

```bash
./simple-span-controller <serial-number> <password>
./simple-span-controller <serial-number> <password> --broker-host 192.168.1.100
```

**With SPAN-API utilities (automatic credentials and CA cert):**

If you have access to the SPAN-API-Client-Docs repository, you can enable automatic credential and certificate management:

```bash
# Add SPAN-API-Client-Docs lib to PYTHONPATH
export PYTHONPATH=$PYTHONPATH:/path/to/SPAN-API-Client-Docs/lib

# Run without password - uses ~/.span-auth.json
./simple-span-controller <serial-number>

# Force insecure mode (skip CA cert verification)
./simple-span-controller <serial-number> --insecure
```

When SPAN-API utilities are available:

- Password is retrieved from `~/.span-auth.json` if not provided on command line
- CA certificate is fetched/cached in `~/.span-ca-certs/` for secure TLS verification
- Use `--insecure` to skip certificate verification even when CA cert is available

### ha-discovery-bridge

Bridges eBus/Homie devices to Home Assistant MQTT discovery (the reverse of the HA -> eBus path). Publishes a synthetic eBus device (meter / battery / settable control), runs a `Controller` + `HaDiscoveryBridge` that discovers it and emits `homeassistant/device/<id>/config`, then subscribes an independent client (standing in for Home Assistant) to capture and verify the emitted config, and finally shows the config being cleared on device removal. Runs against a real broker but needs no running Home Assistant. See [`doc/ha-discovery-bridge.md`](../doc/ha-discovery-bridge.md) for the full guide.

```bash
./ha-discovery-bridge --config /path/to/broker-cfg.json
./ha-discovery-bridge                 # defaults to 127.0.0.1:1883, no auth
./ha-discovery-bridge --keep-running  # leave it up for a real HA to consume
```

No broker handy? A throwaway local one works: `mosquitto -c <(printf 'listener 1883 127.0.0.1\nallow_anonymous true\n')`, then run with no `--config`.

## Configuration

All examples accept broker configuration via:

1. **Command line**: `--config /path/to/broker-cfg.json`
2. **Environment variable**: `EBUS_BROKER_CFG=/path/to/broker-cfg.json`

### Broker Config Format

```json
{
  "host": "mqtt.example.com",
  "port": 1883,
  "authentication": {
    "type": "USER_PASS",
    "username": "myuser",
    "password": "mypassword"
  }
}
```

For MQTTS (TLS) with insecure mode (no certificate verification):

```json
{
  "host": "secure-broker.example.com",
  "port": 8883,
  "use_tls": true,
  "tls_insecure": true,
  "authentication": {
    "type": "USER_PASS",
    "username": "myuser",
    "password": "mypassword"
  }
}
```

For MQTTS with CA certificate verification (secure mode):

```json
{
  "host": "secure-broker.example.com",
  "port": 8883,
  "use_tls": true,
  "tls_ca_cert": "/path/to/ca-cert.crt",
  "tls_insecure": false,
  "authentication": {
    "type": "USER_PASS",
    "username": "myuser",
    "password": "mypassword"
  }
}
```

**TLS Options:**

- `use_tls`: Enable TLS/SSL connection (required for port 8883)
- `tls_ca_cert`: Path to CA certificate file for server verification
- `tls_ca_data`: CA certificate content as PEM string or DER bytes (alternative to file)
- `tls_insecure`: Skip certificate verification (default: true for backwards compatibility)

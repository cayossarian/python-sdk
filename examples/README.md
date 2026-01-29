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

### simple-span-controller

SPAN Panel controller with mDNS discovery. Connects via MQTTS and monitors power flow properties.

Requires the `mdns` extra:
```bash
pip install ebus-sdk[mdns]
```

```bash
./simple-span-controller <serial-number> <password>
./simple-span-controller <serial-number> <password> --broker-host 192.168.1.100
```

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

For MQTTS (TLS):

```json
{
  "host": "secure-broker.example.com",
  "port": 8883,
  "use_tls": true,
  "authentication": {
    "type": "USER_PASS",
    "username": "myuser",
    "password": "mypassword"
  }
}
```

# OutBackPower Mate3 integration with Home Assistant

![Home Assistant](/docs/HomeAssistant/example_ha_sunsynk-power-flow-card.png)  
![card](/docs/HomeAssistant/example_ha_outback_stat_card.png)  
![card](/docs/HomeAssistant/example_ha_outback_config_card.png)

---
# Supported Hardware
Devices are detected automatically from the SunSpec blocks the MATE3 reports - no configuration is needed to select a hardware family.

| Device | SunSpec blocks | Notes |
|---|---|---|
| Radian / FXR inverter, split phase | 64115 / 64116 | |
| Radian / FXR inverter, single phase | 64117 / 64116 | |
| FX / VFX inverter | 64113 / 64114 | Single phase. Also reports daily buy/sell/output/charger kWh |
| FM60 / FM80 charge controller | 64111 / 64112 | |
| FLEXnet-DC battery monitor | 64118 / 64119 | |

FX/VFX and Radian inverters publish the same JSON fields and MQTT topics, so Home Assistant automations are portable between the two families.

---
# Installation

### Requirements

- A compatible OutBack Power system connected to a MATE3/MATE3S with Modbus TCP enabled.
- Python 3 installed on the computer running the scripts.
- Network access to the MATE3/MATE3S.
- An MQTT broker for MQTT publishing and Home Assistant integration.
- MariaDB (optional, for database storage).

### Setup

**1. Download the repository**

Download or clone the repository to your computer.

**2. Install Python dependencies**

Open a terminal in the project directory and run:

```bash
python -m pip install -r requirements.txt
```

**3. Configure the MATE3/MATE3S connection**

Edit `config.cfg` and configure your MATE3/MATE3S IP address:

```ini
[MATE3 connection]
mate3_ip = 192.168.0.150
mate3_modbus = 502
```

Replace the example IP address with your device's actual address.

**4. Configure MQTT (optional)**

To enable MQTT publishing and Home Assistant MQTT Auto Discovery, configure the `[MQTT]` section of `config.cfg` with your broker address, port, and credentials.

```ini
MQTT_active = true
MQTT_discovery_active = true
```

For Home Assistant sensor availability, see [Availability when the MATE3 stops answering](#availability-when-the-mate3-stops-answering).

**5. Configure the execution mode**

ReadMateStatusModBus supports continuous operation (**daemon mode**) or single execution (**run-once mode**).

Configure the desired mode in the `[General]` section of `config.cfg`:

```ini
daemon_active = true
scan_frequency = 60
```

Set `daemon_active = false` to run the script once.

**6. Run ReadMateStatusModBus**

From the project directory, execute:

```bash
python ReadMateStatusModBus.py
```

Check the script output to confirm successful communication with the MATE3/MATE3S.

If MQTT Auto Discovery is enabled, the detected devices and sensors should appear automatically in Home Assistant.

---
# How Does This Software Work?
This integration is based on:
- `ReadMateStatusModBus.py` (RMS) for reading MATE3/MATE3S
- `ChangeMateStatusModBus.py` (CMS) for writing data
- `sdc.py` (SDC - SunSpec Data Configuration), a shared Single Source of Truth based on the OutBack AXS application note, used by both RMS and CMS for SunSpec block, register, datatype, access, scale factor, enum, and bitfield definitions

- RMS creates a JSON file with almost all useful parameters extracted from MATE3/MATE3S and pushes MQTT data for selected parameters. More functionalities of RMS can be configured in the config file (`config.cfg`).
- MQTT Auto Discovery for Home Assistant devices/sensors is implemented as of v3.0.0. Manual configuration of MQTT sensors in YAML or using the JSON file remains valid options.
- CMS uses the shared SDC definitions to validate and write supported parameters to MATE3/MATE3S.

---
# ReadMateStatusModBus.py
- Queries MATE3/MATE3S, retrieves and formats data, optionally stores it in MariaDB (more info [here](/docs/MariaDB/Readme.txt)), publishes MQTT data, and generates a JSON file.
- Enum and bitfield definitions are read from the shared SDC data model instead of being maintained as hardcoded lookup lists in ReadMate.
- The `ReadMateStatusModBus.py` script can run in **daemon mode** with a configurable scan interval, or in **run-once mode** where a task should be created (Windows or Linux).
- `config.cfg` is the configuration file for the script and should be set up based on your needs.

### CLI usage
Force a single execution in run-once mode with temporary `config.cfg` overrides:
```
python ReadMateStatusModBus.py daemon_active=false
```
* Supports multiple `key=value` arguments (e.g. `MQTT_active=false`, `MQTT_discovery_active=false`)
```
python ReadMateStatusModBus.py MQTT_active=false MQTT_discovery_active=false
```
* Allows only enabling/disabling features (MQTT, JSON, SQL); see the whitelist of valid CLI parameters
* Any valid CLI parameter automatically forces run-once mode; therefore, `daemon_active=true` from CLI is ignored

Default behavior (no CLI args) follows `config.cfg`.

### FLEXnet-DC shunt roles
The FNDC reports what each of its three shunts measures, but not what the shunt is *wired to* - a shunt on a diversion load and one on an inverter look identical over ModBus. The calculated summary values therefore need to be told, in the `[Labels]` section of `config.cfg`:

```ini
shunt_a_role = unused
shunt_b_role = solar
shunt_c_role = inverter
```

| Role | Meaning |
|---|---|
| `solar`, `charger` | Source. Current normally flows into the battery |
| `inverter`, `load`, `diverter` | Sink. Current normally flows out of the battery |
| `unused` | Shunt is not connected. Left out of the battery current total |
| `other` | Counted in the battery total, but gets no total of its own |

For each role in use, the summary publishes `shunt_<role>_current` and `shunt_<role>_power`. A role you do not have produces no sensors.

**Both** the current and the power of a role are reported in the direction that role normally runs: a source reads positive while supplying the battery, a sink positive while drawing from it. So an inverter drawing 350 W reports `shunt_inverter_current` 6.7 and `shunt_inverter_power` 351, rather than a negative current beside a positive power.

The raw reading in the FNDC's own sign convention, where positive always means into the battery, stays available unchanged on the per shunt sensors `shunt_a_current`, `shunt_b_current` and `shunt_c_current`.

Roles are optional. **Leave all three blank and the previous behaviour is kept**, where shunt C was assumed to be a diversion load and published as `diverted_current` / `diverted_power`. In that case the output is unchanged in every respect, including the JSON file, which does not gain the role fields. Those two values are still published when a shunt is given the `diverter` role, so existing dashboards keep working - and `diverted_current` keeps the raw FNDC convention it has always had, so upgrading never flips the sign of a sensor you already use. Its role equivalent `shunt_diverter_current` follows the role convention above.

Shunt labels (`shunt_a`, `shunt_b`, `shunt_c`) remain free text and set the display name of the shunt sensors in Home Assistant. They do not affect any calculation - that is what the roles are for.

Changing a role takes effect on the next restart. The sensors for the old role are not removed from Home Assistant automatically - see [Removing entities that are no longer published](#removing-entities-that-are-no-longer-published) below.

### ReadMateStatusModBus.sh (Optional)
- This is an example Linux script that can be used to start `ReadMateStatusModBus.py`. The script should run at the desired update frequency (e.g., every minute). Refer to your OS or distribution’s documentation for setting up daemons or scheduled tasks.

---
# ChangeMateStatusModBus.py
`ChangeMateStatusModBus.py` writes supported ModBus parameters to MATE3/MATE3S.

Starting with v1.0.0, ChangeMate uses the shared SDC data model instead of a fixed hardcoded command list. Parameters defined in the SDC with the appropriate write access can be handled through the common command interface.

- Commands can be provided through CLI arguments or `mate_input.json`.
- Multiple parameters can be changed during a single run.
- Commands and values are validated against the SDC before writing.
- ModBus writes remain protected by the `write_active` configuration option.
- More details can be found [here](/docs/ChangeMate_Status/ChangeMate_1.0_Upgrade_Notes.md).
- Automation in Home Assistant can be achieved using [shell commands](https://www.home-assistant.io/integrations/shell_command/). Examples are in the documentation folder [here](/docs/HomeAssistant/example_shell_command_usage_yaml.txt).

> **Note:** The `mate_input.json` structure changed in ChangeMate v1.0.0 to support the new SDC-based command model. 
Existing files from previous ChangeMate versions should be updated. See the ChangeMate documentation for CLI usage, available parameters, validation, examples, and migration instructions.

---
# Home Assistant Configuration
## Integration Variants
### 1. Automatic: MQTT Auto Discovery (RECOMMENDED)

```ini
MQTT_discovery_active = true
```
- At startup, OutBack devices are scanned.
- Based on your hardware configuration (inverters / chargers / FNDC), entities are automatically created in Home Assistant:
  - Inverters  
  - Chargers  
  - FNDC  
  - Summary  
  - System  

#### Availability when the MATE3 stops answering

Home Assistant sensors use retained MQTT values, which means they may continue displaying their last readings even when communication with the MATE3 is lost.

Starting with **ReadMateStatusModBus v1.5.1**, sensor availability is managed automatically through two separate MQTT topics:

```text
outback/status          MATE3 communication status
outback/availability    Home Assistant sensor availability
```

MQTT Auto Discovery automatically configures Home Assistant sensors to use `outback/availability`, so no manual sensor configuration is required.

In the `[MQTT]` section of `config.cfg`, you can configure:

```ini
MQTT_availability_threshold = 10
```

In **daemon mode**, this example marks Home Assistant sensors as unavailable after **10 consecutive failed MATE3 communication cycles**. A successful cycle resets the failure counter. With a 60-second scan interval, this corresponds to approximately 10 minutes of consecutive failures. This helps prevent brief communication interruptions from marking sensors as unavailable. The value `10` is an example, not the script default.

In **single-run mode**, availability is updated immediately based on the result of each execution, regardless of this setting.

Sensors become available again after successful communication with the MATE3 and MQTT data publishing.

**Note:** Availability reflects communication with the MATE3, not whether the ReadMate script itself is running. If the script stops unexpectedly, the last retained availability state remains unchanged.

#### Removing entities that are no longer published
Discovery configs and sensor values are published to MQTT with the **retain** flag, and the topics are one per sensor. When your configuration changes, the script publishes the topics that now apply - it does not publish to the topics that no longer apply, and nothing overwrites them. The broker keeps serving those old retained messages, including across a broker restart, so Home Assistant recreates the entity every time it subscribes and shows its last value indefinitely. Deleting the device in Home Assistant does not help, because the retained config brings it straight back.

This happens after a shunt role change, after swapping between inverter families, or whenever a sensor stops applying for any other reason.

```ini
MQTT_discovery_cleanup = false
```

Left at the default, the script leaves those sensors untouched. If unused Home Assistant sensors may exist, the normal log reports:

```
Unused HA sensors may exist. Set MQTT_discovery_cleanup=true to remove them
```

The detailed list of sensors not published by the current configuration is available when DEBUG logging is enabled.

The script cannot tell which of those actually exist in Home Assistant without reading the broker back, which it deliberately does not do. On a new installation, the DEBUG list therefore simply represents sensors that do not apply to the detected hardware and current configuration.

Set `MQTT_discovery_cleanup` to `true` and the script clears them instead, by publishing an empty retained message to both the discovery config topic and the state topic - which is the mechanism MQTT Auto Discovery provides for removing an entity. This deletes entities from Home Assistant, so it is off unless you ask for it.

Only sensors are cleared, and only within devices that are still present. A device that is missing from a scan is never removed, so a charge controller that is briefly offline does not disappear from Home Assistant.

### 2. Manual: MQTT Sensors Configuration in YAML
```ini
MQTT_discovery_active = false
```
- Use MQTT Explorer to view the full list of available topics.
- Examples can be found [here](/docs/HomeAssistant/examples_ha_mqtt_sensors_manual_configuration.txt)

### 3. Manual: JSON File Decoding (FILE integration)
- A new folder named `data` should be created in the `www` directory in Home Assistant (e.g., `/config/www/data`).  
This path must match the JSON output path defined in `config.cfg`. RMS will save the JSON file to this location.
```ini
MQTT_discovery_active = false
```
- Use the `file` integration in Home Assistant to decode the JSON file saved in:
  ```
  /config/www/data/mate_status.json
  ```

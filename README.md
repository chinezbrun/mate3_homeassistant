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
# How Does This Software Work?
This integration is based on:
- `ReadMateStatusModBus.py` (RMS) for reading MATE3  
- `ChangeMateStatusModBus.py` (CMS) for writing data  

- An MQTT broker must be installed (MQTT documentation is outside the scope of this project).
- RMS creates a JSON file with almost all useful parameters extracted from Mate3 and pushes MQTT data for selected parameters. More functionalities of RMS can be configured in the config file (`config.cfg`).
- MQTT Auto Discovery for Home Assistant devices/sensors is implemented as of v3.0.0. Manual configuration of MQTT sensors in YAML or using the JSON file remains valid options.
- Running CMS will write a specific parameter to MATE3. More details can be found [here](/docs/ChangeMate_Status/ChangeMateStatusInstructions.txt).
---
# ReadMateStatusModBus.py
- Queries MATE3/MATE3S, retrieves data, formats it, registers it in the MariaDB database (optional - more info [here](/docs/MariaDB/Readme.txt)), pushes MQTT data, and returns a JSON file.
- The `ReadMateStatusModBus.py` script can run in **daemon mode** with a configurable scan interval, or in **run-once mode** where a task should be created (Windows or Linux).
- `config.cfg` is the configuration file for the script and should be set up based on your needs.
### CLI usage
Force a single execution in run-once mode with temporary `config.cfg` overrides:
```
python ReadMateStatusModbus.py daemon_active=false
```
* Supports multiple `key=value` arguments (e.g. `MQTT_active=false`, `MQTT_discovery_active=false`)
```
python ReadMateStatusModbus.py MQTT_active=false MQTT_discovery_active=false
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

Changing a role takes effect on the next restart, and the sensors for the old role are removed from Home Assistant automatically. If you are upgrading from a version before roles existed, entities created by that version are cleared on the first run of this one.

### ReadMateStatusModBus.sh (Optional)
- This is an example Linux script that can be used to start `ReadMateStatusModBus.py`. The script should run at the desired update frequency (e.g., every minute). Refer to your OS or distribution’s documentation for setting up daemons or scheduled tasks.
---
# ChangeMateStatusModBus.py
- `ChangeMateStatusModBus.py` can write ModBus data to MATE3. A limited set of parameters can be modified.  
- The script accepts arguments to indicate the parameters to be changed. It can also change multiple parameters during a single run.  
- More details can be found [here](/docs/ChangeMate_Status/ChangeMateStatusInstructions.txt).  
- Automation in Home Assistant can be achieved using [shell commands](https://www.home-assistant.io/integrations/shell_command/). Examples are in the documentation folder [here](/docs/HomeAssistant/example_shell_command_usage_yaml.txt).
---
# Home Assistant Configuration
- A new folder named `data` should be created in the `www` directory in Home Assistant (e.g., `/config/www/data`).  
This path must match the JSON output path defined in `config.cfg`. RMS will save the JSON file to this location.

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
### 2. Manual: MQTT Sensors Configuration in YAML
```ini
MQTT_discovery_active = false
```
- Use MQTT Explorer to view the full list of available topics.
- Examples can be found [here](/docs/HomeAssistant/examples_ha_mqtt_sensors_manual_configuration.txt)
### 3. Manual: JSON File Decoding (FILE integration)
```ini
MQTT_discovery_active = false
```
- Use the `file` integration in Home Assistant to decode the JSON file saved in:
  ```
  /config/www/data/mate_status.json
  ```


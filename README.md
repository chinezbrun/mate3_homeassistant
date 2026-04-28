# OutBackPower Mate3 integration with Home Assistant

![Home Assistant](/docs/HomeAssistant/example_ha_sunsynk-power-flow-card.png)  
![card](/docs/HomeAssistant/example_ha_outback_stat.png)  
![card](/docs/HomeAssistant/example_ha_outback_config.png)
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
---
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
- A new folder named `data` should be created in the `www` folder located in Home Assistant (e.g., `\home-assistant\www\data`). This folder is where the JSON file will be saved by RMS.

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
- Examples can be found [here](/docs/ChangeMate_Status/ChangeMateStatusInstructions.txt)
### 3. Manual: JSON File Decoding (FILE integration)
```ini
MQTT_discovery_active = false
```
- Use the `file` integration in Home Assistant to decode the JSON file saved in:
  ```
  /config/www/data/mate_status.json
  ```


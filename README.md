# mate3_homeassistant
OutBackPower Mate3 integration with Home Assistant

![Home Assistant](/docs/example_ha_view1.png)

# How Does This Software Work?
This integration is based on `ReadMateStatusModBus.py` (RMS) for reading MATE3 and `ChangeMateStatusModBus.py` (CMS) for writing data.

- RMS creates a JSON file with almost all useful parameters extracted from Mate3 and pushes MQTT data for selected parameters. More functionalities of RMS can be configured in the config file (`config.cfg`).
- Home Assistant needs to be configured to receive MQTT or decode the JSON file.
- An MQTT broker must be installed (MQTT documentation is outside the scope of this project).
- Running CMS will write a specific parameter to MATE3. More details can be found [here](/docs/ChangeMate_Status/ChangeMateStatusInstructions.txt).

# ReadMateStatusModBus.py
- Queries MATE3/MATE3S, retrieves data, formats it, registers it in the MariaDB database (optional - more info [here](/docs/MariaDB/readme.txt)), pushes MQTT data, and returns a JSON file.
- The `ReadMateStatusModBus.py` script should run at a set interval. A task should be created for this (Windows or Linux).
- `config.cfg` is the configuration file for the script and should be set up based on your needs.

### ReadMateStatusModBus.sh (Optional)
This is an example Linux script that can be used to start `ReadMateStatusModBus.py`. The script should run at the desired update frequency (e.g., every minute). Refer to your OS or distribution’s documentation for setting up daemons or scheduled tasks.

# ChangeMateStatusModBus.py
- `ChangeMateStatusModBus.py` can write ModBus data to MATE3. A few parameters can be changed. 
- The script accepts arguments to indicate the parameters to be changed. It can also change multiple parameters during a single run.
- More details can be found [here](/docs/ChangeMate_Status/ChangeMateStatusInstructions.txt).
- Integration in Home Assistant can be achieved using [shell commands](https://www.home-assistant.io/integrations/shell_command/). Examples are below and in the documentation folder [here](/docs/HomeAssistant/).

# Home Assistant Configuration
A new folder named `data` should be created in the `www` folder located in Home Assistant (e.g., `\home-assistant\www\data`). This folder is where the JSON file will be saved by RMS.

## Integration Variants
1. **MQTT - Predefined Parameters with Individual Topics**:
   - Use MQTT Explorer to view the full list of available topics.

2. **MQTT - Access to All Parameters**:
   - A single common topic, `outback/mate`, provides a payload containing the JSON with all data (same as the JSON file saved by the script).

3. **JSON File Decoding**:
   - Use the `file` platform integration in Home Assistant to decode the JSON file saved in the Home Assistant folder (`/config/www/data/status.json`).

## Sensor Configuration in `configuration.yaml`

### Variant 1: MQTT - Predefined Sensors
Add the following sensors in `configuration.yaml`:

~~~yaml
mqtt:
  sensor:
    - name: outback_ac_input
      state_topic: "outback/inverters/1/ac_input"
      unit_of_measurement: "V"
      state_class: "measurement"

    - name: outback_ac_output
      state_topic: "outback/inverters/1/ac_output"
      unit_of_measurement: "V"
      state_class: "measurement"
    
    - name: outback_grid_status
      state_topic: "outback/inverters/1/ac_use"
    
    - name: outback_grid_input_mode
      state_topic: "outback/inverters/1/grid_input_mode"
    
    - name: outback_charger_mode
      state_topic: "outback/inverters/1/charger_mode"
    
    - name: outback_ops_mode
      state_topic: "outback/inverters/1/operating_modes" 
    
    - name: outback_charge_mode
      state_topic: "outback/chargers/1/charge_mode"
    
    - name: outback_soc
      state_topic: "outback/fndc/state_of_charge"
      unit_of_measurement: "%"
      state_class: "measurement"
    
    - name: outback_bat_voltage
      state_topic: "outback/fndc/battery_voltage"
      value_template: "{{value | round(1) }}"
      unit_of_measurement: "V"
      state_class: "measurement"
    
    - name: outback_bat_temp
      state_topic: "outback/fndc/battery_temperature"
      unit_of_measurement: "°C"
      state_class: "measurement"
    
    - name: outback_pv_power
      state_topic: "outback/summary/cc_total_watts"
      unit_of_measurement: "W"
      state_class: "measurement"
    
    - name: outback_input_amp
      state_topic: "outback/fndc/shunt_a_current"
      unit_of_measurement: "A"
      state_class: "measurement" 
    
    - name: outback_used_amp
      state_topic: "outback/fndc/shunt_b_current"
      unit_of_measurement: "A"
      state_class: "measurement"
    
    - name: outback_divert_amp
      state_topic: "outback/fndc/shunt_c_current"
      unit_of_measurement: "A" 
      state_class: "measurement"
    
    - name: outback_since_charge_met
      state_topic: "home-assistant/outback/fndc_since_charge_met"
      unit_of_measurement: "days" 
      value_template: '{{value | round(2)}}'
    
    - name: outback_charge_met
      state_topic: "outback/fndc/charge_params_met"

    - name: outback_today_net_input_kwh
      state_topic: "outback/fndc/todays_net_input_kWh"
      state_class: 'total_increasing'
      device_class: energy
      unit_of_measurement: kWh
      value_template: '{{value | round(2)}}'
    
    - name: outback_today_net_output_kwh
      state_topic: "outback/fndc/todays_net_output_kWh"
      state_class: 'total_increasing'
      device_class: energy
      unit_of_measurement: kWh
      value_template: '{{value | round(2)}}'
    
    - name: outback_pv_daily_kwh
      state_topic: "outback/mate"
      state_class: 'total_increasing'
      device_class: energy
      unit_of_measurement: kWh
      value_template: '{{value_json.summary.pv_daily_Kwh | round(2)}}'
~~~

### Variant 2: MQTT - Custom Sensors
Add sensors as per your needs in `configuration.yaml`. Below are examples:

~~~yaml
mqtt:    
  sensor:
    ## Sensor for AC input voltage
    - name: solar_ac_input_file
      state_topic: "outback/mate"
      value_template: '{{ value_json.devices[0].ac_input_voltage }}'
      unit_of_measurement: "V"

    ## Sensor to calculate selling watts 
    - name: outback_sell_watts
      state_topic: "outback/mate"
      value_template: '{{(value_json.devices[0].sell_current|int + value_json.devices[1].sell_current|int) * value_json.devices[0].ac_output_voltage|int}}'
      unit_of_measurement: 'W'

    ## Sensor to calculate buying watts
    - name: outback_buy_watts
      state_topic: "outback/mate"
      value_template: '{{(value_json.devices[0].buy_current|int + value_json.devices[1].buy_current|int) * value_json.devices[0].ac_input_voltage|int}}'
      unit_of_measurement: 'W'
~~~

### Variant 3: JSON File Decoding
Use the `file` platform integration to decode the JSON file saved by the script. Add the following sensors in `configuration.yaml`:

~~~yaml
sensor:
  - platform: file
    name: solar_ac_input_file
    file_path: /config/www/data/status.json
    value_template: '{{ value_json.devices[0].ac_input_voltage }}'
    unit_of_measurement: 'V'
  
  - platform: file
    name: solar_ac_output_file
    file_path: /config/www/data/status.json
    value_template: '{{ value_json.devices[0].ac_output_voltage }}'
    unit_of_measurement: 'V'
~~~

## Shell Command 
input_select with desired arguments should be prior defined
then add in configuration YAML
~~~yaml
shell_command:
  script_outback_change_grid_input_mode: "python3 /media/web/mate3_homeassistant/ChangeMateStatusModBus.py {{states.input_select.solar_grid_input_mode.state}}"
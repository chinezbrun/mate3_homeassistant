# mate3_homeassistant
 OutBackPower Mate3 integration with Home Assistant

![Home Assistant](/docs/ha.PNG)

#  How does this software work?
This integration is based on ReadMateStatusModBus.py (RMS) output.
- Once starts RMS creates a JSON file with almost all useful parameters extracted from Mate3 and send a MQTT command for integrated ones. More functionalities of RMS can be configured in config file - ReadMateStatusModBus.cfg.
- HA needs to be configured to receive MQTT or to decode the JSON file

# ReadMateStatusModBus.py
- Query MATE3/MATE3S and gets data, format, register in the database (optional) and returns a json file that is used for display current status. As of version 0.5.1 MQTT is implemented to further integration with Home Assistant.
- ReadMateStatusModBus.py script is running once every X minute -- task should be created (windows or Linux)
- ReadMateStatusModBus.cfg is the config file for this script
- same script is used also in https://github.com/chinezbrun/MonitorMate_ModBus

ReadMateStatusModBus.sh (is not mandatory)
===========
This is an example of InitScript for ReadMateStatusModBus.py in LINUX. This script should run with minimum one minute frequency .
See your specific OS/distributions documentation for setting up daemons/tasks.

# Home Assistant configuration
A new folder "data" should be created in "www" folder located in home-assistant (ex: \home-assistant\www\data). In this folder, JSON file will be saved by RMS.

Integration variants:
1. MQTT - predefined parameters (more can be added by request)
2. JSON file decoding - access to all parameters

sensors should be defined in configuration.yaml
1. variant MQTT - add below sensors:
~~~
sensor:
  - platform: mqtt
    name: "solar_ac_input"
    state_topic: "home-assistant/solar/solar_ac_input"
    unit_of_measurement: "V"
  - platform: mqtt
    name: "solar_ac_output"
    state_topic: "home-assistant/solar/solar_ac_output"
    unit_of_measurement: "V"  
  - platform: mqtt
    name: "solar_grid_status"
    state_topic: "home-assistant/solar/solar_ac_mode"
    unit_of_measurement: ""
  - platform: mqtt
    name: "solar_grid_input_mode"
    state_topic: "home-assistant/solar/solar_grid_input_mode"
    unit_of_measurement: ""
  - platform: mqtt
    name: "solar_charge_mode"
    state_topic: "home-assistant/solar/solar_charge_mode"
    unit_of_measurement: ""
  - platform: mqtt
    name: "solar_charger_mode"
    state_topic: "home-assistant/solar/solar_charger_mode"
    unit_of_measurement: ""
  - platform: mqtt
    name: "solar_ops_mode"
    state_topic: "home-assistant/solar/solar_operational_mode"
    unit_of_measurement: ""
  - platform: mqtt
    name: "solar_soc"
    state_topic: "home-assistant/solar/solar_soc"
    unit_of_measurement: "%"
  - platform: mqtt
    name: "solar_bat_voltage"
    state_topic: "home-assistant/solar/solar_bat_voltage"
    value_template: "{{value | round(1) }}"
    unit_of_measurement: "V"
  - platform: mqtt
    name: "solar_bat_temp"
    state_topic: "home-assistant/solar/solar_bat_temp"
    unit_of_measurement: "grd"
  - platform: mqtt
    name: "solar_pv_watts"
    state_topic: "home-assistant/solar/solar_pv_watts"
    unit_of_measurement: "W"    
  - platform: mqtt
    name: "solar_divert_amp"
    state_topic: "home-assistant/solar/solar_divert_amp"
    unit_of_measurement: "A"  
  - platform: mqtt
    name: "solar_used_amp"
    state_topic: "home-assistant/solar/solar_used_amp"
    unit_of_measurement: "A"  
  - platform: mqtt
    name: "solar_charge_met"
    state_topic: "home-assistant/solar/solar_charge_met"
    unit_of_measurement: "days"
  - platform: mqtt
    name: "solar_power_diversion_uptime"
    state_topic: "home-assistant/solar/power_diversion_uptime"
    unit_of_measurement: "days"

  - platform: template
    sensors:
      solar_diverted_watts:
        friendly_name: "solar_diverted_watts"
        unit_of_measurement: "W"
        value_template: "{{(states('sensor.solar_bat_voltage')|int) * (-1) * (states('sensor.solar_divert_amp')|int)}}"
      solar_used_watts:
        friendly_name: "solar_used_watts"
        unit_of_measurement: 'W'
        value_template: "{{(states('sensor.solar_bat_voltage')|int) * (-1) * (states('sensor.solar_used_amp')|int)}}"
~~~

2. variant JSON decoding - add sensors like as per your needs, bellow are some examples:

~~~
sensor:
  - platform: file
    name: solar_ac_input_file
    file_path: /config/www/data/status.json
    value_template: '{{ value_json.devices[0].ac_input_voltage }}'
    unit_of_measurement: 'v'  
  - platform: file
    name: solar_ac_output_file
    file_path: /config/www/data/status.json
    value_template: '{{ value_json.devices[0].ac_output_voltage }}'
    unit_of_measurement: 'v'
~~~

# mate3_homeassistant
 OutBackPower Mate3 integration with Home Assistant

![Home Assistant](/docs/ha.PNG)

#  How does this software work?
This integration is based on ReadMateStatusModBus.py (RMS) output.
- RMS creates a JSON file with almost all useful parameters extracted from Mate3 and send MQTT data for integrated ones. More functionalities of RMS can be configured in config file - ReadMateStatusModBus.cfg.
- HA needs to be configured to receive MQTT or to decode the JSON file

# ReadMateStatusModBus.py
- Query MATE3/MATE3S, gets data, format, register in the database (optional), push MQTT data and returns a JSON file.
- ReadMateStatusModBus.py script should run every X minute -- task should be created (windows or Linux)
- ReadMateStatusModBus.cfg is the config file for this script -- should be configured based on your needs
- same script is used also in https://github.com/chinezbrun/MonitorMate_ModBus

ReadMateStatusModBus.sh (is not mandatory)
===========
Example of LINUX script that can be used to start ReadMateStatusModBus.py. The script should run with desire update frequency (ex. every minute)
See your specific OS/distributions documentation for setting up daemons/tasks.

# Home Assistant configuration
A new folder "data" should be created in "www" folder located in home-assistant (where the ex: \home-assistant\www\data). In this folder, the JSON file will be saved by RMS.

Integration variants:
1. MQTT - predefined parameters
2. MQTT - access to all parameters, option avalailable as of v1.0.1 topic: home-assistant/solar/mate, payload: json with all data
3. JSON file decoding - access to all parameters

sensors should be defined in configuration.yaml

1. variant MQTT - predefined sensors -- add below sensors in configuration.yaml:
~~~
# Example configuration.yaml entry
mqtt:    
  sensor:
    - name: "solar_ac_input"
      state_topic: "home-assistant/solar/solar_ac_input"
      unit_of_measurement: "V"
      state_class: "measurement"
    - name: "solar_ac_output"
      state_topic: "home-assistant/solar/solar_ac_output"
      unit_of_measurement: "V"
      state_class: "measurement"    
    - name: "solar_grid_status"
      state_topic: "home-assistant/solar/solar_ac_mode"
    - name: "solar_grid_input_mode"
      state_topic: "home-assistant/solar/solar_grid_input_mode"
    - name: "solar_charger_mode"
      state_topic: "home-assistant/solar/solar_charger_mode"
    - name: "solar_charge_mode"
      state_topic: "home-assistant/solar/solar_charge_mode"
    - name: "solar_ops_mode"
      state_topic: "home-assistant/solar/solar_operational_mode"
    - name: "solar_soc"
      state_topic: "home-assistant/solar/solar_soc"
      unit_of_measurement: "%"
      state_class: "measurement"
    - name: "solar_bat_voltage"
      state_topic: "home-assistant/solar/solar_bat_voltage"
      value_template: "{{value | round(1) }}"
      unit_of_measurement: "V"
      state_class: "measurement"   
    - name: "solar_bat_temp"
      state_topic: "home-assistant/solar/solar_bat_temp"
      unit_of_measurement: "grd"
      state_class: "measurement"
    - name: "solar_pv_watts"
      state_topic: "home-assistant/solar/solar_pv_watts"
      unit_of_measurement: "W"
      state_class: "measurement"
    - name: "solar_divert_amp"
      state_topic: "home-assistant/solar/solar_divert_amp"
      unit_of_measurement: "A" 
      state_class: "measurement"
    - name: "solar_used_amp"
      state_topic: "home-assistant/solar/solar_used_amp"
      unit_of_measurement: "A"
      state_class: "measurement"
    - name: "solar_since_charge_met"
      state_topic: "home-assistant/solar/solar_since_charge_met"
      unit_of_measurement: "days" 
      value_template: '{{value | round(2)}}'
    - name: "solar_charge_met"
      state_topic: "home-assistant/solar/solar_charge_met"
      unit_of_measurement: "" 
    - name: "solar_today_net_input_kwh"
      state_topic: "home-assistant/solar/solar_today_net_input_kwh"
      unit_of_measurement: "KWh" 
      value_template: '{{value | round(2)}}'
    - name: "solar_today_net_output_kwh"
      state_topic: "home-assistant/solar/solar_today_net_output_kwh"
      unit_of_measurement: "KWh" 
      value_template: '{{value | round(2)}}'
    - name: "solar_power_diversion_uptime"
      state_topic: "home-assistant/solar/power_diversion_uptime"
      unit_of_measurement: "days"
    - name: "mqtt_client_uptime"
      state_topic: "home-assistant/solar/mqtt_client_uptime"
      unit_of_measurement: "days"
~~~
2. variant MQTT - add sensors like as per your needs,  below example for one sensor:
~~~
# Example configuration.yaml entry
mqtt:    
  sensor:
    - name: solar_ac_input_file
	  state_topic: "home-assistant/solar/mate"
	  value_template: '{{ value_json.devices[0].ac_input_voltage }}'
~~~
3. variant JSON decoding - add sensors like as per your needs, below are some examples:

~~~
# Example configuration.yaml entry
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

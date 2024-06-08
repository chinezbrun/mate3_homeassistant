# mate3_homeassistant
 OutBackPower Mate3 integration with Home Assistant

![Home Assistant](/docs/example_ha_view1.png)

#  How does this software work?
This integration is based on ReadMateStatusModBus.py (RMS) output.
- RMS creates a JSON file with almost all useful parameters extracted from Mate3 and push MQTT data for selected ones. More functionalities of RMS can be configured in config file - ReadMateStatusModBus.cfg.
- Home Assistant needs to be configured to receive MQTT or to decode the JSON file
- MQTT broker must be installed (MQTT documentation is out of this project scope)

# ReadMateStatusModBus.py
- Query MATE3/MATE3S, gets data, format, register in the database (optional), push MQTT data and returns a JSON file.
- ReadMateStatusModBus.py script should run every X minute -- task should be created (windows or Linux)
- ReadMateStatusModBus.cfg is the config file for this script -- should be configured based on your needs

ReadMateStatusModBus.sh (is not mandatory)
===========
Example of LINUX script that can be used to start ReadMateStatusModBus.py. The script should run with desire update frequency (ex. every minute)
See your specific OS/distributions documentation for setting up daemons/tasks.

# Home Assistant configuration
A new folder "data" should be created in "www" folder located in home-assistant (ex: \home-assistant\www\data). In this folder, the JSON file will be saved by RMS.

Integration variants:
1. MQTT - predefined parameters with individual topics (MQTT- Explorer can be used to see the full list of them)
2. MQTT - access to all parameters 
the one common topic:  'outback/mate' where the payload received will be the json with all data (same json as the normal one saved by the script in output location)
3. JSON file decoding - access to all parameters

sensors should be defined in configuration.yaml

1. variant MQTT - predefined sensors - add below sensors in configuration.yaml:
~~~
# Example configuration.yaml entry

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
      unit_of_measurement: "grd"
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
2. variant MQTT - add sensors as per your needs in configuration.yaml, below are some examples:
~~~
# Example configuration.yaml entry
mqtt:    
  sensor:
    ## sensor for ac_input_voltage
	- name: solar_ac_input_file
	  state_topic: "outback/mate"
	  value_template: '{{ value_json.devices[0].ac_input_voltage }}'
	  
    ## sensor to calculate selling watts 
    - name: outback_sell_watts
      state_topic: "outback/mate"
      value_template: '{{(value_json.devices[0].sell_current|int + value_json.devices[1].sell_current|int) * value_json.devices[0].ac_output_voltage|int}}'
      unit_of_measurement: 'W'
    
    ## sensor to calculate buying watts
    - name: outback_buy_watts
      state_topic: "outback/mate"
      value_template: '{{(value_json.devices[0].buy_current|int + value_json.devices[1].buy_current|int) * value_json.devices[0].ac_input_voltage|int}}'#    unit_of_measurement: 'W' 	  
	  
~~~
3. variant JSON decoding 
Using 'platform file' integration from Home Assistant you can decode the json file saved by the script in home assitant folder location: /config/www/data/status.json.
In this case just add sensors as per your needs in configuration.yaml, below are some examples:

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

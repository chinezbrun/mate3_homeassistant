import json
import time
import logging
import mysql.connector as mariadb
from datetime import datetime
from pymodbus.client.sync import ModbusTcpClient as ModbusClient
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder
from configparser import ConfigParser
import paho.mqtt.publish as publish
import shutil  
import sys, os

script_ver = "0.7.3_20230312"
print ("script version: "+ script_ver)

pathname          = os.path.dirname(sys.argv[0])        
fullpathname      = os.path.abspath(pathname)+'/ReadMateStatusModBus.cfg' 
print("full path:               ", fullpathname)

config            = ConfigParser()
config.read(fullpathname)

#MATE3 connection
mate3_ip          = config.get('MATE3 connection', 'mate3_ip')
mate3_modbus      = config.get('MATE3 connection', 'mate3_modbus')
sunspec_start_reg = 40000

# SQL Maria DB connection
SQL_active        = config.get('Maria DB connection', 'SQL_active')                             
host              = config.get('Maria DB connection', 'host')
db_port           = config.get('Maria DB connection', 'db_port')
user              = config.get('Maria DB connection', 'user')
password          = config.get('Maria DB connection', 'password')
database          = config.get('Maria DB connection', 'database')
output_path       = config.get('Path', 'output_path')
duplicate_active  = config.get('Path', 'duplicate_active')
duplicate_path    = config.get('Path', 'duplicate_path')

# MQTT 
MQTT_active                     = config.get('MQTT', 'MQTT_active')
MQTT_broker                     = config.get('MQTT', 'MQTT_broker')
MQTT_master_ac_input_voltage    = config.get('MQTT','MQTT_master_ac_input_voltage')
MQTT_master_output_ac_voltage   = config.get('MQTT','MQTT_master_output_ac_voltage')
MQTT_master_ac_use              = config.get('MQTT','MQTT_master_ac_use')
MQTT_master_operating_modes     = config.get('MQTT','MQTT_master_operating_modes')
MQTT_master_grid_input_mode     = config.get('MQTT','MQTT_master_grid_input_mode')
MQTT_master_charger_mode        = config.get('MQTT','MQTT_master_charger_mode')
MQTT_controller_1_cc_mode       = config.get('MQTT','MQTT_controller_1_cc_mode')
MQTT_fndc_battery_voltage       = config.get('MQTT','MQTT_fndc_battery_voltage')
MQTT_fndc_state_of_charge       = config.get('MQTT','MQTT_fndc_state_of_charge')
MQTT_fndc_battery_temperature   = config.get('MQTT','MQTT_fndc_battery_temperature')
MQTT_fndc_shunt_a_current       = config.get('MQTT','MQTT_fndc_shunt_a_current')
MQTT_fndc_shunt_c_current       = config.get('MQTT','MQTT_fndc_shunt_c_current')
MQTT_fndc_shunt_b_current       = config.get('MQTT','MQTT_fndc_shunt_b_current')
MQTT_fndc_charge_params_met     = config.get('MQTT','MQTT_fndc_charge_params_met')
MQTT_fndc_days_since_charge_met = config.get('MQTT','MQTT_fndc_days_since_charge_met')
MQTT_fndc_todays_net_input_kWh  = config.get('MQTT','MQTT_fndc_todays_net_input_kWh')
MQTT_fndc_todays_net_output_kWh = config.get('MQTT','MQTT_fndc_todays_net_output_kWh')
MQTT_summary_cc_total_watts     = config.get('MQTT','MQTT_summary_cc_total_watts') 
MQTT_all_data                   = config.get('MQTT','MQTT_all_data')

debug             = config.get('General', 'debug')

print("output location:         ", output_path)
print("duplicate output active: ", duplicate_active)
print("SQL active  :            ", SQL_active)
print("MQTT active :            ", MQTT_active)
print("debug active:            ", debug)

# Setup logging
if debug == "true":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%Y%m%d %H:%M:%S')
logging.getLogger(__name__)

now              = datetime.now()
date_str         = now.strftime("%Y-%m-%dT%H:%M:%S")
date_sql         = datetime.now().replace(second=0, microsecond=0)

device_list=[          #used in main loop      
    "VFXR3048_master", #port 1   used for MQTT data
    "VFXR3048_slave",  #port 2
    "FM60",            #port 3
    "FM80",            #port 4
    "FNDC"             #port 5   used for MQTT data
    ]

shunt_list=[           # used in main loop
    "Solar",           # Shunt A
    "Invertor",        # Shunt B
    "Diverter",        # Shunt C
    ]

# Define the dictionary mapping SUNSPEC DID's to Outback names
# Device IDs definitions = (DID)
# AXS_APP_NOTE.PDF from Outback website has the data
mate3_did = {
    64110: "Outback block",
    64111: "Charge Controller Block",
    64112: "Charge Controller Configuration block",    
    64115: "Split Phase Radian Inverter Real Time Block",
    64116: "Radian Inverter Configuration Block",
    64117: "Single Phase Radian Inverter Real Time Block",
    64113: "FX Inverter Real Time Block",
    64114: "FX Inverter Configuration Block",
    64119: "FLEXnet-DC Configuration Block",
    64118: "FLEXnet-DC Real Time Block",
    64120: "Outback System Control Block",
    101: "SunSpec Inverter - Single Phase",
    102: "SunSpec Inverter - Split Phase",
    103: "SunSpec Inverter - Three Phase",
    64255: "OpticsRE Statistics Block",
    65535: "End of SunSpec"
}

# Subroutines
# Read SunSpec Header with logic from pymodbus example
def decode_int16(signed_value):
    """
    Negative numbers (INT16 = short)
      Some manufacturers allow negative values for some registers. Instead of an allowed integer range 0-65535,
      a range -32768 to 32767 is allowed. This is implemented as any received value in the upper range (32768-65535)
      is interpreted as negative value (in the range -32768 to -1).
      This is two’s complement and is described at http://en.wikipedia.org/wiki/Two%27s_complement.
      Help functions to calculate the two’s complement value (and back) are provided in MinimalModbus.
    """

    # Outback has some bugs in their firmware it seems. The FlexNet DC Shunt current measurements
    # return an offset from 65535 for negative values. No reading should ever be higher then 2000. So use that
    # print("int16 RAW: {!s}".format(signed_value))

    if signed_value > 32768+2000:
        return signed_value - 65535
    elif signed_value >= 32768:
        return int(32768 - signed_value)
    else:
        return signed_value
    
#convert decimal to binary string    
def binary(decimal) :
    otherBase = ""
    while decimal != 0 :
        otherBase  =  str(decimal % 2) + otherBase
        decimal    //=  2
    return otherBase
    #return otherBase [::-1] #invert de string    

def get_common_block(basereg):
    """ Read and return the sunspec common information
    block.
    :returns: A dictionary of the common block information
    """
    length   = 69
    response = client.read_holding_registers(basereg, length + 2)
    decoder  = BinaryPayloadDecoder.fromRegisters(response.registers,
                                                 byteorder=Endian.Big,
                                                 wordorder=Endian.Big)
    return {
        'SunSpec_ID': decoder.decode_32bit_uint(),
        'SunSpec_DID': decoder.decode_16bit_uint(),
        'SunSpec_Length': decoder.decode_16bit_uint(),
        'Manufacturer': decoder.decode_string(size=32),
        'Model': decoder.decode_string(size=32),
        'Options': decoder.decode_string(size=16),
        'Version': decoder.decode_string(size=16),
        'SerialNumber': decoder.decode_string(size=32),
        'DeviceAddress': decoder.decode_16bit_uint(),
        'Next_DID': decoder.decode_16bit_uint(),
        'Next_DID_Length': decoder.decode_16bit_uint(),
    }

# Read SunSpec header
def getSunSpec(basereg):
    # Read two bytes from basereg, a SUNSPEC device will start with 0x53756e53
    # As 8bit ints they are 21365, 28243
    try:
        response = client.read_holding_registers(basereg, 2)
    except:
        return None

    if response.registers[0] == 21365 and response.registers[1] == 28243:
        logging.info(".. SunSpec device found. Reading Manufacturer info")
    else:
        return None
    # There is a 16 bit string at basereg + 4 that contains Manufacturer
    response = client.read_holding_registers(basereg + 4, 16)
    decoder  = BinaryPayloadDecoder.fromRegisters(response.registers,
                                                 byteorder=Endian.Big,
                                                 wordorder=Endian.Big)
    manufacturer = decoder.decode_string(16)
    
    if "OUTBACK_POWER" in str(manufacturer.upper()):
        logging.info(".. Outback Power device found")
    else:
        logging.info(".. Not an Outback Power device. Detected " + manufacturer)
        return None
    try:
        register = client.read_holding_registers(basereg + 3)
    except:
        return None
    blocksize = int(register.registers[0])
    return blocksize

def getBlock(basereg):
    #print(basereg) #DPO debug
    try:
        register = client.read_holding_registers(basereg)
    except:
        return None
    blockID = int(register.registers[0])
    # Peek at block style
    try:
        register = client.read_holding_registers(basereg + 1)
    except:
        return None
    blocksize = int(register.registers[0])
    blockname = None
    try:
        blockname = mate3_did[blockID]
    except:
        print("ERROR: Unknown device type with DID=" + str(blockID))
    return {"size": blocksize, "DID": blockname}

#error log subroutine
def ErrorPrint (str) :
    try:
        with open(output_path + "/data/general_info.log","r") as file:
            save = file.read()
        with open(output_path + "/data/general_info.log","w") as file:
            file = open(output_path + "/data/general_info.log","a")
            file.write(now.strftime("%d/%m/%Y %H:%M:%S "))
            file.write(str + "\n")
            print(str)
        with open(output_path + "/data/general_info.log","a") as file:
            file.write(save)
        return
    except OSError:
        print(str,"Error: RMS - Errorhandling block: double error")

#------------------------------------------------
#  Start MATE3 ModBus Interface
#------------------------------------------------
# Try to build the mate3 MODBUS connection
logging.info("Building MATE3 MODBUS connection")
start_run  = datetime.now() # used only for runtime calculation

# Mate3 connection
try:
    client = ModbusClient(mate3_ip, mate3_modbus)
    logging.info(".. Make sure we are indeed connected to an Outback power system")
    reg    = sunspec_start_reg
    size   = getSunSpec(reg)
    if size is None:
        logging.info("We have failed to detect an Outback system. Exciting")
        exit()
except:
    client.close()
    ErrorPrint("Error: RMS - Fail to connect to MATE")
    logging.info(".. Failed to connect to MATE3. Enable SUNSPEC and check port. Exciting")
    exit()
logging.info(".. Connected OK to an Outback system")

#This is the main loop
#--------------------------------------------------------------

devices            = []                           # used for JSON file - list of data for all devices
various            = []                           # used for JSON file - different data not connected with MateMonitoring project
db_devices_values  = []                           # used for MariaDB upload - list of all data for all devices  
db_devices_sql     = []                           # used for MariaDB upload - list of all data for all devices 
mqtt_devices       = []                           # used for MQTT - list with topics and payloads 
CC_total_watts     = 0                            # used for MQTT to sum the pv power from charge controlers
CC_total_daily_kwh = 0                            # used for MQTT to sum the daily pv power from charge controlers  

startReg = reg + size + 4

while True:
    time={                                         # used for JSON file - servertime now
    "relay_local_time": date_str,
    "mate_local_time": date_str,
    "server_local_time": date_str}

    reg = startReg
    for block in range(0, 30):
        blockResult = getBlock(reg)
        
        try:        
            if "Single Phase Radian Inverter Real Time Block" in blockResult['DID']:
                logging.info(".. Detected a Single Phase Radian Inverter Real Time Block" + " -Registry:"+str(reg))
                response = client.read_holding_registers(reg + 2, 1)
                port=(response.registers[0]-1)
                address=port+1
                logging.info(".... Connected on HUB port " + str(response.registers[0]))
       
                # Inverter Output current
                response = client.read_holding_registers(reg + 7, 1)
                gs_single_inverter_output_current = round(response.registers[0],2)
                logging.info(".... FXR Inverted output current (A) " + str(gs_single_inverter_output_current))
               
                response = client.read_holding_registers(reg + 8, 1)
                gs_single_inverter_charge_current = round(response.registers[0],2)
                logging.info(".... FXR Charger current (A) " + str(gs_single_inverter_charge_current))
                
                response = client.read_holding_registers(reg + 9, 1)
                gs_single_inverter_buy_current = round(response.registers[0],2)
                logging.info(".... FXR Input current (A) " + str(gs_single_inverter_buy_current))
                
                response = client.read_holding_registers(reg + 30, 1)
                gs_single_ac_input_voltage = round(response.registers[0],2)
                logging.info(".... FXR AC Input Voltage " + str(gs_single_ac_input_voltage))

                response = client.read_holding_registers(reg + 13, 1)
                gs_single_output_ac_voltage = round(response.registers[0],2)
                logging.info(".... FXR Voltage Out (V) " + str(gs_single_output_ac_voltage))
                
                response = client.read_holding_registers(reg + 10, 1)
                GS_Single_Inverter_Sell_Current = round(response.registers[0],2)
                logging.info(".... FXR Sell current (A) " + str(GS_Single_Inverter_Sell_Current))
               
                response = client.read_holding_registers(reg + 14, 1)
                gs_single_inverter_operating_mode = int(response.registers[0])
                operating_modes_list = [
                "Off",                    # 0
                "Searching",              # 1
                "Inverting",              # 2
                "Charging",               # 3
                "Silent",                 # 4
                "Float",                  # 5
                "Equalize",               # 6
                "Charger Off",            # 7
                "Support",                # 8
                "Sell",                   # 9
                "Pass-through",           # 10
                "Slave Inverter On",      # 11
                "Slave Inverter Off",     # 12
                "Unknown",                # 13
                "Offsetting",             # 14
                "AGS Error",              # 15
                "Comm Error"]             # 16 
                
                operating_modes=operating_modes_list[gs_single_inverter_operating_mode]
                logging.info(".... FXR Inverter Operating Mode " + str(gs_single_inverter_operating_mode) +" "+ operating_modes)  
                
                response = client.read_holding_registers(reg + 31, 1)
                gs_single_ac_input_state = round(int(response.registers[0]),2)
                ac_use_list = [
                "AC Drop",
                "AC Use"
                ]
                ac_use = ac_use_list[gs_single_ac_input_state]
                logging.info(".... FXR AC USE (Y/N) " + str(gs_single_ac_input_state) + " " + ac_use)
                
                response = client.read_holding_registers(reg + 17, 1)
                gs_single_battery_voltage = round(int(response.registers[0]) * 0.1,1)
                logging.info(".... FXR Battery voltage (V) " + str(gs_single_battery_voltage))
                
                response = client.read_holding_registers(reg + 18, 1)
                gs_single_temp_compensated_target_voltage = round(int(response.registers[0]) * 0.1,2)
                logging.info(".... FXR Battery target voltage - temp compensated (V) " + str(gs_single_temp_compensated_target_voltage))

                response = client.read_holding_registers(reg + 19, 1)
                GS_Single_AUX_Relay_Output_State = int(response.registers[0])
                logging.info(".... FXR Aux Relay state  " + str(GS_Single_AUX_Relay_Output_State))
                aux_relay_list=["Aux:disabled","Aux:enabled"]
                aux_relay=aux_relay_list[GS_Single_AUX_Relay_Output_State]

                response = client.read_holding_registers(reg + 27, 1)
                gs_single_battery_temperature = decode_int16(int(response.registers[0]))
                logging.info(".... FXR Battery temperature (V) " + str(gs_single_battery_temperature))
               
                response = client.read_holding_registers(reg + 15, 1)
                GS_Split_Error_Flags = int(response.registers[0])
                logging.info(".... FXR Error Flags " + str(GS_Split_Error_Flags))
                error_flags='None'
                if GS_Split_Error_Flags == 1:   error_flags='Low AC output voltage'
                if GS_Split_Error_Flags == 2:   error_flags='Stacking error'               
                if GS_Split_Error_Flags == 4:   error_flags='Over temperature error'
                if GS_Split_Error_Flags == 8:   error_flags='Low battery voltage'               
                if GS_Split_Error_Flags == 16:  error_flags='Phase loss'                
                if GS_Split_Error_Flags == 32:  error_flags='High battery voltage'
                if GS_Split_Error_Flags == 64:  error_flags='AC output shorted'               
                if GS_Split_Error_Flags == 128: error_flags='AC backfeed'
                
                response = client.read_holding_registers(reg + 16, 1)
                GS_Single_Warning_Flags = int(response.registers[0])
                logging.info(".... FXR Warning Flags " + str(GS_Single_Warning_Flags))
                warning_flags='None'
                if GS_Single_Warning_Flags == 1:   warning_flags='AC input frequency too high'
                if GS_Single_Warning_Flags == 2:   warning_flags='AC input frequency too low'               
                if GS_Single_Warning_Flags == 4:   warning_flags='AC input voltage too low'
                if GS_Single_Warning_Flags == 8:   warning_flags='AC input voltage too high'               
                if GS_Single_Warning_Flags == 16:  warning_flags='AC input current exceeds max'                
                if GS_Single_Warning_Flags == 32:  warning_flags='Temperature sensor bad' 
                if GS_Single_Warning_Flags == 64:  warning_flags='Communications error'               
                if GS_Single_Warning_Flags == 128: warning_flags='Cooling fan fault'                

                # FXR data - JSON preparation
                devices_array={
                  "address": address,
                  "device_id": 5,
                  "inverter_current": gs_single_inverter_output_current,
                  "buy_current": gs_single_inverter_buy_current,
                  "charge_current": gs_single_inverter_charge_current,
                  "ac_input_voltage": gs_single_ac_input_voltage,
                  "ac_output_voltage": gs_single_output_ac_voltage,
                  "sell_current": GS_Single_Inverter_Sell_Current,
                  "operational_mode": operating_modes,
                  "error_modes": [
                    error_flags
                  ],
                  "ac_mode": ac_use,
                  "battery_voltage":gs_single_battery_voltage,
                  "misc":aux_relay,
                  "warning_modes": [
                    warning_flags
                  ],
                  "label":device_list[port]}
                devices.append(devices_array)     # append FXR data to devices
              
                # FXR data - MariaDB SQL preparation
                db_devices_values.append ((date_sql,address,5,gs_single_inverter_output_current,gs_single_inverter_charge_current,gs_single_inverter_buy_current,
                gs_single_ac_input_voltage,gs_single_output_ac_voltage,GS_Single_Inverter_Sell_Current,operating_modes,
                error_flags,ac_use,gs_single_battery_voltage,aux_relay,warning_flags))
                
                db_devices_sql.append ("INSERT INTO monitormate_fx \
                (date,address,device_id,inverter_current,charge_current,buy_current,ac_input_voltage,ac_output_voltage,\
                sell_current,operational_mode,error_modes,ac_mode,battery_voltage,misc,warning_modes) \
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")

                # FXR data - MQTT preparation   
                if device_list[port]=='VFXR3048_master' :                     # master invertor 
                    mqtt_devices.append(
                                {MQTT_master_ac_input_voltage        :gs_single_ac_input_voltage,
                                 MQTT_master_output_ac_voltage       :gs_single_output_ac_voltage,
                                 MQTT_master_ac_use         :ac_use,
                                 MQTT_master_operating_modes:operating_modes})
      
        except Exception as e:
            ErrorPrint("Error: RMS - port: " + str(port) + " FXR module " + str(e))
        
        try:
            if "Radian Inverter Configuration Block" in blockResult['DID']:
                response = client.read_holding_registers(reg + 26, 1)
                GSconfig_Grid_Input_Mode = int(response.registers[0])
                logging.info(".... FXR Grid input Mode " + str(GSconfig_Grid_Input_Mode))
                grid_input_mode='None'
                if GSconfig_Grid_Input_Mode == 0:   grid_input_mode ='Generator'
                if GSconfig_Grid_Input_Mode == 1:   grid_input_mode ='Support'               
                if GSconfig_Grid_Input_Mode == 2:   grid_input_mode ='GridTied'
                if GSconfig_Grid_Input_Mode == 3:   grid_input_mode ='UPS'               
                if GSconfig_Grid_Input_Mode == 4:   grid_input_mode ='Backup'                
                if GSconfig_Grid_Input_Mode == 5:   grid_input_mode ='MiniGrid' 
                if GSconfig_Grid_Input_Mode == 6:   grid_input_mode ='GridZero'
                
                response = client.read_holding_registers(reg + 24, 1)
                GSconfig_Charger_Operating_Mode = int(response.registers[0])
                logging.info(".... FXR Charger Mode " + str(GSconfig_Charger_Operating_Mode))
                charger_mode='None'
                if GSconfig_Charger_Operating_Mode == 0:   charger_mode ='Off'
                if GSconfig_Charger_Operating_Mode == 1:   charger_mode ='On'

                # FXR dataconfig - JSON preparation
                various_array={
                  "address": address,
                  "device_id": 5,
                  "grid_input_mode": grid_input_mode,
                  "charger_mode": charger_mode
                  }
                various.append(various_array)     # append FXR data to devices
                devices[address-1]["grid_input_mode"] = grid_input_mode
                devices[address-1]["charger_mode"] = charger_mode
                
                # FXR dataconfig - Mqtt preparation
                if device_list[port]=='VFXR3048_master':
                    mqtt_devices.append(
                        {MQTT_master_grid_input_mode:grid_input_mode,
                         MQTT_master_charger_mode:charger_mode})
     
        except Exception as e:
            ErrorPrint("Error: RMS - port: " + str(port) + " FXR config block " + str(e))

        try:
            if "Charge Controller Block" in blockResult['DID']:
                logging.info(".. Detected a Charge Controller Block")

                response = client.read_holding_registers(reg + 2, 1)
                logging.info(".... Connected on HUB port " + str(response.registers[0]))
                port=(response.registers[0]-1)
                address=port+1
     
                response = client.read_holding_registers(reg + 10, 1)
                cc_batt_current = round(int(response.registers[0]) * 0.1,2)    # correction value *0.1
                logging.info(".... CC Battery Current (A) " + str(cc_batt_current))
     
                response = client.read_holding_registers(reg + 11, 1)
                cc_array_current = round(int(response.registers[0]),2)
                logging.info(".... CC Array Current (A) " + str(cc_array_current))
                
                response = client.read_holding_registers(reg + 9, 1)
                cc_array_voltage = round(int(response.registers[0]) * 0.1,2)
                logging.info(".... CC Array Voltage " + str(cc_array_voltage))
                
                response = client.read_holding_registers(reg + 18, 1)
                CC_Todays_KW = round(int(response.registers[0]) * 0.1,2)
                logging.info(".... CC Daily_KW (KW) " + str(CC_Todays_KW))
                CC_total_daily_kwh = CC_total_daily_kwh + CC_Todays_KW
                
                response = client.read_holding_registers(reg + 13, 1)
                CC_Watts = round(int(response.registers[0]),2)
                logging.info(".... CC Actual_watts (W) " + str(CC_Watts))
                CC_total_watts = CC_total_watts + CC_Watts

                response = client.read_holding_registers(reg + 12, 1)
                cc_charger_state = round(int(response.registers[0]),2)
                logging.info(".... CC Charger State " + str(cc_charger_state))  # 0=Silent,1=Float,2=Bulk,3=Absorb,4=EQ
                cc_mode_list=["Silent","Float","Bulk","Absorb","Equalize"]
                cc_mode=cc_mode_list[cc_charger_state]
     
                response = client.read_holding_registers(reg + 8, 1)
                cc_batt_voltage = round(int(response.registers[0]) * 0.1,2)
                logging.info(".... CC Battery Voltage (V) " + str(cc_batt_voltage))
     
                response = client.read_holding_registers(reg + 19, 1)
                CC_Todays_AH = round(int(response.registers[0]),2)
                logging.info(".... CC Daily_AH (A) " + str(CC_Todays_AH))
                
            if "Charge Controller Configuration block" in blockResult['DID']:           #some CC parameters are in configuration block
                logging.info(".. Charge Controller Configuration block")
                response = client.read_holding_registers(reg + 2, 1)
                logging.info(".... Connected on HUB port " + str(response.registers[0]))
                port=(response.registers[0]-1)
                
                response = client.read_holding_registers(reg + 32, 1)
                CCconfig_AUX_Mode   = int(response.registers[0])
                logging.info(".... CC Aux Mode " + str(CCconfig_AUX_Mode))
              
                aux_mode_list=["Float","Diversion: Relay","Diversion:SSR","Low Batt Disconnect",
                          "Remote","Vent Fan","PV Trigger","Error Output","Night Light"]                                         # 0 disabled, 1 enabled 
                aux_mode=aux_mode_list[CCconfig_AUX_Mode ]
                
                response = client.read_holding_registers(reg + 34, 1)
                CCconfig_AUX_State  = int(response.registers[0])
                logging.info(".... CC Aux State " + str(CCconfig_AUX_State))
                aux_state_list=[
                "Disabled",     # 0 - disabled
                "Enabled"       # 1 - Enabled
                ]                                         
                aux_state=aux_state_list[CCconfig_AUX_State]
                
                response = client.read_holding_registers(reg + 9, 1)
                CCconfig_Faults = int(response.registers[0])
                logging.info(".... CC Error Flags " + str(CCconfig_Faults))
                error_flags='None'            
                if CCconfig_Faults == 16:  error_flags='Fault Input Active'                
                if CCconfig_Faults == 32:  error_flags='Shorted Battery Temp Sensor'
                if CCconfig_Faults == 64:  error_flags='Over Temp'               
                if CCconfig_Faults == 128: error_flags='High VOC'

                # Controlers data - JSON preparation
                devices_array= {
                  "address": address,
                  "device_id":3,
                  "charge_current": cc_batt_current,
                  "pv_current": cc_array_current,
                  "pv_voltage": cc_array_voltage,
                  "pv_watts": CC_Watts,
                  "aux": aux_mode,
                  "aux_mode": aux_state,
                  "error_modes": [
                    error_flags
                  ],
                  "charge_mode": cc_mode,
                  "battery_voltage": cc_batt_voltage,
                  "daily_ah": CC_Todays_AH,
                  "daily_kwh": CC_Todays_KW,
                  "label": device_list[port]
                    }
                devices.append(devices_array)

                # Controlers data - MariaDB SQL preparation
                db_devices_values.append((date_sql,address,3,cc_batt_current,cc_array_current,cc_array_voltage,CC_Todays_KW,aux_mode,aux_state,error_flags,cc_mode,cc_batt_voltage,CC_Todays_AH))
                db_devices_sql.append ("INSERT INTO monitormate_cc \
                (date,address,device_id,charge_current,pv_current,pv_voltage,daily_kwh,aux_mode,aux,error_modes,charge_mode,battery_voltage,daily_ah) \
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
                
                #controlers data - MQTT data preparation
                if device_list[port]=='FM80':
                    mqtt_devices.append({MQTT_controller_1_cc_mode:cc_mode})                 
        
        except Exception as e:
            ErrorPrint("Error: RMS - port: " + str(port) + " CC module " + str(e))

        try:
            if "FLEXnet-DC Real Time Block" in blockResult['DID']:
                logging.info(".. Detect a FLEXnet-DC Real Time Block")

                response = client.read_holding_registers(reg + 2, 1)
                logging.info(".... Connected on HUB port " + str(response.registers[0]))
                port=(response.registers[0]-1)
                address=port+1

                response = client.read_holding_registers(reg + 8, 1)
                fn_shunt_a_current = round(decode_int16(int(response.registers[0])) * 0.1,2)
                logging.info(".... FN Shunt A Current (A) " + str(fn_shunt_a_current))
                
                response = client.read_holding_registers(reg + 9, 1)
                fn_shunt_b_current = round(decode_int16(response.registers[0]) * 0.1,2)
                logging.info(".... FN Shunt B Current (A) " + str(fn_shunt_b_current))
               
                response = client.read_holding_registers(reg + 10, 1)
                fn_shunt_c_current = round(decode_int16(int(response.registers[0])) * 0.1,2)
                logging.info(".... FN Shunt C Current (A) " + str(fn_shunt_c_current))

                response = client.read_holding_registers(reg + 11, 1)
                fn_battery_voltage = round(int(response.registers[0]) * 0.1,2)
                logging.info(".... FN Battery Voltage " + str(fn_battery_voltage))

                response = client.read_holding_registers(reg + 27, 1)
                fn_state_of_charge = int(response.registers[0])
                logging.info(".... FN State of Charge " + str(fn_state_of_charge))
                
                response = client.read_holding_registers(reg + 14, 1)
                FN_Status_Flags  = int(response.registers[0])
                logging.info(".... FN Status Flag " + str(FN_Status_Flags))
                charge_params_met="false"
                if FN_Status_Flags==2 or FN_Status_Flags==6 or FN_Status_Flags==7:
                    charge_params_met="true"              
                logging.info(".... FN Charge Parameters Met " + str(FN_Status_Flags ) + " " + charge_params_met)
                relay_status="disabled"
                if FN_Status_Flags==1 or FN_Status_Flags==3 or FN_Status_Flags==5 or FN_Status_Flags==7:
                    relay_status="enabled"              
                logging.info(".... FN Relay Status " + str(FN_Status_Flags ) + " " + relay_status)
                relay_mode="auto"
                if FN_Status_Flags==4 or FN_Status_Flags==5 or FN_Status_Flags==7:
                    relay_mode="manual"              
                logging.info(".... FN Relay Mode " + str(FN_Status_Flags ) + " " + relay_mode)

                response = client.read_holding_registers(reg + 13, 1)
                fn_battery_temperature = decode_int16(int(response.registers[0]))
                logging.info(".... FN Battery Temperature " + str(fn_battery_temperature))

                response = client.read_holding_registers(reg + 15, 1)
                FN_Shunt_A_Accumulated_AH = round(decode_int16(int(response.registers[0])),2)
                logging.info(".... FN FN_Shunt_A_Accumulated_AH " + str(FN_Shunt_A_Accumulated_AH))

                response = client.read_holding_registers(reg + 16, 1)
                FN_Shunt_A_Accumulated_kWh = round(decode_int16(int(response.registers[0])) * 0.01,2)
                logging.info(".... FN FN_Shunt_A_Accumulated_kWh " + str(FN_Shunt_A_Accumulated_kWh))

                response = client.read_holding_registers(reg + 17, 1)
                FN_Shunt_B_Accumulated_AH = round(decode_int16(int(response.registers[0])),2)
                logging.info(".... FN FN_Shunt_B_Accumulated_AH " + str(FN_Shunt_B_Accumulated_AH))

                response = client.read_holding_registers(reg + 18, 1)
                FN_Shunt_B_Accumulated_kWh = round(decode_int16(int(response.registers[0])) * 0.01,2)
                logging.info(".... FN FN_Shunt_B_Accumulated_kWh " + str(FN_Shunt_B_Accumulated_kWh))
                     
                response = client.read_holding_registers(reg + 19, 1)
                FN_Shunt_C_Accumulated_AH = round(decode_int16(int(response.registers[0])),2)
                logging.info(".... FN FN_Shunt_C_Accumulated_AH " + str(FN_Shunt_C_Accumulated_AH))

                response = client.read_holding_registers(reg + 20, 1)
                FN_Shunt_C_Accumulated_kWh = round(decode_int16(int(response.registers[0])) * 0.01,2)
                logging.info(".... FN FN_Shunt_C_Accumulated_kWh " + str(FN_Shunt_C_Accumulated_kWh))
                
                response = client.read_holding_registers(reg + 26, 1)
                FN_Days_Since_Charge_Parameters_Met = round(int((response.registers[0])) * 0.1,2)
                logging.info(".... FN days_since_full " + str(FN_Days_Since_Charge_Parameters_Met))
                
                response = client.read_holding_registers(reg + 28, 1)
                FN_Todays_Minimum_SOC = int(response.registers[0])
                logging.info(".... FN Todays_Minimum_SOC " + str(FN_Todays_Minimum_SOC))

                response = client.read_holding_registers(reg + 29, 1)
                FN_Todays_Maximum_SOC = int(response.registers[0])
                logging.info(".... FN Todays_Maximum_SOC " + str(FN_Todays_Maximum_SOC))
                
                response = client.read_holding_registers(reg + 30, 1)
                FN_Todays_NET_Input_AH = round(int(response.registers[0]),2)
                logging.info(".... FN Todays_NET_Input_AH " + str(FN_Todays_NET_Input_AH))

                response = client.read_holding_registers(reg + 31, 1)
                FN_Todays_NET_Input_kWh = round(int(response.registers[0]) * 0.01,2)
                logging.info(".... FN Todays_NET_Input_kWh " + str(FN_Todays_NET_Input_kWh))
                
                response = client.read_holding_registers(reg + 32, 1)
                FN_Todays_NET_Output_AH = round(int(response.registers[0]),2)
                logging.info(".... FN Todays_NET_Output_AH " + str(FN_Todays_NET_Output_AH))
                
                response = client.read_holding_registers(reg + 33, 1)
                FN_Todays_NET_Output_kWh = round(int(response.registers[0]) * 0.01,2)
                logging.info(".... FN Todays_NET_Output_kWh " + str(FN_Todays_NET_Output_kWh))

                response = client.read_holding_registers(reg + 36, 1)
                FN_Charge_Factor_Corrected_NET_Battery_AH = round(decode_int16(int(response.registers[0])),2)
                logging.info(".... FN Charge_Factor_Corrected_NET_Battery_AH " + str(FN_Charge_Factor_Corrected_NET_Battery_AH))
                
                response = client.read_holding_registers(reg + 37, 1)
                FN_Charge_Factor_Corrected_NET_Battery_kWh = round(decode_int16(int(response.registers[0])) * 0.01,2)
                logging.info(".... FN_Charge_Factor_Corrected_NET_Battery_kWh " + str(FN_Charge_Factor_Corrected_NET_Battery_kWh))
                
                response = client.read_holding_registers(reg + 26, 1)
                FN_Days_Since_Charge_Parameters_Met = round(int(response.registers[0]) * 0.1,2)
                logging.info(".... FN_Days_Since_Charge_Parameters_Met " + str(FN_Days_Since_Charge_Parameters_Met))
                
            if "FLEXnet-DC Configuration Block" in blockResult['DID']:
                logging.info(".. Detect a FLEXnet-DC Configuration Block")

                response = client.read_holding_registers(reg + 2, 1)
                logging.info(".... Connected on HUB port " + str(response.registers[0]))
                port=(response.registers[0]-1)
                
                response = client.read_holding_registers(reg + 14, 1)
                FNconfig_Shunt_A_Enabled = int(response.registers[0])
                Shunt_A_Enabled_list=("ON","OFF")
                Shunt_A_Enabled=Shunt_A_Enabled_list[FNconfig_Shunt_A_Enabled]
                logging.info(".... FN Shunt_A_Enabled " + Shunt_A_Enabled)
                
                response = client.read_holding_registers(reg + 15, 1)
                FNconfig_Shunt_B_Enabled = int(response.registers[0])
                Shunt_B_Enabled_list=("ON","OFF")
                Shunt_B_Enabled=Shunt_B_Enabled_list[FNconfig_Shunt_B_Enabled]
                logging.info(".... FN Shunt_B_Enabled " + Shunt_B_Enabled)
                
                response = client.read_holding_registers(reg + 16, 1)
                FNconfig_Shunt_C_Enabled = int(response.registers[0])
                Shunt_C_Enabled_list=("ON","OFF")
                Shunt_C_Enabled=Shunt_C_Enabled_list[FNconfig_Shunt_C_Enabled]
                logging.info(".... FN Shunt_C_Enabled " + Shunt_C_Enabled)
                
                # FNDC data - JSON preparation
                devices_array= {
                  "address": address,
                  "device_id": 4,
                  "shunt_a_current": fn_shunt_a_current,
                  "shunt_b_current": fn_shunt_b_current,
                  "shunt_c_current": fn_shunt_c_current,
                  "battery_voltage": fn_battery_voltage,
                  "soc": fn_state_of_charge,
                  "shunt_enabled_a": Shunt_A_Enabled,
                  "shunt_enabled_b": Shunt_B_Enabled,
                  "shunt_enabled_c": Shunt_C_Enabled,
                  "charge_params_met": charge_params_met,
                  "relay_status": relay_status,
                  "relay_mode": relay_mode,
                  "battery_temp": fn_battery_temperature,
                  "accumulated_ah_shunt_a": FN_Shunt_A_Accumulated_AH,
                  "accumulated_kwh_shunt_a": FN_Shunt_A_Accumulated_kWh,
                  "accumulated_ah_shunt_b": FN_Shunt_B_Accumulated_AH,
                  "accumulated_kwh_shunt_b": FN_Shunt_B_Accumulated_kWh,
                  "accumulated_ah_shunt_c": FN_Shunt_C_Accumulated_AH,
                  "accumulated_kwh_shunt_c": FN_Shunt_C_Accumulated_kWh,
                  "days_since_full": FN_Days_Since_Charge_Parameters_Met,
                  "today_min_soc": FN_Todays_Minimum_SOC,
                  "today_net_input_ah": FN_Todays_NET_Input_AH,
                  "today_net_output_ah": FN_Todays_NET_Output_AH,
                  "today_net_input_kwh": FN_Todays_NET_Input_kWh,
                  "today_net_output_kwh": FN_Todays_NET_Output_kWh,
                  "charge_factor_corrected_net_batt_ah": FN_Charge_Factor_Corrected_NET_Battery_AH,
                  "charge_factor_corrected_net_batt_kwh": FN_Charge_Factor_Corrected_NET_Battery_kWh,
                  "label": device_list[port],
                  "shunt_a_label": shunt_list[0],
                  "shunt_b_label": shunt_list[1],
                  "shunt_c_label": shunt_list[2]
                }            
                devices.append(devices_array)
                                
                # FNDC data - MariaDB SQL preparation
                db_devices_values.append ((
                date_sql,
                address,
                4,
                fn_shunt_a_current,
                fn_shunt_b_current,
                fn_shunt_c_current,
                FN_Shunt_A_Accumulated_AH,
                FN_Shunt_A_Accumulated_kWh,
                FN_Shunt_B_Accumulated_AH,
                FN_Shunt_B_Accumulated_kWh,
                FN_Shunt_C_Accumulated_AH,
                FN_Shunt_C_Accumulated_kWh,
                FN_Days_Since_Charge_Parameters_Met,
                FN_Todays_Minimum_SOC,
                FN_Todays_NET_Input_AH,
                FN_Todays_NET_Output_AH,
                FN_Todays_NET_Input_kWh,
                FN_Todays_NET_Output_kWh,
                FN_Charge_Factor_Corrected_NET_Battery_AH,
                FN_Charge_Factor_Corrected_NET_Battery_kWh,
                charge_params_met,
                relay_mode,
                relay_status,
                fn_battery_voltage,
                fn_state_of_charge,
                Shunt_A_Enabled,
                Shunt_B_Enabled,
                Shunt_C_Enabled,
                fn_battery_temperature))
                
                db_devices_sql.append ("INSERT INTO monitormate_fndc (\
                date,\
                address,\
                device_id,\
                shunt_a_current,\
                shunt_b_current,\
                shunt_c_current,\
                accumulated_ah_shunt_a,\
                accumulated_kwh_shunt_a,\
                accumulated_ah_shunt_b,\
                accumulated_kwh_shunt_b,\
                accumulated_ah_shunt_c,\
                accumulated_kwh_shunt_c,\
                days_since_full,\
                today_min_soc,\
                today_net_input_ah,\
                today_net_output_ah,\
                today_net_input_kwh,\
                today_net_output_kwh,\
                charge_factor_corrected_net_batt_ah,\
                charge_factor_corrected_net_batt_kwh,\
                charge_params_met,\
                relay_mode,\
                relay_status,\
                battery_voltage,\
                soc,\
                shunt_enabled_a,\
                shunt_enabled_b,\
                shunt_enabled_c,\
                battery_temp) \
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
                    
                # FNDC data - MQTT data preparation topic:value
                mqtt_devices.append (
                    {MQTT_fndc_battery_voltage:fn_battery_voltage,
                     MQTT_fndc_state_of_charge:fn_state_of_charge,
                     MQTT_fndc_battery_temperature:fn_battery_temperature,
                     MQTT_fndc_shunt_a_current:fn_shunt_a_current,
                     MQTT_fndc_shunt_c_current:fn_shunt_c_current,
                     MQTT_fndc_shunt_b_current:fn_shunt_b_current,
                     MQTT_fndc_charge_params_met:charge_params_met,
                     MQTT_fndc_days_since_charge_met:FN_Days_Since_Charge_Parameters_Met,
                     MQTT_fndc_todays_net_input_kWh:FN_Todays_NET_Input_kWh,
                     MQTT_fndc_todays_net_output_kWh:FN_Todays_NET_Output_kWh})

        except Exception as e:
            ErrorPrint("Error: RMS - port: " + str(port) + " FNDC module " + str(e))

        if "End of SunSpec" not in blockResult['DID']:
            reg = reg + blockResult['size'] + 2
        else:
            client.close()
            mate_run = datetime.now()                                             #DPO debug
            running_time = round ((mate_run - start_run).total_seconds(),3)       #DPO debug
            logging.info(" Mate connection closed")
            print("running time Mate:       ",format(running_time,".3f")," sec")  #DPO debug
  
            break
  
    # MariaDB upload
    min_bat_temp = None 
    max_bat_temp = None
    max_pv_voltage = None    
    try:
        if SQL_active=='true':
            
            date_now=now.strftime("%Y-%m-%d") #current date
            mydb = mariadb.connect(host=host,port=db_port,user=user,password=password,database=database)
            
            # devices data - MariaDB upload
            n=0
            for value in db_devices_values:
                mycursor = mydb.cursor()
                mycursor.execute(db_devices_sql[n], value)
                n = n+1
            
            # summary of the day calculation for MariaDB upload
            sql="SELECT min(battery_temp) from monitormate_fndc where date(date)= DATE(NOW())" #calculate min temp of the day
            mycursor = mydb.cursor()
            mycursor.execute(sql)
            myresult = mycursor.fetchall()
            min_bat_temp = myresult[0]
            
            for x in myresult:
                min_bat_temp=x[0]
            
            sql="SELECT max(battery_temp) from monitormate_fndc where date(date)= DATE(NOW())" #calculate max temp of the day
            mycursor = mydb.cursor()
            mycursor.execute(sql)
            myresult = mycursor.fetchall()
            max_bat_temp = myresult[0]
            
            for x in myresult:
                max_bat_temp=x[0]
            
            sql="SELECT max(pv_voltage) from monitormate_cc where date(date)= DATE(NOW())" #calculate max pv voltage of the day
            
            mycursor = mydb.cursor()
            mycursor.execute(sql)
            myresult = mycursor.fetchall()
            max_pv_voltage = myresult[0]            
            
            for x in myresult:
                max_pv_voltage=int(x[0])
                
            sql="SELECT min(charge_factor_corrected_net_batt_ah), min(charge_factor_corrected_net_batt_kwh) FROM monitormate_fndc WHERE date(date) = DATE(NOW())"    

            mycursor = mydb.cursor()
            mycursor.execute(sql)
            myresult = mycursor.fetchall()
            
            for x in myresult:
                out_batt_ah  = 0
                out_batt_kwh = 0
                if x[0] is not None: out_batt_ah  = x[0]    
                if x[1] is not None: out_batt_kwh = x[1]            
            
            sql="SELECT date,kwh_in,kwh_out,ah_in,max_temp,min_temp,max_soc,min_soc,max_pv_voltage FROM monitormate_summary \
            where date(date)= DATE(NOW())"
            
            mycursor = mydb.cursor()
            mycursor.execute(sql)
            myresult = mycursor.fetchall()
            
            if not myresult:                                                               # check if any records for today - if not, record for the first time
                val=(date_now,FN_Todays_NET_Input_kWh,FN_Todays_NET_Output_kWh,FN_Todays_NET_Input_AH,FN_Todays_NET_Output_AH,out_batt_ah,out_batt_kwh,
                     max_bat_temp,min_bat_temp,FN_Todays_Maximum_SOC,FN_Todays_Minimum_SOC,max_pv_voltage)
                sql="INSERT INTO monitormate_summary (date,kwh_in,kwh_out,ah_in,ah_out,out_batt_ah,out_batt_kwh,max_temp,min_temp,max_soc,min_soc,max_pv_voltage)\
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                mycursor = mydb.cursor()
                mycursor.execute(sql, val)
                mydb.commit()
                logging.info(" Summary of the day - first record completed")
            else:                                                                           # if records - update table
                val=(FN_Todays_NET_Input_kWh,FN_Todays_NET_Output_kWh,FN_Todays_NET_Input_AH,FN_Todays_NET_Output_AH,out_batt_ah,out_batt_kwh,
                     max_bat_temp,min_bat_temp,FN_Todays_Maximum_SOC,FN_Todays_Minimum_SOC,max_pv_voltage,date_now)
                sql="UPDATE monitormate_summary SET kwh_in=%s,kwh_out=%s,ah_in=%s,ah_out=%s,out_batt_ah=%s,out_batt_kwh=%s,max_temp=%s,min_temp=%s,\
                max_soc=%s,min_soc=%s,max_pv_voltage=%s WHERE date=%s"
                mycursor = mydb.cursor()
                mycursor.execute(sql, val)
                mydb.commit()
                
            mycursor.close()
            mydb.close()
            mariadb_run = datetime.now()                                             #DPO debug
            running_time = round ((mariadb_run - mate_run).total_seconds(),3)        #DPO debug
            print("running time MariaDB:    ",format(running_time,".3f")," sec")     #DPO debug 

    except Exception as e:
        mycursor.close()
        mydb.close()
        mariadb_run = datetime.now()
        ErrorPrint("Error: RMS - MariaDB upload - "+ str(e))

    # Summary data - JSON preparation
    date_now=now.strftime("%Y-%m-%d") #current date
    summary={
        "date": date_now,
        "kwh_in": FN_Todays_NET_Input_kWh,
        "kwh_out": FN_Todays_NET_Output_kWh,
        "ah_in": FN_Todays_NET_Input_AH,
        "ah_out": FN_Todays_NET_Output_AH,
        "max_temp": max_bat_temp,
        "min_temp": min_bat_temp,
        "min_soc": FN_Todays_Minimum_SOC,
        "max_soc": FN_Todays_Maximum_SOC,
        "max_pv_voltage": max_pv_voltage,
        "pv_watts": CC_total_watts,
        "pv_daily_Kwh":CC_total_daily_kwh}

    # summary values - send data via MQTT 
    mqtt_devices.append ({MQTT_summary_cc_total_watts:CC_total_watts})
   
    #JSON serialisation and save
    try:
        json_data={"time":time, "devices":devices, "summary":summary, "various":various}
        with open(output_path + '/data/status.json', 'w') as outfile:
            json.dump(json_data, outfile)
        if duplicate_active == 'true':
            shutil.copy(output_path + '/data/status.json', duplicate_path + '/data/status.json')#copy the file in second location
        json_run = datetime.now()                                                           #DPO debug
        running_time = round ((json_run - mariadb_run).total_seconds(),3)                   #DPO debug
        print("running time JSON:       ",format(running_time,".3f")," sec")                #DPO debug 

    except Exception as e:
        ErrorPrint("Error: RMS - JSON w/r - " + str(e))
        json_run = datetime.now()
        
    # MQTT send data to MQTT broker
    try:
        if MQTT_active=='true':
            for mqtt_data in mqtt_devices:
                for topic in mqtt_data:
                    publish.single(topic, mqtt_data[topic], hostname=MQTT_broker)
                    #print(topic + ": " + str(mqtt_data[topic]))                    #DPO debug
                    
        ## send overall json data via MQTT                                                  
        state_topic = MQTT_all_data                                    
        message     = json.dumps(json_data)                                         
        publish.single(state_topic, message, hostname=MQTT_broker)                    

        mqtt_run = datetime.now()                                                   #DPO debug
        running_time = round ((mqtt_run - json_run).total_seconds(),3)              #DPO debug
        print("running time MQTT:       ",format(running_time,".3f"), " sec")       #DPO debug 
      
    except Exception as e:
        ErrorPrint("Error: RMS - MQTT module - "+ str(e))

    #time.sleep(30) # DPO - used if continue loop is used
    break           # DPO - remark it if continuous loop needed

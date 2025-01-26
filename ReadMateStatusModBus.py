import json
import time
import logging
from logging.handlers import RotatingFileHandler
import mysql.connector as mariadb
from datetime import datetime
from pymodbus.client import ModbusTcpClient as ModbusClient
from pymodbus.constants import Endian
from configparser import ConfigParser
import paho.mqtt.publish as publish
import shutil  
import sys, os

script_ver = "0.10.0_20250126"
print ("script version: "+ script_ver)

pathname               = os.path.dirname(sys.argv[0])
working_dir            = os.path.abspath(pathname) 

config                 = ConfigParser()
config.read(os.path.join(working_dir, 'config.cfg'))

#MATE3 connection
mate3_ip               = config.get('MATE3 connection', 'mate3_ip')
mate3_modbus           = config.get('MATE3 connection', 'mate3_modbus')
sunspec_start_reg      = 40000

# SQL Maria DB connection
SQL_active             = config.get('Maria DB connection', 'SQL_active')                             
host                   = config.get('Maria DB connection', 'host')
db_port                = config.get('Maria DB connection', 'db_port')
user                   = config.get('Maria DB connection', 'user')
password               = config.get('Maria DB connection', 'password')
database               = config.get('Maria DB connection', 'database')
output_path            = config.get('Path', 'output_path')
duplicate_active       = config.get('Path', 'duplicate_active')
duplicate_path         = config.get('Path', 'duplicate_path')

# merge paths to use proper separators windows or Linux
if output_path == "":
    output_path = os.path.join(working_dir, 'data')

# MQTT 
MQTT_active            = config.get('MQTT', 'MQTT_active')
MQTT_broker            = config.get('MQTT', 'MQTT_broker')
LOGGING_LEVEL_FILE     = config.get('General','LOGGING_LEVEL_FILE')
LOGGING_FILE_MAX_SIZE  = int(config.get('General','LOGGING_FILE_MAX_SIZE'))
LOGGING_FILE_MAX_FILES = int(config.get('General','LOGGING_FILE_MAX_FILES'))

print("working directory:       ", working_dir)
print("output location:         ", output_path)
print("duplicate output active: ", duplicate_active)
print("SQL active  :            ", SQL_active)
print("MQTT active :            ", MQTT_active)

## LOGGER setup
# Creează un logger
logger = logging.getLogger("outback")
logger.setLevel(LOGGING_LEVEL_FILE)  # Setează nivelul minim de logare

# Handler pentru consolă
console_handler = logging.StreamHandler()
console_handler.setLevel(LOGGING_LEVEL_FILE)
#formater
console_formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s', datefmt='%Y%m%d %H:%M:%S')
console_handler.setFormatter(console_formatter)

# Handler pentru fișier
# merge paths to use proper separators windows or Linux
log_path = os.path.join(working_dir, 'data', 'events_rms.log')
file_handler = RotatingFileHandler(log_path , mode='a', maxBytes=LOGGING_FILE_MAX_SIZE*1000, backupCount=LOGGING_FILE_MAX_FILES, encoding=None, delay=False)
file_handler.setLevel(LOGGING_LEVEL_FILE)

#formater
file_formatter = logging.Formatter('%(asctime)s| RMS |%(levelname)8s| %(message)s ',datefmt='%Y%m%d %H:%M:%S') 
file_handler.setFormatter(file_formatter)

# Adaugă handler-ele la logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

curent_date_time = datetime.now()
date_str         = curent_date_time.strftime("%Y-%m-%dT%H:%M:%S")
date_sql         = datetime.now().replace(second=0, microsecond=0)

device_list=[          #used in main loop      
    "VFXR3048_01",     #port 1   used for MQTT data
    "FM60",            #port 2
    "FM80",            #port 3
    "VFXR3048_02",     #port 4
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

# Decoder Class to replace BinaryPayloadDecoder that will be removed in pymodbus 3.9.0
class SunSpecDecoder:
    def __init__(self, registers):
        self.registers = registers
        self.offset = 0
        
    def decode_16bit_uint(self):
        value = self.registers[self.offset]
        self.offset += 1
        return value
    
    def decode_32bit_uint(self):
        value = (self.registers[self.offset] << 16) + self.registers[self.offset + 1]
        self.offset += 2
        return value
    
    def decode_string(self, size):
        string_data = ''.join([chr((self.registers[i] >> 8) & 0xFF) + chr(self.registers[i] & 0xFF) for i in range(self.offset, self.offset + (size // 2))])
        self.offset += size // 2
        return string_data.strip()
    
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

def get_common_block(basereg):
    """ Read and return the sunspec common information
    block.
    :returns: A dictionary of the common block information
    """
    length   = 69
    response = client.read_holding_registers(basereg, count=(length + 2))
    decoder = SunSpecDecoder(response.registers)
    
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
        response = client.read_holding_registers(basereg, count=2)
    except:
        return None

    if response.registers[0] == 21365 and response.registers[1] == 28243:
        logger.debug(".. SunSpec device found. Reading Manufacturer info")
    else:
        return None
    # There is a 16 bit string at basereg + 4 that contains Manufacturer
    response = client.read_holding_registers(basereg + 4, count=16)
    decoder = SunSpecDecoder(response.registers)
    manufacturer = decoder.decode_string(16)
    
    if "OUTBACK_POWER" in str(manufacturer.upper()):
        logger.debug(".. Outback Power device found")
    else:
        logger.debug(".. Not an Outback Power device. Detected " + manufacturer)
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

#------------------------------------------------
#  Start MATE3 ModBus Interface
#------------------------------------------------
# Try to build the mate3 MODBUS connection
logger.debug("Building MATE3 MODBUS connection")
start_run  = datetime.now() # used only for runtime calculation

# Mate3 connection
try:
    client = ModbusClient(mate3_ip, port=mate3_modbus)
    client.connect()

    logger.debug(".. Make sure we are indeed connected to an Outback power system")
    reg    = sunspec_start_reg
    size   = getSunSpec(reg)
    if size is None:
        logger.debug("We have failed to detect an Outback system. Exciting")
        exit()
except Exception as e:
    client.close()
    logger.error(".. Failed to connect to MATE3. Enable SUNSPEC and check port. Exciting")
    exit()
logger.debug(".. Connected OK to an Outback system")

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

    inverters=0                                    # used to count number of inverters detected
    chargers =0                                    # used to count number of chargers detected
    reg = startReg
    for block in range(0, 30):
        blockResult = getBlock(reg)
        
        try:        
            if "Single Phase Radian Inverter Real Time Block" in blockResult['DID']:
                logger.debug(".. Detected a Single Phase Radian Inverter Real Time Block" + " -Registry:"+str(reg))
                inverters = inverters + 1
                response = client.read_holding_registers(reg + 2, count=1)
                port=(response.registers[0]-1)
                address=port+1
                logger.debug(".... Connected on HUB port " + str(response.registers[0]))
       
                # Inverter Output current
                response = client.read_holding_registers(reg + 7, count=1)
                gs_single_inverter_output_current = round(response.registers[0],2)
                logger.debug(".... FXR Inverted output current (A) " + str(gs_single_inverter_output_current))
               
                response = client.read_holding_registers(reg + 8, count=1)
                gs_single_inverter_charge_current = round(response.registers[0],2)
                logger.debug(".... FXR Charger current (A) " + str(gs_single_inverter_charge_current))
                
                response = client.read_holding_registers(reg + 9, count=1)
                gs_single_inverter_buy_current = round(response.registers[0],2)
                logger.debug(".... FXR Input current (A) " + str(gs_single_inverter_buy_current))
                
                response = client.read_holding_registers(reg + 30, count=1)
                gs_single_ac_input_voltage = round(response.registers[0],2)
                logger.debug(".... FXR AC Input Voltage " + str(gs_single_ac_input_voltage))

                response = client.read_holding_registers(reg + 13, count=1)
                gs_single_output_ac_voltage = round(response.registers[0],2)
                logger.debug(".... FXR Voltage Out (V) " + str(gs_single_output_ac_voltage))
                
                response = client.read_holding_registers(reg + 10, count=1)
                GS_Single_Inverter_Sell_Current = round(response.registers[0],2)
                logger.debug(".... FXR Sell current (A) " + str(GS_Single_Inverter_Sell_Current))
               
                response = client.read_holding_registers(reg + 14, count=1)
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
                logger.debug(".... FXR Inverter Operating Mode " + str(gs_single_inverter_operating_mode) +" "+ operating_modes)  
                
                response = client.read_holding_registers(reg + 31, count=1)
                gs_single_ac_input_state = round(int(response.registers[0]),2)
                ac_use_list = [
                "AC Drop",
                "AC Use"
                ]
                ac_use = ac_use_list[gs_single_ac_input_state]
                logger.debug(".... FXR AC USE (Y/N) " + str(gs_single_ac_input_state) + " " + ac_use)
                
                response = client.read_holding_registers(reg + 17, count=1)
                gs_single_battery_voltage = round(int(response.registers[0]) * 0.1,1)
                logger.debug(".... FXR Battery voltage (V) " + str(gs_single_battery_voltage))
                
                response = client.read_holding_registers(reg + 18, count=1)
                gs_single_temp_compensated_target_voltage = round(int(response.registers[0]) * 0.1,2)
                logger.debug(".... FXR Battery target voltage - temp compensated (V) " + str(gs_single_temp_compensated_target_voltage))

                response = client.read_holding_registers(reg + 19, count=1)
                GS_Single_AUX_Relay_Output_State = int(response.registers[0])
                logger.debug(".... FXR Aux Relay state  " + str(GS_Single_AUX_Relay_Output_State))
                aux_relay_list=["disabled","enabled"]
                aux_relay=aux_relay_list[GS_Single_AUX_Relay_Output_State]

                response = client.read_holding_registers(reg + 21, count=1)
                GS_Single_L_Module_Transformer_Temperature = int(response.registers[0])
                logger.debug(".... FXR L Transformer Temperature  " + str(GS_Single_L_Module_Transformer_Temperature))
                
                response = client.read_holding_registers(reg + 22, count=1)
                GS_Single_L_Module_Capacitor_Temperature = int(response.registers[0])
                logger.debug(".... FXR L Capacitor Temperature  " + str(GS_Single_L_Module_Capacitor_Temperature))
                
                response = client.read_holding_registers(reg + 23, count=1)
                GS_Single_L_Module_FET_Temperature = int(response.registers[0])
                logger.debug(".... FXR L FET Temperature  " + str(GS_Single_L_Module_FET_Temperature))                  

                response = client.read_holding_registers(reg + 27, count=1)
                gs_single_battery_temperature = decode_int16(int(response.registers[0]))
                logger.debug(".... FXR Battery temperature (V) " + str(gs_single_battery_temperature))
               
                response = client.read_holding_registers(reg + 15, count=1)
                GS_Split_Error_Flags = int(response.registers[0])
                logger.debug(".... FXR Error Flags " + str(GS_Split_Error_Flags))
                error_flags='None'
                if GS_Split_Error_Flags == 0:   error_flags='Nothing'                
                if GS_Split_Error_Flags == 1:   error_flags='Low AC output voltage'
                if GS_Split_Error_Flags == 2:   error_flags='Stacking error'               
                if GS_Split_Error_Flags == 4:   error_flags='Over temperature error'
                if GS_Split_Error_Flags == 8:   error_flags='Low battery voltage'               
                if GS_Split_Error_Flags == 16:  error_flags='Phase loss'                
                if GS_Split_Error_Flags == 32:  error_flags='High battery voltage'
                if GS_Split_Error_Flags == 64:  error_flags='AC output shorted'               
                if GS_Split_Error_Flags == 128: error_flags='AC backfeed'
                
                response = client.read_holding_registers(reg + 16, count=1)
                GS_Single_Warning_Flags = int(response.registers[0])
                logger.debug(".... FXR Warning Flags " + str(GS_Single_Warning_Flags))
                warning_flags='None'
                if GS_Single_Warning_Flags == 0:   warning_flags='Nothing'                
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
                  "transformator_temperature": GS_Single_L_Module_Transformer_Temperature,
                  "capacitors_temperature": GS_Single_L_Module_Capacitor_Temperature,
                  "fet_temperature": GS_Single_L_Module_FET_Temperature,
                  "error_modes": [
                    error_flags
                  ],
                  "ac_mode": ac_use,
                  "battery_voltage":gs_single_battery_voltage,
                  "aux_relay":aux_relay,
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
                mqtt_devices.append({
                             "outback/inverters/" + str(inverters) + "/inverter_current":gs_single_inverter_output_current,
                             "outback/inverters/" + str(inverters) + "/charge_current"  :gs_single_inverter_charge_current,
                             "outback/inverters/" + str(inverters) + "/buy_current"     :gs_single_inverter_buy_current,
                             "outback/inverters/" + str(inverters) + "/sell_current"    :GS_Single_Inverter_Sell_Current,
                             "outback/inverters/" + str(inverters) + "/battery_voltage" :gs_single_battery_voltage,
                             "outback/inverters/" + str(inverters) + "/battery_voltage_compensated" :gs_single_temp_compensated_target_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_input"        :gs_single_ac_input_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_output"       :gs_single_output_ac_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_use"          :ac_use,
                             "outback/inverters/" + str(inverters) + "/operating_modes" :operating_modes,
                             "outback/inverters/" + str(inverters) + "/aux_relay"       :aux_relay,
                             "outback/inverters/" + str(inverters) + "/error_flags"     :error_flags,
                             "outback/inverters/" + str(inverters) + "/warning_modes"   :warning_flags,
                             "outback/inverters/" + str(inverters) + "/trafo_temp"      :GS_Single_L_Module_Transformer_Temperature,
                             "outback/inverters/" + str(inverters) + "/capacitor_temp"  :GS_Single_L_Module_Capacitor_Temperature,
                             "outback/inverters/" + str(inverters) + "/fet_temp"        :GS_Single_L_Module_FET_Temperature
                             })
      
        except Exception as e:
            logger.warning("port: " + str(port) + " FXR module " + str(e))
        
        try:
            if "Radian Inverter Configuration Block" in blockResult['DID']:
                response = client.read_holding_registers(reg + 26, count=1)
                GSconfig_Grid_Input_Mode = int(response.registers[0])
                logger.debug(".... FXR Grid input Mode " + str(GSconfig_Grid_Input_Mode))
                grid_input_mode='None'
                if GSconfig_Grid_Input_Mode == 0:   grid_input_mode ='Generator'
                if GSconfig_Grid_Input_Mode == 1:   grid_input_mode ='Support'               
                if GSconfig_Grid_Input_Mode == 2:   grid_input_mode ='GridTied'
                if GSconfig_Grid_Input_Mode == 3:   grid_input_mode ='UPS'               
                if GSconfig_Grid_Input_Mode == 4:   grid_input_mode ='Backup'                
                if GSconfig_Grid_Input_Mode == 5:   grid_input_mode ='MiniGrid' 
                if GSconfig_Grid_Input_Mode == 6:   grid_input_mode ='GridZero'
                
                response = client.read_holding_registers(reg + 24, count=1)
                GSconfig_Charger_Operating_Mode = int(response.registers[0])
                logger.debug(".... FXR Charger Mode " + str(GSconfig_Charger_Operating_Mode))
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
                mqtt_devices.append(
                        {"outback/inverters/" + str(inverters) + "/grid_input_mode":grid_input_mode,
                         "outback/inverters/" + str(inverters) + "/charger_mode"   :charger_mode})
     
        except Exception as e:
            logger.warning("port: " + str(port) + " FXR config block " + str(e))

        try:
            if "Charge Controller Block" in blockResult['DID']:
                logger.debug(".. Detected a Charge Controller Block")
                chargers = chargers +1

                response = client.read_holding_registers(reg + 2, count=1)
                logger.debug(".... Connected on HUB port " + str(response.registers[0]))
                port=(response.registers[0]-1)
                address=port+1
     
                response = client.read_holding_registers(reg + 10, count=1)
                cc_batt_current = round(int(response.registers[0]) * 0.1,2)    # correction value *0.1
                logger.debug(".... CC Battery Current (A) " + str(cc_batt_current))
     
                response = client.read_holding_registers(reg + 11, count=1)
                cc_array_current = round(int(response.registers[0]),2)
                logger.debug(".... CC Array Current (A) " + str(cc_array_current))
                
                response = client.read_holding_registers(reg + 9, count=1)
                cc_array_voltage = round(int(response.registers[0]) * 0.1,2)
                logger.debug(".... CC Array Voltage " + str(cc_array_voltage))
                
                response = client.read_holding_registers(reg + 18, count=1)
                CC_Todays_KW = round(int(response.registers[0]) * 0.1,2)
                logger.debug(".... CC Daily_KW (KW) " + str(CC_Todays_KW))
                CC_total_daily_kwh = CC_total_daily_kwh + CC_Todays_KW
                
                response = client.read_holding_registers(reg + 13, count=1)
                CC_Watts = round(int(response.registers[0]),2)
                logger.debug(".... CC Actual_watts (W) " + str(CC_Watts))
                CC_total_watts = CC_total_watts + CC_Watts

                response = client.read_holding_registers(reg + 12, count=1)
                cc_charger_state = round(int(response.registers[0]),2)
                logger.debug(".... CC Charger State " + str(cc_charger_state))  # 0=Silent,1=Float,2=Bulk,3=Absorb,4=EQ
                cc_mode_list=["Silent","Float","Bulk","Absorb","Equalize"]
                cc_mode=cc_mode_list[cc_charger_state]
     
                response = client.read_holding_registers(reg + 8, count=1)
                cc_batt_voltage = round(int(response.registers[0]) * 0.1,2)
                logger.debug(".... CC Battery Voltage (V) " + str(cc_batt_voltage))
     
                response = client.read_holding_registers(reg + 19, count=1)
                CC_Todays_AH = round(int(response.registers[0]),2)
                logger.debug(".... CC Daily_AH (A) " + str(CC_Todays_AH))
          
            if "Charge Controller Configuration block" in blockResult['DID']:           #some CC parameters are in configuration block
                logger.debug(".. Charge Controller Configuration block")
                response = client.read_holding_registers(reg + 2, count=1)
                logger.debug(".... Connected on HUB port " + str(response.registers[0]))
                port=(response.registers[0]-1)
                
                response = client.read_holding_registers(reg + 32, count=1)
                CCconfig_AUX_Mode   = int(response.registers[0])
                logger.debug(".... CC Aux Mode " + str(CCconfig_AUX_Mode))
              
                aux_mode_list=["Float","Diversion: Relay","Diversion:SSR","Low Batt Disconnect",
                          "Remote","Vent Fan","PV Trigger","Error Output","Night Light"]                                         # 0 disabled, 1 enabled 
                aux_mode=aux_mode_list[CCconfig_AUX_Mode ]
                
                response = client.read_holding_registers(reg + 34, count=1)
                CCconfig_AUX_State  = int(response.registers[0])
                logger.debug(".... CC Aux State " + str(CCconfig_AUX_State))
                aux_state_list=[
                "Disabled",     # 0 - disabled
                "Enabled"       # 1 - Enabled
                ]                                         
                aux_state=aux_state_list[CCconfig_AUX_State]
                
                response = client.read_holding_registers(reg + 9, count=1)
                CCconfig_Faults = int(response.registers[0])
                logger.debug(".... CC Error Flags " + str(CCconfig_Faults))
                error_flags='None'            
                if CCconfig_Faults == 0:   error_flags='Nothing' 
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
                mqtt_devices.append({
                    "outback/chargers/" + str(chargers) + "/charge_current" :cc_batt_current,
                    "outback/chargers/" + str(chargers) + "/pv_current"     :cc_array_current,
                    "outback/chargers/" + str(chargers) + "/pv_voltage"     :cc_array_voltage,
                    "outback/chargers/" + str(chargers) + "/pv_watts"       :CC_Watts,
                    "outback/chargers/" + str(chargers) + "/aux"            :aux_mode,
                    "outback/chargers/" + str(chargers) + "/aux_mode"       :aux_state,
                    "outback/chargers/" + str(chargers) + "/error_modes"    :error_flags,
                    "outback/chargers/" + str(chargers) + "/battery_voltage":cc_batt_voltage,
                    "outback/chargers/" + str(chargers) + "/daily_ah"       :CC_Todays_AH,
                    "outback/chargers/" + str(chargers) + "/daily_kwh"      :CC_Todays_KW,
                    "outback/chargers/" + str(chargers) + "/charge_mode"    :cc_mode
                    })                 
        
        except Exception as e:
            logger.warning("port: " + str(port) + " CC module " + str(e))

        try:
            if "FLEXnet-DC Real Time Block" in blockResult['DID']:
                logger.debug(".. Detect a FLEXnet-DC Real Time Block")

                response = client.read_holding_registers(reg + 2, count=1)
                logger.debug(".... Connected on HUB port " + str(response.registers[0]))
                port=(response.registers[0]-1)
                address=port+1

                response = client.read_holding_registers(reg + 8, count=1)
                fn_shunt_a_current = round(decode_int16(int(response.registers[0])) * 0.1,2)
                logger.debug(".... FN Shunt A Current (A) " + str(fn_shunt_a_current))
                
                response = client.read_holding_registers(reg + 9, count=1)
                fn_shunt_b_current = round(decode_int16(response.registers[0]) * 0.1,2)
                logger.debug(".... FN Shunt B Current (A) " + str(fn_shunt_b_current))
               
                response = client.read_holding_registers(reg + 10, count=1)
                fn_shunt_c_current = round(decode_int16(int(response.registers[0])) * 0.1,2)
                logger.debug(".... FN Shunt C Current (A) " + str(fn_shunt_c_current))

                response = client.read_holding_registers(reg + 11, count=1)
                fn_battery_voltage = round(int(response.registers[0]) * 0.1,2)
                logger.debug(".... FN Battery Voltage " + str(fn_battery_voltage))

                response = client.read_holding_registers(reg + 27, count=1)
                fn_state_of_charge = int(response.registers[0])
                logger.debug(".... FN State of Charge " + str(fn_state_of_charge))
                
                response = client.read_holding_registers(reg + 14, count=1)
                FN_Status_Flags  = int(response.registers[0])
                logger.debug(".... FN Status Flag " + str(FN_Status_Flags))
                charge_params_met="false"
                if FN_Status_Flags==2 or FN_Status_Flags==6 or FN_Status_Flags==7:
                    charge_params_met="true"              
                logger.debug(".... FN Charge Parameters Met " + str(FN_Status_Flags ) + " " + charge_params_met)
                relay_status="disabled"
                if FN_Status_Flags==1 or FN_Status_Flags==3 or FN_Status_Flags==5 or FN_Status_Flags==7:
                    relay_status="enabled"              
                logger.debug(".... FN Relay Status " + str(FN_Status_Flags ) + " " + relay_status)
                relay_mode="auto"
                if FN_Status_Flags==4 or FN_Status_Flags==5 or FN_Status_Flags==7:
                    relay_mode="manual"              
                logger.debug(".... FN Relay Mode " + str(FN_Status_Flags ) + " " + relay_mode)

                response = client.read_holding_registers(reg + 13, count=1)
                fn_battery_temperature = decode_int16(int(response.registers[0]))
                logger.debug(".... FN Battery Temperature " + str(fn_battery_temperature))

                response = client.read_holding_registers(reg + 15, count=1)
                FN_Shunt_A_Accumulated_AH = round(decode_int16(int(response.registers[0])),2)
                logger.debug(".... FN FN_Shunt_A_Accumulated_AH " + str(FN_Shunt_A_Accumulated_AH))

                response = client.read_holding_registers(reg + 16, count=1)
                FN_Shunt_A_Accumulated_kWh = round(decode_int16(int(response.registers[0])) * 0.01,2)
                logger.debug(".... FN FN_Shunt_A_Accumulated_kWh " + str(FN_Shunt_A_Accumulated_kWh))

                response = client.read_holding_registers(reg + 17, count=1)
                FN_Shunt_B_Accumulated_AH = round(decode_int16(int(response.registers[0])),2)
                logger.debug(".... FN FN_Shunt_B_Accumulated_AH " + str(FN_Shunt_B_Accumulated_AH))

                response = client.read_holding_registers(reg + 18, count=1)
                FN_Shunt_B_Accumulated_kWh = round(decode_int16(int(response.registers[0])) * 0.01,2)
                logger.debug(".... FN FN_Shunt_B_Accumulated_kWh " + str(FN_Shunt_B_Accumulated_kWh))
                     
                response = client.read_holding_registers(reg + 19, count=1)
                FN_Shunt_C_Accumulated_AH = round(decode_int16(int(response.registers[0])),2)
                logger.debug(".... FN FN_Shunt_C_Accumulated_AH " + str(FN_Shunt_C_Accumulated_AH))

                response = client.read_holding_registers(reg + 20, count=1)
                FN_Shunt_C_Accumulated_kWh = round(decode_int16(int(response.registers[0])) * 0.01,2)
                logger.debug(".... FN FN_Shunt_C_Accumulated_kWh " + str(FN_Shunt_C_Accumulated_kWh))
                
                response = client.read_holding_registers(reg + 26, count=1)
                FN_Days_Since_Charge_Parameters_Met = round(int((response.registers[0])) * 0.1,2)
                logger.debug(".... FN days_since_full " + str(FN_Days_Since_Charge_Parameters_Met))
                
                response = client.read_holding_registers(reg + 28, count=1)
                FN_Todays_Minimum_SOC = int(response.registers[0])
                logger.debug(".... FN Todays_Minimum_SOC " + str(FN_Todays_Minimum_SOC))

                response = client.read_holding_registers(reg + 29, count=1)
                FN_Todays_Maximum_SOC = int(response.registers[0])
                logger.debug(".... FN Todays_Maximum_SOC " + str(FN_Todays_Maximum_SOC))
                
                response = client.read_holding_registers(reg + 30, count=1)
                FN_Todays_NET_Input_AH = round(int(response.registers[0]),2)
                logger.debug(".... FN Todays_NET_Input_AH " + str(FN_Todays_NET_Input_AH))

                response = client.read_holding_registers(reg + 31, count=1)
                FN_Todays_NET_Input_kWh = round(int(response.registers[0]) * 0.01,2)
                logger.debug(".... FN Todays_NET_Input_kWh " + str(FN_Todays_NET_Input_kWh))
                
                response = client.read_holding_registers(reg + 32, count=1)
                FN_Todays_NET_Output_AH = round(int(response.registers[0]),2)
                logger.debug(".... FN Todays_NET_Output_AH " + str(FN_Todays_NET_Output_AH))
                
                response = client.read_holding_registers(reg + 33, count=1)
                FN_Todays_NET_Output_kWh = round(int(response.registers[0]) * 0.01,2)
                logger.debug(".... FN Todays_NET_Output_kWh " + str(FN_Todays_NET_Output_kWh))

                response = client.read_holding_registers(reg + 36, count=1)
                FN_Charge_Factor_Corrected_NET_Battery_AH = round(decode_int16(int(response.registers[0])),2)
                logger.debug(".... FN Charge_Factor_Corrected_NET_Battery_AH " + str(FN_Charge_Factor_Corrected_NET_Battery_AH))
                
                response = client.read_holding_registers(reg + 37, count=1)
                FN_Charge_Factor_Corrected_NET_Battery_kWh = round(decode_int16(int(response.registers[0])) * 0.01,2)
                logger.debug(".... FN_Charge_Factor_Corrected_NET_Battery_kWh " + str(FN_Charge_Factor_Corrected_NET_Battery_kWh))
                
                response = client.read_holding_registers(reg + 38, count=1)
                FN_Todays_Minimum_Battery_Voltage = round(decode_int16(int(response.registers[0])) * 0.1 ,2)
                logger.debug(".... FN_Todays_Minimum_Battery_Voltage " + str(FN_Todays_Minimum_Battery_Voltage))                
                
                response = client.read_holding_registers(reg + 41, count=1)
                FN_Todays_Maximum_Battery_Voltage = round(decode_int16(int(response.registers[0])) * 0.1 ,2)
                logger.debug(".... FN_Todays_Maximum_Battery_Voltage " + str(FN_Todays_Maximum_Battery_Voltage))                

            if "FLEXnet-DC Configuration Block" in blockResult['DID']:
                logger.debug(".. Detect a FLEXnet-DC Configuration Block")

                response = client.read_holding_registers(reg + 2, count=1)
                logger.debug(".... Connected on HUB port " + str(response.registers[0]))
                port=(response.registers[0]-1)
                
                response = client.read_holding_registers(reg + 14, count=1)
                FNconfig_Shunt_A_Enabled = int(response.registers[0])
                Shunt_A_Enabled_list=("ON","OFF")
                Shunt_A_Enabled=Shunt_A_Enabled_list[FNconfig_Shunt_A_Enabled]
                logger.debug(".... FN Shunt_A_Enabled " + Shunt_A_Enabled)
                
                response = client.read_holding_registers(reg + 15, count=1)
                FNconfig_Shunt_B_Enabled = int(response.registers[0])
                Shunt_B_Enabled_list=("ON","OFF")
                Shunt_B_Enabled=Shunt_B_Enabled_list[FNconfig_Shunt_B_Enabled]
                logger.debug(".... FN Shunt_B_Enabled " + Shunt_B_Enabled)
                
                response = client.read_holding_registers(reg + 16, count=1)
                FNconfig_Shunt_C_Enabled = int(response.registers[0])
                Shunt_C_Enabled_list=("ON","OFF")
                Shunt_C_Enabled=Shunt_C_Enabled_list[FNconfig_Shunt_C_Enabled]
                logger.debug(".... FN Shunt_C_Enabled " + Shunt_C_Enabled)
                
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
                mqtt_devices.append ({
                     "outback/fndc/battery_voltage"      :fn_battery_voltage,
                     "outback/fndc/state_of_charge"      :fn_state_of_charge,
                     "outback/fndc/battery_temperature"  :fn_battery_temperature,
                     "outback/fndc/shunt_a_current"      :fn_shunt_a_current,
                     "outback/fndc/shunt_c_current"      :fn_shunt_c_current,
                     "outback/fndc/shunt_b_current"      :fn_shunt_b_current,
                     "outback/fndc/charge_params_met"    :charge_params_met,
                     "outback/fndc/today_min_soc"        :FN_Todays_Minimum_SOC,
                     "outback/fndc/days_since_charge_met":FN_Days_Since_Charge_Parameters_Met,
                     "outback/fndc/today_net_input_ah"   :FN_Todays_NET_Input_AH,
                     "outback/fndc/today_net_output_ah"  :FN_Todays_NET_Output_AH,
                     "outback/fndc/todays_net_input_kWh" :FN_Todays_NET_Input_kWh,
                     "outback/fndc/todays_net_output_kWh":FN_Todays_NET_Output_kWh
                     })

        except Exception as e:
            logger.warning("port: " + str(port) + " FNDC module " + str(e))

        if "End of SunSpec" not in blockResult['DID']:
            reg = reg + blockResult['size'] + 2
        else:
            client.close()
            mate_run = datetime.now()                                             
            running_time = round ((mate_run - start_run).total_seconds(),3)       
            logger.debug(" Mate connection closed")
            print("running time Mate:       ",format(running_time,".3f")," sec")  
  
            break
  
    # MariaDB upload
    mariadb_run = datetime.now() 
    try:
        if SQL_active=='true':
            
            date_now=curent_date_time.strftime("%Y-%m-%d") #current date
            mydb = mariadb.connect(host=host,port=db_port,user=user,password=password,database=database)
            
            # devices data - MariaDB upload
            n=0
            for value in db_devices_values:
                mycursor = mydb.cursor()
                mycursor.execute(db_devices_sql[n], value)
                n = n+1
            
            # summary of the day calculation for MariaDB upload
            sql="SELECT date,kwh_in,kwh_out,ah_in,max_soc,min_soc FROM monitormate_summary \
            where date(date)= DATE(NOW())"
            
            mycursor = mydb.cursor()
            mycursor.execute(sql)
            myresult = mycursor.fetchall()

            if not myresult:                                                               # check if any records for today - if not, record for the first time
                val=(date_now,FN_Todays_NET_Input_kWh,FN_Todays_NET_Output_kWh,FN_Todays_NET_Input_AH,FN_Todays_NET_Output_AH,FN_Todays_Maximum_SOC,FN_Todays_Minimum_SOC)
                sql="INSERT INTO monitormate_summary (date,kwh_in,kwh_out,ah_in,ah_out,max_soc,min_soc)\
                VALUES (%s,%s,%s,%s,%s,%s,%s)"
                mycursor = mydb.cursor()
                mycursor.execute(sql, val)
                mydb.commit()
                logger.debug(" Summary of the day - first record completed")
            else:                                                                           # if records - update table
                val=(FN_Todays_NET_Input_kWh,FN_Todays_NET_Output_kWh,FN_Todays_NET_Input_AH,FN_Todays_NET_Output_AH,
                     FN_Todays_Maximum_SOC,FN_Todays_Minimum_SOC,date_now)
                sql="UPDATE monitormate_summary SET kwh_in=%s,kwh_out=%s,ah_in=%s,ah_out=%s,\
                max_soc=%s,min_soc=%s WHERE date=%s"
                mycursor = mydb.cursor()
                mycursor.execute(sql, val)
                mydb.commit()
                
            mycursor.close()
            mydb.close()
            mariadb_run = datetime.now()                                             
            running_time = round ((mariadb_run - mate_run).total_seconds(),3)        
            print("running time MariaDB:    ",format(running_time,".3f")," sec")     

    except Exception as e:
        mycursor.close()
        mydb.close()
        mariadb_run = datetime.now()
        logger.warning ("MariaDB upload - "+ str(e))

    # Summary data - JSON preparation
    date_now=curent_date_time.strftime("%Y-%m-%d") #current date
    summary={
        "date": date_now,
        "kwh_in": FN_Todays_NET_Input_kWh,
        "kwh_out": FN_Todays_NET_Output_kWh,
        "ah_in": FN_Todays_NET_Input_AH,
        "ah_out": FN_Todays_NET_Output_AH,
        "min_voltage": FN_Todays_Minimum_Battery_Voltage,        
        "max_voltage": FN_Todays_Maximum_Battery_Voltage,
        "min_soc": FN_Todays_Minimum_SOC,
        "max_soc": FN_Todays_Maximum_SOC,
        "pv_watts": CC_total_watts,
        "pv_daily_Kwh":CC_total_daily_kwh}

    # summary values - send data via MQTT 
    mqtt_devices.append ({
        "outback/summary/cc_total_watts":CC_total_watts,
        "outback/summary/pv_daily_Kwh"  :CC_total_daily_kwh
        })

    #JSON serialisation and save
    try:
        json_data={"time":time, "devices":devices, "summary":summary, "various":various}
        with open(os.path.join(output_path, 'mate_status.json'), 'w') as outfile:
            json.dump(json_data, outfile)
        if duplicate_active == 'true':
            #shutil.copy(os.path.join(output_path, 'mate_status.json'), os.path.join(duplicate_path, 'mate_status.json'))      #copy the file in second location
            shutil.copy(os.path.join(output_path, 'mate_status.json'), os.path.join(duplicate_path, 'mate_status.json'))
        json_run = datetime.now()                                                           
        running_time = round ((json_run - mariadb_run).total_seconds(),3)                   
        print("running time JSON:       ",format(running_time,".3f")," sec")                

    except Exception as e:
        logger.error("JSON read/write - " + str(e))
        json_run = datetime.now()
        
    # MQTT send data to MQTT broker
    try:
        if MQTT_active=='true':
            for mqtt_data in mqtt_devices:
                for topic in mqtt_data:
                    publish.single(topic, mqtt_data[topic], hostname=MQTT_broker)
                    
        ## send overall json data via MQTT                                                  
        state_topic = "outback/mate"                                   
        message     = json.dumps(json_data)                                         
        publish.single(state_topic, message, hostname=MQTT_broker)                    

        mqtt_run = datetime.now()                                                   
        running_time = round ((mqtt_run - json_run).total_seconds(),3)              
        print("running time MQTT:       ",format(running_time,".3f"), " sec")        
      
    except Exception as e:
        logger.error("MQTT module - "+ str(e))

    #time.sleep(30) # DPO - used if continue loop is used
    break           # DPO - remark it if continuous loop needed

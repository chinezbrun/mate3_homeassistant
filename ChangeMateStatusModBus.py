import logging
from logging.handlers import RotatingFileHandler
import json
import time
from datetime import datetime
from pymodbus.client import ModbusTcpClient as ModbusClient
from pymodbus.constants import Endian
from configparser import ConfigParser
import sys, os

script_ver = "0.9.0_20250126"
print ("script version   : "+ script_ver)

pathname          = os.path.dirname(sys.argv[0])
working_dir       = os.path.abspath(pathname) 

config            = ConfigParser()
config.read(os.path.join(working_dir, 'config.cfg'))

mate3_ip                             = config.get('MATE3 connection', 'mate3_ip')
mate3_modbus                         = config.get('MATE3 connection', 'mate3_modbus')
sunspec_start_reg                    = 40000
MQTT_active                          = config.get('MQTT', 'MQTT_active')                        # default = false  -- if active will publish MQTT topics to varoius platforms i.e Home Assistant
MQTT_broker                          = config.get('MQTT', 'MQTT_broker')                        # your MQTT broker address - i.e 192.168.0.xxx
output_path                          = config.get('Path', 'output_path')

# merge paths to use proper separators windows or Linux
if output_path == "":
    output_path = os.path.join(working_dir, 'data')
    
LOGGING_LEVEL_FILE                  = config.get('General','LOGGING_LEVEL_FILE')
LOGGING_FILE_MAX_SIZE               = int(config.get('General','LOGGING_FILE_MAX_SIZE'))
LOGGING_FILE_MAX_FILES              = int(config.get('General','LOGGING_FILE_MAX_FILES'))

loop                                = 0 
curent_date_time                    = datetime.now()

# ACmode_list is used to convert numbers in pretty name -- in registry modes are coded like below:
ACmode_list = [
    "Generator",     # 0
    "Support",       # 1
    "GridTied",      # 2
    "UPS",           # 3
    "Backup",        # 4
    "MiniGrid",      # 5
    "GridZero",      # 6
    "Disabled"]      # 7

# Charge_Enable_Disable_list is used to convert numbers in pretty name -- in registry modes are coded like below:
Charge_Enable_Disable_list = [
    "Default",       #0
    "StartBulk",     #1
    "StopBulk",      #2
    "StartEQ",       #3
    "StopEQ"]        #4

# initiate block's flags - used to prevent conflicts when bulk data is written
OutbackBlock_flag               = 0                         
InverterConfigurationBlock_flag = 0
OutbackSystemControlBlock_flag  = 0

def blankjsonfile():
    mate_input = {
    "time_posted":str(curent_date_time.strftime("%Y-%m-%d %H:%M:%S")), 
    "time_taken": "",
    "weather":{
        "date"          :"notset",
        "ID"            :"notset",
        "description"   :"notset",
        "cloud_coverage":"notset"
        },
    "predictive_soc":{
        "deye_soc"      :"notset",
        "outback_soc"   :"notset"
        },       
    "OutbackBlock":{
        "OutbackBlock_flag":0,
        "outback_schedule":{
            "sched_1_ac_mode"     :"notset",
            "sched_1_ac_mode_hour":"notset",
            "sched_1_ac_mode_minute":"notset",
            "sched_2_ac_mode"     :"notset",                
            "sched_2_ac_mode_hour":"notset",
            "sched_2_ac_mode_minute":"notset",
            "sched_3_ac_mode"     :"notset",
            "sched_3_ac_mode_hour":"notset",
            "sched_3_ac_mode_minute":"notset"
            }},
     "OutbackSystemControlBlock": {
        "OutbackSystemControlBlock_flag": 0,
        "Charge_Enable_Disable": "notset"
         },
     "RadianInverterConfigurationBlock": {
        "InverterConfigurationBlock_flag": 0,
        "charger_operating_mode": "notset",
        "grid_input_mode": "notset"
         }
     }
    
    json_path = os.path.join(output_path, 'mate_input.json')
    with open(json_path, 'w') as outfile:
        json.dump(mate_input, outfile, indent=1)
    print("multiple input json file created")
    
# prepare the bulk parameters to be writen 
if output_path != "none":
    try:
        mate_input                         = json.load(open(output_path + '/mate_input.json'))
        Sched_1_AC_Mode_local                = mate_input["OutbackBlock"]["outback_schedule"]["sched_1_ac_mode"]
        OutBack_Sched_1_AC_Mode_Hour_local   = mate_input["OutbackBlock"]["outback_schedule"]["sched_1_ac_mode_hour"]
        OutBack_Sched_1_AC_Mode_Minute_local = mate_input["OutbackBlock"]["outback_schedule"]["sched_1_ac_mode_minute"]
        Sched_2_AC_Mode_local                = mate_input["OutbackBlock"]["outback_schedule"]["sched_2_ac_mode"]
        OutBack_Sched_2_AC_Mode_Hour_local   = mate_input["OutbackBlock"]["outback_schedule"]["sched_2_ac_mode_hour"]
        OutBack_Sched_2_AC_Mode_Minute_local = mate_input["OutbackBlock"]["outback_schedule"]["sched_2_ac_mode_minute"]
        Sched_3_AC_Mode_local                = mate_input["OutbackBlock"]["outback_schedule"]["sched_3_ac_mode"]
        OutBack_Sched_3_AC_Mode_Hour_local   = mate_input["OutbackBlock"]["outback_schedule"]["sched_3_ac_mode_hour"]
        OutBack_Sched_3_AC_Mode_Minute_local = mate_input["OutbackBlock"]["outback_schedule"]["sched_3_ac_mode_minute"]
        OutbackBlock_flag                    = mate_input["OutbackBlock"]["OutbackBlock_flag"]
        Charger_Operating_Mode_local         = mate_input["RadianInverterConfigurationBlock"]["charger_operating_mode"]
        Grid_Input_Mode_local                = mate_input["RadianInverterConfigurationBlock"]["grid_input_mode"]
        InverterConfigurationBlock_flag      = mate_input["RadianInverterConfigurationBlock"]["InverterConfigurationBlock_flag"]
        OB_Charge_Enable_Disable_local       = mate_input["OutbackSystemControlBlock"]["Charge_Enable_Disable"]
        OutbackSystemControlBlock_flag       = mate_input["OutbackSystemControlBlock"]["OutbackSystemControlBlock_flag"]
    except Exception as e:
        print("multiple input json file not found " + str(e))
        blankjsonfile()
        
print ("working directory: " +  working_dir)
print ("output path      : " + output_path)
print ("variables initialization completed")

# Creează un logger
logger = logging.getLogger("outback")
logger.setLevel(LOGGING_LEVEL_FILE)  # Setează nivelul minim de logare

# Handler pentru consolă
console_handler = logging.StreamHandler()
console_handler.setLevel(LOGGING_LEVEL_FILE)
#formater
console_formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s', datefmt='%Y%m%d %H:%M:%S')
console_handler.setFormatter(console_formatter)

# merge paths to use proper separators windows or Linux
log_path = os.path.join(working_dir, 'data', 'events_cms.log')
file_handler = RotatingFileHandler(log_path , mode='a', maxBytes=LOGGING_FILE_MAX_SIZE*1000, backupCount=LOGGING_FILE_MAX_FILES, encoding=None, delay=False)
file_handler.setLevel(LOGGING_LEVEL_FILE)

#formater
file_formatter = logging.Formatter('%(asctime)s| CMS |%(levelname)8s| %(message)s ',datefmt='%Y%m%d %H:%M:%S') 
file_handler.setFormatter(file_formatter)

# Adaugă handler-ele la logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# if external python arguments provided - this has priority, specific mate_input parameter will be overwriten
if len(sys.argv) > 1:
    
    logger.debug("..'argument received ", sys.argv[1])
    
    if sys.argv[1] == 'On' or sys.argv[1] == 'Off':
        Charger_Operating_Mode_local    = sys.argv[1] # new value received 
        OutbackBlock_flag = 0                         
        OutbackSystemControlBlock_flag  = 0
        InverterConfigurationBlock_flag = 1
        logger.debug("..'Charger_Operating_Mode_local' was overwritten: ", Charger_Operating_Mode_local)
        print("..'Charger_Operating_Mode_local' was overwritten: ", Charger_Operating_Mode_local)
    
    if sys.argv[1] in ACmode_list:
        Grid_Input_Mode_local           = sys.argv[1] # new value received 
        OutbackBlock_flag = 0                        
        OutbackSystemControlBlock_flag  = 0
        InverterConfigurationBlock_flag = 1
        logger.debug("..'Grid_Input_Mode_local' was overwritten: ", Grid_Input_Mode_local)
        print("..'Grid_Input_Mode_local' was overwritten: ", Grid_Input_Mode_local)
    
    if sys.argv[1] in Charge_Enable_Disable_list:
        OB_Charge_Enable_Disable_local  = sys.argv[1] # new value received 
        OutbackBlock_flag = 0                         
        InverterConfigurationBlock_flag = 0
        OutbackSystemControlBlock_flag  = 1
        logger.debug("..'OB_Charge_Enable_Disable_local' was overwritten: ", OB_Charge_Enable_Disable_local)
        print("..'OB_Charge_Enable_Disable_local' was overwritten: ", OB_Charge_Enable_Disable_local)       
else:
    logger.debug(".. no argument received")
    
if (OutbackSystemControlBlock_flag+InverterConfigurationBlock_flag+OutbackBlock_flag) == 0:
    logger.debug(".. nothing to write")
    print(".. nothing to write, exiting")
    exit()
# =================================== ModbusMate subroutines & variables =====================================
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
    length = 69
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
# 
def OutbackBlock():
    loop = 0
    while loop < 3 :
        # autoscheduling 1 -- reading registries
        response = client.read_holding_registers(reg + 409, count=1)
        OutBack_Sched_1_AC_Mode = response.registers[0]
        response = client.read_holding_registers(reg + 410, count=1)                
        OutBack_Sched_1_AC_Mode_Hour = response.registers[0]
        response = client.read_holding_registers(reg + 411, count=1)
        OutBack_Sched_1_AC_Mode_Minute = response.registers[0]
        
        if OutBack_Sched_1_AC_Mode == 65535:
            Sched_1_AC_Mode = ACmode_list[OutBack_Sched_1_AC_Mode-65528]
        else:
            Sched_1_AC_Mode = ACmode_list[OutBack_Sched_1_AC_Mode]
        
        if Sched_1_AC_Mode_local == "Disabled":
            OutBack_Sched_1_AC_Mode_local = 65535
        elif Sched_1_AC_Mode_local not in ACmode_list :
            OutBack_Sched_1_AC_Mode_local = None
        else:
            OutBack_Sched_1_AC_Mode_local = ACmode_list.index(Sched_1_AC_Mode_local)
        
        logger.debug(".... schedule_1 [h:mm] " + str(OutBack_Sched_1_AC_Mode_Hour) + ":" + str(OutBack_Sched_1_AC_Mode_Minute) + " " + str(Sched_1_AC_Mode))

        # autoscheduling 1 -- write OutBack_Sched_1_AC_Mode registry
        if  Sched_1_AC_Mode_local != "notset" and Sched_1_AC_Mode_local in ACmode_list and OutBack_Sched_1_AC_Mode != OutBack_Sched_1_AC_Mode_local:
            rw = client.write_register(reg + 409, OutBack_Sched_1_AC_Mode_local)
            logger.debug(".... updating sch_1 to: " + str(Sched_1_AC_Mode_local))
            Sched_1_AC_Mode_flag = 1
        else:
            Sched_1_AC_Mode_flag = 0
        # autoscheduling 1 -- write OutBack_Sched_1_AC_Mode_Hour registry    
        if  OutBack_Sched_1_AC_Mode_Hour_local != "notset" and OutBack_Sched_1_AC_Mode_Hour_local in range(24) and OutBack_Sched_1_AC_Mode_Hour != OutBack_Sched_1_AC_Mode_Hour_local:
            rw = client.write_register(reg + 410, OutBack_Sched_1_AC_Mode_Hour_local)
            logger.debug(".... updating sch_1 hour : " + str(OutBack_Sched_1_AC_Mode_Hour_local))
            Sched_1_AC_Mode_Hour_flag = 1
        else:
            Sched_1_AC_Mode_Hour_flag = 0

        # autoscheduling 2 -- reading registries
        response = client.read_holding_registers(reg + 412, count=1)
        OutBack_Sched_2_AC_Mode = response.registers[0]
        response = client.read_holding_registers(reg + 413, count=1)                
        OutBack_Sched_2_AC_Mode_Hour = response.registers[0]
        response = client.read_holding_registers(reg + 414, count=1)
        OutBack_Sched_2_AC_Mode_Minute = response.registers[0]
        
        if OutBack_Sched_2_AC_Mode == 65535:
            Sched_2_AC_Mode = ACmode_list[OutBack_Sched_2_AC_Mode-65528]
        else:
            Sched_2_AC_Mode = ACmode_list[OutBack_Sched_2_AC_Mode]
        
        if Sched_2_AC_Mode_local == "Disabled":
            OutBack_Sched_2_AC_Mode_local = 65535
        elif Sched_2_AC_Mode_local not in ACmode_list :
            OutBack_Sched_2_AC_Mode_local = None           
        else:
            OutBack_Sched_2_AC_Mode_local = ACmode_list.index(Sched_2_AC_Mode_local)
       
        logger.debug(".... schedule_2 [h:mm] " + str(OutBack_Sched_2_AC_Mode_Hour) + ":" + str(OutBack_Sched_2_AC_Mode_Minute) + " " + str(Sched_2_AC_Mode))
        
        # autoscheduling 2 -- write OutBack_Sched_2_AC_Mode registry
        if Sched_2_AC_Mode_local != "notset" and Sched_2_AC_Mode_local in ACmode_list and OutBack_Sched_2_AC_Mode != OutBack_Sched_2_AC_Mode_local:
            rw = client.write_register(reg + 412, OutBack_Sched_2_AC_Mode_local)
            logger.debug(".... updating sch_2 to: " + str(Sched_2_AC_Mode_local))
            Sched_2_AC_Mode_flag = 1
        else:
            Sched_2_AC_Mode_flag = 0
            
        # autoscheduling 2 -- write OutBack_Sched_2_AC_Mode_Hour registry    
        if  OutBack_Sched_2_AC_Mode_Hour_local != "notset" and OutBack_Sched_2_AC_Mode_Hour_local in range(24) and OutBack_Sched_2_AC_Mode_Hour != OutBack_Sched_2_AC_Mode_Hour_local:
            rw = client.write_register(reg + 413, OutBack_Sched_2_AC_Mode_Hour_local)
            logger.debug(".... updating sch_2 hour : " + str(OutBack_Sched_2_AC_Mode_Hour_local))
            Sched_2_AC_Mode_Hour_flag = 1
        else:
            Sched_2_AC_Mode_Hour_flag = 0 
 
        # autoscheduling 3 -- reading registries
        response = client.read_holding_registers(reg + 415, count=1)
        OutBack_Sched_3_AC_Mode = response.registers[0]
        response = client.read_holding_registers(reg + 416, count=1)                
        OutBack_Sched_3_AC_Mode_Hour = response.registers[0]
        response = client.read_holding_registers(reg + 417, count=1)
        OutBack_Sched_3_AC_Mode_Minute = response.registers[0]
        
        if OutBack_Sched_3_AC_Mode == 65535:
            Sched_3_AC_Mode = ACmode_list[OutBack_Sched_3_AC_Mode-65528]
        else:
            Sched_3_AC_Mode = ACmode_list[OutBack_Sched_3_AC_Mode]
        
        if Sched_3_AC_Mode_local == "Disabled":
            OutBack_Sched_3_AC_Mode_local = 65535
        elif Sched_3_AC_Mode_local not in ACmode_list :
            OutBack_Sched_3_AC_Mode_local = None           
        else:
            OutBack_Sched_3_AC_Mode_local = ACmode_list.index(Sched_3_AC_Mode_local)
        
        logger.debug(".... schedule_3 [h:mm] " + str(OutBack_Sched_3_AC_Mode_Hour) + ":" + str(OutBack_Sched_3_AC_Mode_Minute) + " " + str(Sched_3_AC_Mode))
        
        # autoscheduling 3 -- write OutBack_Sched_3_AC_Mode registry
        if Sched_3_AC_Mode_local != "notset" and Sched_3_AC_Mode_local in ACmode_list and OutBack_Sched_3_AC_Mode != OutBack_Sched_3_AC_Mode_local:
            rw = client.write_register(reg + 415, OutBack_Sched_3_AC_Mode_local)
            logger.debug(".... updating sch_3 to: " + str(Sched_3_AC_Mode_local))
            Sched_3_AC_Mode_flag = 1
        else:
            Sched_3_AC_Mode_flag = 0
            
        # autoscheduling 3 -- write OutBack_Sched_3_AC_Mode_Hour registry    
        if  OutBack_Sched_3_AC_Mode_Hour_local != "notset" and OutBack_Sched_3_AC_Mode_Hour_local in range(24) and OutBack_Sched_3_AC_Mode_Hour != OutBack_Sched_3_AC_Mode_Hour_local:
            rw = client.write_register(reg + 416, OutBack_Sched_3_AC_Mode_Hour_local)
            logger.debug(".... updating sch_3 hour : " + str(OutBack_Sched_3_AC_Mode_Hour_local))
            Sched_3_AC_Mode_Hour_flag = 1
        else:
            Sched_3_AC_Mode_Hour_flag = 0
 
        if Sched_1_AC_Mode_flag == 0 and Sched_1_AC_Mode_Hour_flag == 0 and\
           Sched_2_AC_Mode_flag == 0 and Sched_2_AC_Mode_Hour_flag == 0 and\
           Sched_3_AC_Mode_flag == 0 and Sched_3_AC_Mode_Hour_flag == 0:
                       
            mate_input["OutbackBlock"]["outback_schedule"]["sched_1_ac_mode"]        = "notset"
            mate_input["OutbackBlock"]["outback_schedule"]["sched_1_ac_mode_hour"]   = "notset"
            mate_input["OutbackBlock"]["outback_schedule"]["sched_1_ac_mode_minute"] = "notset"
            mate_input["OutbackBlock"]["outback_schedule"]["sched_2_ac_mode"]        = "notset"
            mate_input["OutbackBlock"]["outback_schedule"]["sched_2_ac_mode_hour"]   = "notset"
            mate_input["OutbackBlock"]["outback_schedule"]["sched_2_ac_mode_minute"] = "notset"
            mate_input["OutbackBlock"]["outback_schedule"]["sched_3_ac_mode"]        = "notset"
            mate_input["OutbackBlock"]["outback_schedule"]["sched_3_ac_mode_hour"]   = "notset"
            mate_input["OutbackBlock"]["outback_schedule"]["sched_3_ac_mode_minute"] = "notset"
            mate_input["OutbackBlock"]["OutbackBlock_flag"]                          = 0
            break
        else:
            loop = loop + 1
            logger.debug(".... verification loop " +  str(loop))

    if  mate_input["OutbackBlock"]["OutbackBlock_flag"] == 0:
        logger.debug(".... verification completed: all good")
        logger.info("update schedule: " +\
                 str(OutBack_Sched_1_AC_Mode_Hour) + ":" + str(OutBack_Sched_1_AC_Mode_Minute) + " " + str(Sched_1_AC_Mode) + " "+\
                 str(OutBack_Sched_2_AC_Mode_Hour) + ":" + str(OutBack_Sched_2_AC_Mode_Minute) + " " + str(Sched_2_AC_Mode) + " "+\
                 str(OutBack_Sched_3_AC_Mode_Hour) + ":" + str(OutBack_Sched_3_AC_Mode_Minute) + " " + str(Sched_3_AC_Mode))

    else:
        logger.debug(".... update verification failed")
    
    mate_input["time_taken"] = str(curent_date_time.strftime("%Y-%m-%d %H:%M:%S"))
    
    json_path = os.path.join(output_path, 'mate_input.json')
    with open(json_path, 'w') as outfile:
        json.dump(mate_input, outfile, indent=1)
    
    return

def OutbackSystemControlBlock():
    loop = 0
    while loop <= 3 :
        response = client.read_holding_registers(reg + 5, count=1)
        OB_Charge_Enable_Disable = int(response.registers[0])
        logger.debug(".... Curent charging mode " + Charge_Enable_Disable_list[OB_Charge_Enable_Disable])
        
        if OB_Charge_Enable_Disable_local in Charge_Enable_Disable_list and OB_Charge_Enable_Disable_local != Charge_Enable_Disable_list[OB_Charge_Enable_Disable]:
            rw = client.write_register(reg + 5, Charge_Enable_Disable_list.index(OB_Charge_Enable_Disable_local))
            logger.info("updating charging mode to: " + OB_Charge_Enable_Disable_local)
            Charge_Enable_Disable_flag = 1
        else:    
            Charge_Enable_Disable_flag = 0
            mate_input["OutbackSystemControlBlock"]["Charge_Enable_Disable"] = "notset"

        if Charge_Enable_Disable_flag == 0:
            mate_input["OutbackSystemControlBlock"]["OutbackSystemControlBlock_flag"] == 0
            break
        else:
            loop = loop + 1
            logger.debug(".... update verification loop " +  str(loop))

    if mate_input["OutbackSystemControlBlock"]["OutbackSystemControlBlock_flag"] == 0:
        logger.debug(".... verification completed: all good")
    else:
        logger.warning(".... update verification failed")
        
    mate_input["time_taken"] = str(curent_date_time.strftime("%Y-%m-%d %H:%M:%S"))
    
    json_path = os.path.join(output_path, 'mate_input.json')
    with open(json_path, 'w') as outfile:
        json.dump(mate_input, outfile, indent=1)

    return

def RadianInverterConfigurationBlock():
    loop = 0
    while loop <= 3 :
        #GSconfig_Charger_Operating_Mode
        response = client.read_holding_registers(reg + 24, count=1)
        GSconfig_Charger_Operating_Mode = int(response.registers[0])
        logger.debug(".... FXR Charger Mode " + str(GSconfig_Charger_Operating_Mode))
        
        Charger_Operating_Mode='None'
        if GSconfig_Charger_Operating_Mode == 0:   Charger_Operating_Mode ='Off'
        if GSconfig_Charger_Operating_Mode == 1:   Charger_Operating_Mode ='On'
        
        if Charger_Operating_Mode_local != "notset" and Charger_Operating_Mode != Charger_Operating_Mode_local:
            if Charger_Operating_Mode_local == 'On':   GSconfig_Charger_Operating_Mode_SC = 1
            if Charger_Operating_Mode_local == 'Off':  GSconfig_Charger_Operating_Mode_SC = 0
            rw = client.write_register(reg + 24, GSconfig_Charger_Operating_Mode_SC)
            Charger_Operating_Mode = GSconfig_Charger_Operating_Mode_SC
            logger.info("updating AC charging to: " + str(Charger_Operating_Mode_local))
            Charger_Operating_Mode_flag = 1
        else:    
            Charger_Operating_Mode_flag = 0
            mate_input["RadianInverterConfigurationBlock"]["charger_operating_mode"] = "notset"
        
        # GSconfig_Grid_Input_Mode
        response = client.read_holding_registers(reg + 26, count=1)
        GSconfig_Grid_Input_Mode = int(response.registers[0])
        logger.debug(".... FXR Input mode " + ACmode_list[GSconfig_Grid_Input_Mode])
        
        if Grid_Input_Mode_local in ACmode_list and GSconfig_Grid_Input_Mode != ACmode_list.index(Grid_Input_Mode_local):                    
            rw = client.write_register(reg + 26, ACmode_list.index(Grid_Input_Mode_local))
            logger.info("updating AC Mode to: " + Grid_Input_Mode_local)
            Grid_Input_Mode_flag = 1
        else:    
            Grid_Input_Mode_flag = 0
            mate_input["RadianInverterConfigurationBlock"]["grid_input_mode"] = "notset"
       
        if Grid_Input_Mode_flag == 0 and Charger_Operating_Mode_flag == 0:
            mate_input["RadianInverterConfigurationBlock"]["InverterConfigurationBlock_flag"] = 0
            break
        else:
            loop = loop + 1
            logger.debug(".... update verification loop " +  str(loop))
    
    if mate_input["RadianInverterConfigurationBlock"]["InverterConfigurationBlock_flag"] == 0:
        logger.debug(".... verification completed: all good")
    else:
        logger.warning(".... update verification failed")
        
    mate_input["time_taken"] = str(curent_date_time.strftime("%Y-%m-%d %H:%M:%S"))
    
    json_path = os.path.join(output_path, 'mate_input.json')
    with open(json_path, 'w') as outfile:
        json.dump(mate_input, outfile, indent=1)    

    return 

def FLEXnetDCRealTimeBlock():
    logger.debug(".. Detect a FLEXnet-DC Real Time Block")  
    return
 
# =======================================This is the main loop =====================================
#------------------------------------------------
# MATE3 ModBus Interface
#------------------------------------------------

# Try to build the mate3 MODBUS connection
print(".. waiting few seconds ")
time.sleep(1)
start_run  = datetime.now() # used only for runtime calculation
logger.debug("Building MATE3 MODBUS connection")

try:
    client = ModbusClient(mate3_ip, port=mate3_modbus)
    logger.debug(".. Make sure we are indeed connected to an Outback power system")
    reg    = sunspec_start_reg
    size   = getSunSpec(reg)
    if size is None:
        logger.debug("We have failed to detect an Outback system. Exciting")
        client.close()
        exit()
except Exception as e:
    client.close()
    logger.error(".. Failed to connect to MATE3. Enable SUNSPEC and check port. Exciting"+ str(e))
    exit()

logger.debug(".. Connected OK to an Outback system")

# scanning blocks
startReg = reg + size + 4
while True:
    reg = startReg
    check = 0
    for block in range(0, 30):
        blockResult = getBlock(reg)
        
        if "Outback block" in blockResult['DID']:
            logger.debug(".. Detected a Outback Block")
            if OutbackBlock_flag == 1: OutbackBlock()
            
        if "Outback System Control Block" in blockResult['DID']:
            logger.debug(".. Detected a Outback System Control Block")
            if OutbackSystemControlBlock_flag == 1: OutbackSystemControlBlock()
            
        if "Radian Inverter Configuration Block" in blockResult['DID']: 
            logger.debug(".. Detected a FXR inverter")
            if InverterConfigurationBlock_flag == 1: RadianInverterConfigurationBlock()

        if "FLEXnet-DC Real Time Block" in blockResult['DID']: FLEXnetDCRealTimeBlock()
        
        if "End of SunSpec" not in blockResult['DID']:
            reg = reg + blockResult['size'] + 2
        else:
            break

    if loop >0:
        logger.debug(".. update verification completed in " + str(loop) +" loop: all good")

    client.close()
    logger.debug(".. Mate connection closed ")
    logger.debug("Exiting ")
    print(".. done ")
    end_run = datetime.now()                                                 
    running_time = round ((end_run - start_run).total_seconds(),3)           
    print("response time Mate3:    ",format(running_time,".3f")," sec")       

    break           

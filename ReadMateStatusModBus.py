import json
import time as tm
import logging
from logging.handlers import RotatingFileHandler
import mysql.connector as mariadb
from datetime import datetime
from pymodbus.client import ModbusTcpClient as ModbusClient
from configparser import ConfigParser
import paho.mqtt.publish as publish
import shutil  
import sys, os
import re

script_ver = "1.2.0_20260804"
print ("script version: "+ script_ver)

pathname               = os.path.dirname(sys.argv[0])
working_dir            = os.path.abspath(pathname) 


# CLI overrides - temporary config overrides from key=value arguments
CLI_ALLOWED_KEYS = {
    "daemon_active",
    "duplicate_active",
    "MQTT_active",
    "MQTT_discovery_active",
    "SQL_active"
}

# CLI overrides - decoding
def apply_cli_overrides(cfg):
    # flag: becomes True if at least one valid CLI parameter is found
    cli_override_found = False

    for arg in sys.argv[1:]:
        # accept only key=value format
        if "=" not in arg:
            continue

        try:
            key, value = arg.split("=", 1)
            key   = key.strip()
            value = value.strip()

            # allow only whitelisted keys
            if key not in CLI_ALLOWED_KEYS:
                continue

            # apply override to all sections containing this key
            for section in cfg.sections():
                if cfg.has_option(section, key):
                    old_value = cfg.get(section, key, fallback=None)
                    cfg.set(section, key, value)
                    cli_override_found = True

        except Exception:
            
            # silent fallback - do not interrupt execution
            print("CLI override:            ", cli_override_found, "- key/value not valid")
            pass

    # if at least one valid CLI parameter was provided:
    if cli_override_found:
        
        cfg.set('General', 'daemon_active', 'false')
        cfg.set('Path', 'duplicate_active', 'false')
        
    print("CLI override:            ", cli_override_found)
    print("working directory:       ", working_dir)
    return cfg

config                 = ConfigParser()
config.read(os.path.join(working_dir, 'config.cfg'))
config                 = apply_cli_overrides(config)  #CLI overrides - temporary config overrides from key=value arguments

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
MQTT_discovery_active  = config.get('MQTT', 'MQTT_discovery_active', fallback='true')
MQTT_broker            = config.get('MQTT', 'MQTT_broker')
MQTT_port              = int(config.get('MQTT', 'MQTT_port'))
MQTT_username          = config.get('MQTT', 'MQTT_username')
MQTT_password          = config.get('MQTT', 'MQTT_password')

daemon_active          = config.get('General','daemon_active', fallback='false')

try:
    scan_frequency = int(config.get('General','scan_frequency', fallback='60'))
    if scan_frequency < 10:
        raise ValueError
except:
    print("Too low scan_frequency, fallback to 10 sec")
    scan_frequency = 10

LOGGING_LEVEL_FILE     = config.get('General','LOGGING_LEVEL_FILE')
LOGGING_FILE_MAX_SIZE  = int(config.get('General','LOGGING_FILE_MAX_SIZE'))
LOGGING_FILE_MAX_FILES = int(config.get('General','LOGGING_FILE_MAX_FILES'))

print("working directory:       ", working_dir)
print("output location:         ", output_path)
print("duplicate output active: ", duplicate_active)
print("SQL active  :            ", SQL_active)
print("MQTT active :            ", MQTT_active)
print("MQTT discovery active:   ", MQTT_discovery_active)
print("daemon active:           ", daemon_active)
print("scan frequency:          ", scan_frequency, "sec")

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

def get_config_label(section, option, fallback):
    value = config.get(section, option, fallback=fallback)
    if value == "":
        return fallback
    return value

# MQTT discovery helper - sanitize labels from config.cfg before using them
# in Home Assistant identifiers, display names, and model fields.
def clean_name(txt):
    txt = str(txt).strip()
    txt = txt.replace(" ", "_")
    txt = re.sub(r'[^A-Za-z0-9_]', '', txt)
    if txt == "":
        return "Unknown"
    return txt

device_list=[          # used in main loop - HUB port labels from config.cfg
    get_config_label('Labels', 'port_1', 'Port1'),
    get_config_label('Labels', 'port_2', 'Port2'),
    get_config_label('Labels', 'port_3', 'Port3'),
    get_config_label('Labels', 'port_4', 'Port4'),
    get_config_label('Labels', 'port_5', 'Port5'),
    get_config_label('Labels', 'port_6', 'Port6'),
    get_config_label('Labels', 'port_7', 'Port7'),
    get_config_label('Labels', 'port_8', 'Port8')
    ]

shunt_list=[           # used in main loop - FLEXnet-DC shunt labels from config.cfg
    get_config_label('Labels', 'shunt_a', 'Solar'),
    get_config_label('Labels', 'shunt_b', 'Invertor'),
    get_config_label('Labels', 'shunt_c', 'Diverter')
    ]

# FLEXnet-DC shunt roles from config.cfg.
# The labels above are free text for display only. The FNDC does not report what a shunt is wired to,
# so the calculated summary needs to be told: a shunt measuring a diversion load and one measuring an
# inverter look identical over ModBus.
SHUNT_SOURCE_ROLES = ("solar", "charger")                 # current normally flows into the battery
SHUNT_SINK_ROLES   = ("inverter", "load", "diverter")     # current normally flows out of the battery
SHUNT_OTHER_ROLES  = ("unused", "other")                  # not reported as a total of its own
SHUNT_ROLES        = SHUNT_SOURCE_ROLES + SHUNT_SINK_ROLES + SHUNT_OTHER_ROLES

def get_shunt_role(option):
    role = config.get('Labels', option, fallback='').strip().lower()
    if role == "":
        return None
    if role not in SHUNT_ROLES:
        logger.warning("Unknown " + option + " '" + role + "' in config.cfg. Valid roles: " + ", ".join(SHUNT_ROLES))
        return None
    return role

shunt_role_list=[      # used in main loop - FLEXnet-DC shunt roles from config.cfg
    get_shunt_role('shunt_a_role'),
    get_shunt_role('shunt_b_role'),
    get_shunt_role('shunt_c_role')
    ]

# Before roles were configurable the summary assumed shunt C was a diversion load, which is wrong on
# any system that uses shunt C for something else. Installations that have not set any role keep the
# old behaviour so their existing Home Assistant sensors keep working.
if not any(role is not None for role in shunt_role_list):
    shunt_role_list = [None, None, 'diverter']
    shunt_roles_configured = False
else:
    shunt_roles_configured = True

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

# SunSpec blocks that are actually decoded below. Blocks that the MATE3 reports but that are not in
# this set are listed in the log at the end of every scan, so an unsupported device shows up as a
# clear message instead of being skipped in silence.
handled_blocks = {
    "Split Phase Radian Inverter Real Time Block",
    "Single Phase Radian Inverter Real Time Block",
    "Radian Inverter Configuration Block",
    "FX Inverter Real Time Block",
    "FX Inverter Configuration Block",
    "Charge Controller Block",
    "Charge Controller Configuration block",
    "FLEXnet-DC Real Time Block",
    "FLEXnet-DC Configuration Block"
}

# Real time block names that carry an inverter, used to warn when a scan finds no inverter at all.
inverter_blocks = {
    "Split Phase Radian Inverter Real Time Block",
    "Single Phase Radian Inverter Real Time Block",
    "FX Inverter Real Time Block"
}

# FX/VFX series lookup tables - AXS_APP_NOTE.PDF tables 13 to 17.
# The error and warning bits are the same as the Radian ones, but the FX enums are not: the FX
# charger has an extra 'Auto' mode and the FX AC input type has three values instead of seven.
fx_error_flags = [
    (0x0001, 'Low AC output voltage'),
    (0x0002, 'Stacking error'),
    (0x0004, 'Over temperature error'),
    (0x0008, 'Low battery voltage'),
    (0x0010, 'Phase loss'),
    (0x0020, 'High battery voltage'),
    (0x0040, 'AC output shorted'),
    (0x0080, 'AC backfeed')
]

fx_warning_flags = [
    (0x0001, 'AC input frequency too high'),
    (0x0002, 'AC input frequency too low'),
    (0x0004, 'AC input voltage too low'),
    (0x0008, 'AC input voltage too high'),
    (0x0010, 'AC input current exceeds max'),
    (0x0020, 'Temperature sensor bad'),
    (0x0040, 'Communications error'),
    (0x0080, 'Cooling fan fault')
]

# FX_Inverter_Operating_Mode. Same enumeration as the Radian real time blocks, so the values
# published to Home Assistant are identical on FX and Radian hardware.
fx_operating_modes_list = [
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

fx_ac_use_list    = ["AC Drop", "AC Use"]           # FX_AC_Input_State
fx_aux_relay_list = ["disabled", "enabled"]         # FX_AUX_Output_State

# FXconfig_AC_Input_Type. Published as grid_input_mode so that it shares the Radian topic and reuses
# the Radian wording for the modes the two families have in common.
fx_grid_input_mode_list = ["GridTied", "Generator", "GridZero"]

# FXconfig_Charger_Operating_Mode
fx_charger_mode_list = ["Off", "Auto", "On"]

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

def to_int16(register):
    """
    Strict two's complement conversion for registers the AXS application note declares as int16.

    decode_int16() above deliberately works around a FLEXnet-DC firmware quirk and therefore cannot
    represent small negative numbers - 0xFFFF comes back as 0 instead of -1. Scale factors and the FX
    temperature registers do use small negative values, so they are converted here instead.
    """
    return register - 65536 if register > 32767 else register

def sunspec_scale(register, default):
    """
    Convert a SunSpec scale factor register into a multiplier, e.g. a scale factor of -1 gives 0.1.

    The FX blocks carry their own scale factors, so FX values are scaled with what the hardware
    reports rather than with hardcoded constants. OutBack warn that the blocks may change between
    firmware releases, so an implausible scale factor falls back to `default` and is logged.
    """
    scale_factor = to_int16(register)
    if not -10 <= scale_factor <= 10:
        logger.warning(".... SunSpec scale factor out of range (" + str(scale_factor) + "), using default " + str(default))
        scale_factor = default
    return 10 ** scale_factor

def decode_enum(value, names, description):
    """
    Look up an enumerated register value, without raising when the MATE3 reports an unexpected one.
    """
    if 0 <= value < len(names):
        return names[value]
    logger.warning(".... Unexpected " + description + " value " + str(value))
    return "Unknown (" + str(value) + ")"

def decode_flags(value, flags, none_text='Nothing'):
    """
    Decode a SunSpec bitfield register into readable text.

    Several bits can be set at the same time, so every flag that is set is reported.
    """
    if value == 0:
        return none_text
    set_flags = [text for bit, text in flags if value & bit]
    if not set_flags:
        logger.warning(".... Unexpected bitfield value " + str(value))
        return "Unknown (" + str(value) + ")"
    return ', '.join(set_flags)

def port_label(port):
    """
    HUB port label from config.cfg, tolerating a port number outside the configured range.
    """
    if 0 <= port < len(device_list):
        return device_list[port]
    logger.warning(".... No label configured for HUB port " + str(port + 1))
    return "Port" + str(port + 1)

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
    blockname = mate3_did.get(blockID)
    if blockname is None:
        logger.warning("Unknown SunSpec device type with DID=" + str(blockID) + " at register " + str(basereg) + ". The scan stops here")
    return {"size": blocksize, "DID": blockname}

#------------------------------------------------
#  MATE3 ModBus connection helper
#------------------------------------------------
# A new connection is opened at the beginning of every scan cycle and closed at
# the end of that cycle. This keeps daemon mode resilient after temporary MATE3
# or network errors. In run-once mode the same cycle logic is used only once.
client   = None
startReg = None

def connect_mate3():
    global client, startReg

    try:
        logger.debug("Building MATE3 MODBUS connection")
        client = ModbusClient(mate3_ip, port=mate3_modbus)
        client.connect()

        logger.debug(".. Make sure we are indeed connected to an Outback power system")
        reg  = sunspec_start_reg
        size = getSunSpec(reg)

        if size is None:
            logger.warning(".. MATE3 unavailable or SunSpec not detected. Retrying next cycle")
            try:
                client.close()
            except Exception:
                pass
            client = None
            return False

        startReg = reg + size + 4
        logger.debug(".. Connected OK to an Outback system")
        return True

    except Exception as e:
        logger.warning(".. Failed to connect to MATE3. Enable SUNSPEC and check port. Retrying next cycle: " + str(e))
        try:
            if client is not None:
                client.close()
        except Exception:
            pass
        client = None
        return False

#This is the main loop
#--------------------------------------------------------------

script_start_time = datetime.now()

# MQTT discovery state - discovery must be published only once after the first
# successful Mate3 scan, because the real client configuration is known only
# after reading the SunSpec blocks.
mqtt_discovery_done = False


# Sensor ids per inverter family. Every inverter family uses the same Home Assistant device name,
# outback_inverter_n, so the sensors behind that device depend on which family is fitted. These are
# named here rather than inline below so the union can be worked out for the retraction step.
INVERTER_SENSOR_IDS = ["inverter_current", "charge_current", "buy_current", "sell_current", "battery_voltage", "battery_voltage_compensated", "ac_input", "ac_output", "ac_use", "operating_modes", "aux_relay", "error_flags", "warning_modes", "trafo_temp", "capacitor_temp", "fet_temp", "grid_input_mode", "charger_mode"]
SPLIT_INVERTER_SENSOR_IDS = ["inverter_L1_current", "charge_L1_current", "buy_L1_current", "sell_L1_current", "inverter_L2_current", "charge_L2_current", "buy_L2_current", "sell_L2_current", "battery_voltage", "battery_voltage_compensated", "ac_input_L1", "ac_output_L1", "ac_input_L2", "ac_output_L2", "ac_use", "operating_modes", "aux_relay", "error_flags", "warning_modes", "trafo_L_temp", "capacitor_L_temp", "fet_L_temp", "trafo_R_temp", "capacitor_R_temp", "fet_R_temp", "grid_input_mode", "charger_mode"]

# Every sensor id any inverter family can publish, used to clear the ones this system does not have.
INVERTER_SENSOR_IDS_ALL = sorted(set(INVERTER_SENSOR_IDS + SPLIT_INVERTER_SENSOR_IDS))

# MQTT discovery retraction.
# Discovery configs are published retained, so the broker keeps serving them until they are replaced
# or cleared. A sensor that stops being applicable - a different inverter family, a shunt role that
# is no longer configured - would otherwise stay in Home Assistant forever, showing a stale value.
# Publishing an empty retained payload to its config topic clears the broker copy and tells Home
# Assistant to delete the entity. This runs on every discovery, so no state has to be kept and no
# information has to be read back from the broker.
def retract_discovery(dev_name, stale_ids, MQTT_auth):
    for stale_id in sorted(stale_ids):
        config_topic = "homeassistant/sensor/" + dev_name + "/" + dev_name + "_" + stale_id + "/config"
        publish.single(config_topic, "", hostname=MQTT_broker, port=MQTT_port, auth=MQTT_auth, qos=0, retain=True)
        logger.debug(" HA sensor retracted: " + dev_name + "_" + stale_id)

# MQTT Home Assistant discovery.
# Creates retained MQTT discovery entities after the first successful Mate3 scan.
# Devices are created only from real devices detected during the SunSpec scan:
#   - Outback Inverter n
#   - Outback Charger n
#   - Outback FNDC
#   - Outback Summary  (calculated totals)
#   - Outback System   (script/internal values)
# The function publishes discovery config only; real sensor values are published
# later through the normal MQTT data path.
def publish_mqtt_discovery(detected_devices, MQTT_auth):

    if MQTT_active != 'true' or MQTT_discovery_active != 'true':
        return

    manufacturer = "Outback Power"
    sw_ver       = script_ver

    # Summary discovery flags are set while processing detected devices.
    # This keeps the logic simple and avoids scanning detected_devices twice.
    summary_charger        = False
    summary_fndc           = False
    summary_inverter       = False
    summary_split_inverter = False

    # Process each detected hardware device and publish its own HA sensors.
    for dev in detected_devices:

        msg      = {}
        dev_type = dev["type"]
        dev_idx  = dev["index"]
        model    = clean_name(dev["name"])

        # -------------------------------------------------
        # FLEXnet-DC battery monitor sensors
        # -------------------------------------------------
        if dev_type == "fndc":

            summary_fndc = True

            dev_name     = "outback_fndc"
            display_name = "Outback FNDC"
            topic_prefix = "outback/fndc"

            # The shunt sensors are displayed under the label configured for them, so Home Assistant
            # shows 'Solar current' rather than 'shunt_b_current'. Only the display name uses the
            # label - the ids below still drive uniq_id and the state topic, so renaming a shunt in
            # config.cfg does not create a duplicate entity or break existing automations.
            names        = ["battery_voltage", "state_of_charge", "battery_temperature", shunt_list[0] + " current", shunt_list[1] + " current", shunt_list[2] + " current", "charge_params_met", "today_min_soc", "today_max_soc", "days_since_charge_met", "today_net_input_ah", "today_net_output_ah", "todays_net_input_kWh", "todays_net_output_kWh", "min_voltage", "max_voltage"]
            ids          = ["battery_voltage", "state_of_charge", "battery_temperature", "shunt_a_current", "shunt_b_current", "shunt_c_current", "charge_params_met", "today_min_soc", "today_max_soc", "days_since_charge_met", "today_net_input_ah", "today_net_output_ah", "todays_net_input_kWh", "todays_net_output_kWh", "min_voltage", "max_voltage"]
            dev_cla      = ["voltage", "battery", "temperature", "current", "current", "current", None, "battery", "battery", None, None, None, "energy", "energy", "voltage", "voltage"]
            stat_cla     = ["measurement", "measurement", "measurement", "measurement", "measurement", "measurement", None, "measurement", "measurement", "measurement", "measurement", "measurement", "total_increasing", "total_increasing", "measurement", "measurement"]
            unit_of_meas = ["V", "%", "°C", "A", "A", "A", None, "%", "%", "d", "Ah", "Ah", "kWh", "kWh", "V", "V"]

        # -------------------------------------------------
        # Charge controller sensors (FM60 / FM80)
        # -------------------------------------------------
        elif dev_type == "charger":

            summary_charger = True

            dev_name     = "outback_charger_" + str(dev_idx)
            display_name = "Outback Charger " + str(dev_idx)
            topic_prefix = "outback/chargers/" + str(dev_idx)

            names        = ["charger_current", "pv_current", "pv_voltage", "pv_power", "aux", "aux_mode", "error_modes", "battery_voltage", "daily_ah", "daily_kwh", "charge_mode"]
            ids          = ["charger_current", "pv_current", "pv_voltage", "pv_power", "aux", "aux_mode", "error_modes", "battery_voltage", "daily_ah", "daily_kwh", "charge_mode"]
            dev_cla      = ["current", "current", "voltage", "power", None, None, None, "voltage", None, "energy", None]
            stat_cla     = ["measurement", "measurement", "measurement", "measurement", None, None, None, "measurement", "measurement", "total_increasing", None]
            unit_of_meas = ["A", "A", "V", "W", None, None, None, "V", "Ah", "kWh", None]

        # -------------------------------------------------
        # Single phase inverter sensors
        # -------------------------------------------------
        elif dev_type == "inverter":

            summary_inverter = True

            dev_name     = "outback_inverter_" + str(dev_idx)
            display_name = "Outback Inverter " + str(dev_idx)
            topic_prefix = "outback/inverters/" + str(dev_idx)

            names        = INVERTER_SENSOR_IDS
            ids          = INVERTER_SENSOR_IDS
            dev_cla      = ["current", "current", "current", "current", "voltage", "voltage", "voltage", "voltage", None, None, None, None, None, "temperature", "temperature", "temperature", None, None]
            stat_cla     = ["measurement", "measurement", "measurement", "measurement", "measurement", "measurement", "measurement", "measurement", None, None, None, None, None, "measurement", "measurement", "measurement", None, None]
            unit_of_meas = ["A", "A", "A", "A", "V", "V", "V", "V", None, None, None, None, None, "°C", "°C", "°C", None, None]

        # -------------------------------------------------
        # FX / VFX series inverter sensors
        # -------------------------------------------------
        # Same device name, topic prefix and sensor names as the single phase Radian inverter above,
        # so Home Assistant automations are portable between the two families. The FX real time block
        # additionally reports daily energy counters, which the Radian blocks do not have, so those
        # are published only for FX hardware rather than being added to the shared list.
        elif dev_type == "fx_inverter":

            summary_inverter = True

            dev_name     = "outback_inverter_" + str(dev_idx)
            display_name = "Outback Inverter " + str(dev_idx)
            topic_prefix = "outback/inverters/" + str(dev_idx)

            names        = ["inverter_current", "charge_current", "buy_current", "sell_current", "battery_voltage", "battery_voltage_compensated", "ac_input", "ac_output", "ac_use", "operating_modes", "aux_relay", "error_flags", "warning_modes", "trafo_temp", "capacitor_temp", "fet_temp", "grid_input_mode", "charger_mode", "output_kwh", "buy_kwh", "sell_kwh", "charger_kwh"]
            ids          = ["inverter_current", "charge_current", "buy_current", "sell_current", "battery_voltage", "battery_voltage_compensated", "ac_input", "ac_output", "ac_use", "operating_modes", "aux_relay", "error_flags", "warning_modes", "trafo_temp", "capacitor_temp", "fet_temp", "grid_input_mode", "charger_mode", "output_kwh", "buy_kwh", "sell_kwh", "charger_kwh"]
            dev_cla      = ["current", "current", "current", "current", "voltage", "voltage", "voltage", "voltage", None, None, None, None, None, "temperature", "temperature", "temperature", None, None, "energy", "energy", "energy", "energy"]
            stat_cla     = ["measurement", "measurement", "measurement", "measurement", "measurement", "measurement", "measurement", "measurement", None, None, None, None, None, "measurement", "measurement", "measurement", None, None, "total_increasing", "total_increasing", "total_increasing", "total_increasing"]
            unit_of_meas = ["A", "A", "A", "A", "V", "V", "V", "V", None, None, None, None, None, "°C", "°C", "°C", None, None, "kWh", "kWh", "kWh", "kWh"]

        # -------------------------------------------------
        # Split phase inverter sensors (L1 / L2)
        # -------------------------------------------------
        elif dev_type == "split_inverter":

            summary_split_inverter = True

            dev_name     = "outback_inverter_" + str(dev_idx)
            display_name = "Outback Inverter " + str(dev_idx)
            topic_prefix = "outback/inverters/" + str(dev_idx)

            names        = SPLIT_INVERTER_SENSOR_IDS
            ids          = SPLIT_INVERTER_SENSOR_IDS
            dev_cla      = ["current", "current", "current", "current", "current", "current", "current", "current", "voltage", "voltage", "voltage", "voltage", "voltage", "voltage", None, None, None, None, None, "temperature", "temperature", "temperature", "temperature", "temperature", "temperature", None, None]
            stat_cla     = ["measurement", "measurement", "measurement", "measurement", "measurement", "measurement", "measurement", "measurement", "measurement", "measurement", "measurement", "measurement", "measurement", "measurement", None, None, None, None, None, "measurement", "measurement", "measurement", "measurement", "measurement", "measurement", None, None]
            unit_of_meas = ["A", "A", "A", "A", "A", "A", "A", "A", "V", "V", "V", "V", "V", "V", None, None, None, None, None, "°C", "°C", "°C", "°C", "°C", "°C", None, None]

        else:
            continue

        # An inverter device carries different sensors depending on its family, so anything this
        # family does not publish is cleared. The other device types have a fixed sensor set.
        if dev_type in ("inverter", "split_inverter"):
            retract_discovery(dev_name, set(INVERTER_SENSOR_IDS_ALL) - set(ids), MQTT_auth)

        for n in range(len(ids)):

            msg["uniq_id"] = dev_name + "_" + ids[n]
            state_topic    = "homeassistant/sensor/" + dev_name + "/" + msg["uniq_id"] + "/config"

            msg["name"]   = names[n]
            msg["stat_t"] = topic_prefix + "/" + ids[n]

            if dev_cla[n] is not None:
                msg["dev_cla"] = dev_cla[n]

            if stat_cla[n] is not None:
                msg["stat_cla"] = stat_cla[n]

            if unit_of_meas[n] is not None:
                msg["unit_of_meas"] = unit_of_meas[n]

            msg["dev"] = {
                "identifiers"  : [dev_name],
                "manufacturer" : manufacturer,
                "model"        : model,
                "name"         : display_name,
                "sw_version"   : sw_ver
            }

            message = json.dumps(msg)

            publish.single(state_topic, message, hostname=MQTT_broker, port=MQTT_port, auth=MQTT_auth, qos=0, retain=True)

            msg = {}

    # -------------------------------------------------
    # Summary sensors
    # -------------------------------------------------
    # Summary discovery publishes calculated totals under the Outback Summary
    # device. The flags below were set while processing detected devices, so
    # only sensors that make sense for the current system are created.

    dev_name     = "outback_summary"
    display_name = "Outback Summary"
    model        = "Summary"

    names        = []
    ids          = []
    topics       = []
    dev_cla      = []
    stat_cla     = []
    unit_of_meas = []

    catalogue    = []

    # Append one calculated Summary sensor definition to the discovery lists.
    # Every sensor is recorded in the catalogue whether or not it applies to this system, so the
    # retraction below always knows the full set this script can publish. Passing 'active' rather
    # than wrapping the calls in an if keeps the catalogue complete by construction - a sensor
    # cannot be added without the retraction learning about it.
    def add_summary_sensor(name, device_class, state_class, unit, active=True):
        catalogue.append(name)
        if not active:
            return
        names.append(name)
        ids.append(name)
        topics.append("outback/summary/" + name)
        dev_cla.append(device_class)
        stat_cla.append(state_class)
        unit_of_meas.append(unit)

    add_summary_sensor("pv_total_power",        "power",   "measurement",      "W",   active=summary_charger)
    add_summary_sensor("pv_daily_kwh",          "energy",  "total_increasing", "kWh", active=summary_charger)
    add_summary_sensor("pv_total_current",      "current", "measurement",      "A",   active=summary_charger)
    add_summary_sensor("chargers_total_current", "current", "measurement",     "A",   active=summary_charger)

    add_summary_sensor("battery_current",       "current", "measurement",      "A",   active=summary_fndc)
    add_summary_sensor("battery_power",         "power",   "measurement",      "W",   active=summary_fndc)
    add_summary_sensor("battery_in_power",      "power",   "measurement",      "W",   active=summary_fndc)
    add_summary_sensor("battery_out_power",     "power",   "measurement",      "W",   active=summary_fndc)

    # One sensor pair per shunt role. Every role is offered to the catalogue so that a role removed
    # from config.cfg has its sensors cleared on the next run.
    for role in SHUNT_SOURCE_ROLES + SHUNT_SINK_ROLES:
        role_active = summary_fndc and shunt_roles_configured and role in shunt_role_list
        add_summary_sensor("shunt_" + role + "_current", "current", "measurement", "A", active=role_active)
        add_summary_sensor("shunt_" + role + "_power",   "power",   "measurement", "W", active=role_active)

    diverter_active = summary_fndc and 'diverter' in shunt_role_list
    add_summary_sensor("diverted_current",      "current", "measurement",      "A",   active=diverter_active)
    add_summary_sensor("diverted_power",        "power",   "measurement",      "W",   active=diverter_active)

    add_summary_sensor("inverter_total_current", "current", "measurement",     "A", active=summary_inverter)
    add_summary_sensor("buy_total_current",      "current", "measurement",     "A", active=summary_inverter)
    add_summary_sensor("sell_total_current",     "current", "measurement",     "A", active=summary_inverter)
    add_summary_sensor("inverter_charge_total_current",   "current", "measurement",     "A", active=summary_inverter)

    # sell_total_power is valid for both single and split inverter systems.
    # For split systems it is the sum of L1 and L2 sell power.
    add_summary_sensor("sell_total_power",       "power",   "measurement",     "W", active=summary_inverter or summary_split_inverter)

    add_summary_sensor("inverter_L1_total_current", "current", "measurement",  "A", active=summary_split_inverter)
    add_summary_sensor("inverter_L2_total_current", "current", "measurement",  "A", active=summary_split_inverter)
    add_summary_sensor("inverter_L1_total_power",   "power",   "measurement",  "W", active=summary_split_inverter)
    add_summary_sensor("inverter_L2_total_power",   "power",   "measurement",  "W", active=summary_split_inverter)
    add_summary_sensor("buy_L1_total_current",      "current", "measurement",  "A", active=summary_split_inverter)
    add_summary_sensor("buy_L2_total_current",      "current", "measurement",  "A", active=summary_split_inverter)
    add_summary_sensor("buy_L1_total_power",        "power",   "measurement",  "W", active=summary_split_inverter)
    add_summary_sensor("buy_L2_total_power",        "power",   "measurement",  "W", active=summary_split_inverter)
    add_summary_sensor("sell_L1_total_current",     "current", "measurement",  "A", active=summary_split_inverter)
    add_summary_sensor("sell_L2_total_current",     "current", "measurement",  "A", active=summary_split_inverter)
    add_summary_sensor("sell_L1_total_power",       "power",   "measurement",  "W", active=summary_split_inverter)
    add_summary_sensor("sell_L2_total_power",       "power",   "measurement",  "W", active=summary_split_inverter)
    add_summary_sensor("charge_L1_total_current",   "current", "measurement",  "A", active=summary_split_inverter)
    add_summary_sensor("charge_L2_total_current",   "current", "measurement",  "A", active=summary_split_inverter)
    add_summary_sensor("charge_L1_total_power",     "power",   "measurement",  "W", active=summary_split_inverter)
    add_summary_sensor("charge_L2_total_power",     "power",   "measurement",  "W", active=summary_split_inverter)

    # Clear the summary sensors this system does not have, including any left behind by an earlier
    # run with different hardware or a different shunt role configuration.
    retract_discovery(dev_name, set(catalogue) - set(ids), MQTT_auth)

    for n in range(len(ids)):

        msg            = {}
        msg["uniq_id"] = dev_name + "_" + ids[n]
        state_topic    = "homeassistant/sensor/" + dev_name + "/" + msg["uniq_id"] + "/config"

        msg["name"]   = names[n]
        msg["stat_t"] = topics[n]

        if dev_cla[n] is not None:
            msg["dev_cla"] = dev_cla[n]

        if stat_cla[n] is not None:
            msg["stat_cla"] = stat_cla[n]

        if unit_of_meas[n] is not None:
            msg["unit_of_meas"] = unit_of_meas[n]

        msg["dev"] = {
            "identifiers"  : [dev_name],
            "manufacturer" : manufacturer,
            "model"        : model,
            "name"         : display_name,
            "sw_version"   : sw_ver
        }

        message = json.dumps(msg)

        publish.single(state_topic, message, hostname=MQTT_broker, port=MQTT_port, auth=MQTT_auth, qos=0, retain=True)

    # -------------------------------------------------
    # System sensors
    # -------------------------------------------------
    # Internal script/runtime metrics. These are not physical Mate3 registers.
    dev_name     = "outback_system"
    display_name = "Outback System"
    model        = "System"

    names        = ["uptime"]
    ids          = ["uptime"]
    topics       = ["outback/system/uptime"]
    dev_cla      = [None]
    stat_cla     = ["measurement"]
    unit_of_meas = ["d"]

    for n in range(len(ids)):

        msg            = {}
        msg["uniq_id"] = dev_name + "_" + ids[n]
        state_topic    = "homeassistant/sensor/" + dev_name + "/" + msg["uniq_id"] + "/config"

        msg["name"]   = names[n]
        msg["stat_t"] = topics[n]

        if dev_cla[n] is not None:
            msg["dev_cla"] = dev_cla[n]

        if stat_cla[n] is not None:
            msg["stat_cla"] = stat_cla[n]

        if unit_of_meas[n] is not None:
            msg["unit_of_meas"] = unit_of_meas[n]

        msg["dev"] = {
            "identifiers"  : [dev_name],
            "manufacturer" : manufacturer,
            "model"        : model,
            "name"         : display_name,
            "sw_version"   : sw_ver
        }

        message = json.dumps(msg)

        publish.single(state_topic, message, hostname=MQTT_broker, port=MQTT_port, auth=MQTT_auth, qos=0, retain=True)

def main():
    global mqtt_discovery_done, client, startReg

    if connect_mate3() == False:
        return False

    # MQTT discovery device list. Filled during the normal SunSpec scan; no
    # extra Modbus reads are made only for discovery.
    detected_devices   = []                           # used for MQTT discovery - detected devices from current Mate3 scan
    devices            = []                           # used for JSON file - list of data for all devices
    various            = []                           # used for JSON file - different data not connected with MateMonitoring project
    db_devices_values  = []                           # used for MariaDB upload - list of all data for all devices  
    db_devices_sql     = []                           # used for MariaDB upload - list of all data for all devices 
    mqtt_devices       = []                           # used for MQTT - list with topics and payloads 

    # Calculated summary values - aggregated during the normal SunSpec scan.
    pv_total_power             = 0      # total PV power from all charge controllers (W)
    pv_daily_kwh               = 0      # total PV energy today from all charge controllers (kWh)
    pv_total_current           = 0      # total PV input current from all charge controllers (A)
    chargers_total_current     = 0      # total battery charger current from all charge controllers (A)

    battery_current            = None   # net battery current calculated from FNDC shunts (A)
    battery_power              = None   # battery voltage x battery_current (W)
    battery_in_power           = None   # battery charging power only (W)
    battery_out_power          = None   # battery discharge power only (W)
    diverted_current           = None   # diversion load current, from the shunt(s) with the diverter role (A)
    diverted_power             = None   # diversion load power calculated from battery voltage and diverted_current (W)
    shunt_role_current         = {}     # net current per configured shunt role (A)
    shunt_role_power           = {}     # net power per configured shunt role (W)

    inverter_total_current     = 0      # total output current from single phase inverters (A)
    buy_total_current          = 0      # total AC buy/input current from single phase inverters (A)
    sell_total_current         = 0      # total AC sell/export current from single phase inverters (A)
    sell_total_power           = 0      # total AC sell/export power from single or split phase inverters (W)
    inverter_charge_total_current       = 0      # total inverter charger current from single phase inverters (A)

    inverter_L1_total_current  = 0      # total split phase inverter L1 output current (A)
    inverter_L2_total_current  = 0      # total split phase inverter L2 output current (A)
    inverter_L1_total_power    = 0      # total split phase inverter L1 output power (W)
    inverter_L2_total_power    = 0      # total split phase inverter L2 output power (W)
    buy_L1_total_current       = 0      # total split phase L1 buy/input current (A)
    buy_L2_total_current       = 0      # total split phase L2 buy/input current (A)
    buy_L1_total_power         = 0      # total split phase L1 buy/input power (W)
    buy_L2_total_power         = 0      # total split phase L2 buy/input power (W)
    sell_L1_total_current      = 0      # total split phase L1 sell/export current (A)
    sell_L2_total_current      = 0      # total split phase L2 sell/export current (A)
    sell_L1_total_power        = 0      # total split phase L1 sell/export power (W)
    sell_L2_total_power        = 0      # total split phase L2 sell/export power (W)
    charge_L1_total_current    = 0      # total split phase L1 charger current (A)
    charge_L2_total_current    = 0      # total split phase L2 charger current (A)
    charge_L1_total_power      = 0      # total split phase L1 charger power (W)
    charge_L2_total_power      = 0      # total split phase L2 charger power (W)
    
    start_run  = datetime.now() # used only for runtime calculation    
    
    curent_date_time = datetime.now()
    date_str         = curent_date_time.strftime("%Y-%m-%dT%H:%M:%S")
    date_sql         = datetime.now().replace(second=0, microsecond=0)   
    
    time={                                         # used for JSON file - servertime now
    "relay_local_time": date_str,
    "mate_local_time": date_str,
    "server_local_time": date_str}

    inverters=0                                    # used to count number of inverters detected
    chargers =0                                    # used to count number of chargers detected
    single_inverters=0                             # used to count single phase inverters for conditional summary JSON
    split_inverters=0                              # used to count split phase inverters for conditional summary JSON
    fndc_detected=False                            # used to include FNDC/battery values in summary JSON only when FNDC exists
    detected_blocks=[]                             # used to report the SunSpec blocks seen during this scan
    inverter_index_by_address={}                   # used to link a configuration block back to its own real time block
    port=None                                      # HUB port of the block being decoded, reported by the error handlers
    reg = startReg
    for block in range(0, 30):
        blockResult = getBlock(reg)
        if blockResult is None or blockResult.get('DID') is None:
            logger.warning(".. Failed to read SunSpec block. Cycle will retry later")
            break

        detected_blocks.append(blockResult['DID'])

        try:        
            if "Split Phase Radian Inverter Real Time Block" in blockResult['DID']:
                logger.debug(".. Detected a Split Phase Radian Inverter Real Time Block")
                inverters = inverters + 1
                split_inverters = split_inverters + 1
                response = client.read_holding_registers(reg + 2, count=1)
                port=(response.registers[0]-1)
                address=port+1
                logger.debug(".... Connected on HUB port " + str(response.registers[0]))

                # MQTT discovery - register detected split phase inverter with HUB port label.
                detected_devices.append({
                    "type"  : "split_inverter",
                    "index" : inverters,
                    "port"  : port + 1,
                    "name"  : device_list[port]
                })
                logger.debug(".... HA device: Outback Inverter " + str(inverters))
       
                # Inverter L1 phase data
                response = client.read_holding_registers(reg + 7, count=1)
                gs_single_inverter_output_current = round(response.registers[0],2)
                logger.debug(".... GS L1 Inverted output current (A) " + str(gs_single_inverter_output_current))
               
                response = client.read_holding_registers(reg + 8, count=1)
                gs_single_inverter_charge_current = round(response.registers[0],2)
                logger.debug(".... GS L1 Charger current (A) " + str(gs_single_inverter_charge_current))
                
                response = client.read_holding_registers(reg + 9, count=1)
                gs_single_inverter_buy_current = round(response.registers[0],2)
                logger.debug(".... GS L1 Input current (A) " + str(gs_single_inverter_buy_current))
                
                response = client.read_holding_registers(reg + 10, count=1)
                GS_Single_Inverter_Sell_Current = round(response.registers[0],2)
                logger.debug(".... GS L1 Sell current (A) " + str(GS_Single_Inverter_Sell_Current))

                response = client.read_holding_registers(reg + 11, count=1)
                gs_single_ac_input_voltage = round(response.registers[0],2)
                logger.debug(".... GS L1 AC Input Voltage " + str(gs_single_ac_input_voltage))

                response = client.read_holding_registers(reg + 13, count=1)
                gs_single_output_ac_voltage = round(response.registers[0],2)
                logger.debug(".... GS L1 Voltage Out (V) " + str(gs_single_output_ac_voltage))
                
                # Inverter L2 phase data
                response = client.read_holding_registers(reg + 14, count=1)
                gs_single_inverter_l2_output_current = round(response.registers[0],2)
                logger.debug(".... GS L2 Inverted output current (A) " + str(gs_single_inverter_l2_output_current))
               
                response = client.read_holding_registers(reg + 15, count=1)
                gs_single_inverter_charge_l2_current = round(response.registers[0],2)
                logger.debug(".... GS L2 Charger current (A) " + str(gs_single_inverter_charge_l2_current))
                
                response = client.read_holding_registers(reg + 16, count=1)
                gs_single_inverter_buy_l2_current = round(response.registers[0],2)
                logger.debug(".... GS L2 Buy current (A) " + str(gs_single_inverter_buy_l2_current))
                
                response = client.read_holding_registers(reg + 17, count=1)
                GS_Single_Inverter_Sell_l2_Current = round(response.registers[0],2)
                logger.debug(".... GS L2 Sell current (A) " + str(GS_Single_Inverter_Sell_l2_Current))

                response = client.read_holding_registers(reg + 18, count=1)
                gs_single_ac_input_l2_voltage = round(response.registers[0],2)
                logger.debug(".... GS L2 AC Input Voltage " + str(gs_single_ac_input_l2_voltage))

                response = client.read_holding_registers(reg + 20, count=1)
                gs_single_output_ac_l2_voltage = round(response.registers[0],2)
                logger.debug(".... GS L2 Voltage Out (V) " + str(gs_single_output_ac_l2_voltage))

                # Calculated summary - split phase inverter values are aggregated by phase.
                inverter_L1_total_current += gs_single_inverter_output_current
                inverter_L2_total_current += gs_single_inverter_l2_output_current
                buy_L1_total_current      += gs_single_inverter_buy_current
                buy_L2_total_current      += gs_single_inverter_buy_l2_current
                sell_L1_total_current     += GS_Single_Inverter_Sell_Current
                sell_L2_total_current     += GS_Single_Inverter_Sell_l2_Current
                charge_L1_total_current   += gs_single_inverter_charge_current
                charge_L2_total_current   += gs_single_inverter_charge_l2_current

                inverter_L1_total_power   += gs_single_inverter_output_current * gs_single_output_ac_voltage
                inverter_L2_total_power   += gs_single_inverter_l2_output_current * gs_single_output_ac_l2_voltage
                buy_L1_total_power        += gs_single_inverter_buy_current * gs_single_ac_input_voltage
                buy_L2_total_power        += gs_single_inverter_buy_l2_current * gs_single_ac_input_l2_voltage
                sell_L1_total_power       += GS_Single_Inverter_Sell_Current * gs_single_ac_input_voltage
                sell_L2_total_power       += GS_Single_Inverter_Sell_l2_Current * gs_single_ac_input_l2_voltage
                sell_total_power          += (GS_Single_Inverter_Sell_Current * gs_single_ac_input_voltage) + (GS_Single_Inverter_Sell_l2_Current * gs_single_ac_input_l2_voltage)
                charge_L1_total_power     += gs_single_inverter_charge_current * gs_single_ac_input_voltage
                charge_L2_total_power     += gs_single_inverter_charge_l2_current * gs_single_ac_input_l2_voltage

                response = client.read_holding_registers(reg + 21, count=1)
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
                logger.debug(".... GS Inverter Operating Mode " + str(gs_single_inverter_operating_mode) +" "+ operating_modes)  
                
                response = client.read_holding_registers(reg + 38, count=1)
                gs_single_ac_input_state = round(int(response.registers[0]),2)
                ac_use_list = [
                "AC Drop",
                "AC Use"
                ]
                ac_use = ac_use_list[gs_single_ac_input_state]
                logger.debug(".... GS AC USE (Y/N) " + str(gs_single_ac_input_state) + " " + ac_use)
                
                response = client.read_holding_registers(reg + 24, count=1)
                gs_single_battery_voltage = round(int(response.registers[0]) * 0.1,1)
                logger.debug(".... GS Battery voltage (V) " + str(gs_single_battery_voltage))
                
                response = client.read_holding_registers(reg + 25, count=1)
                gs_single_temp_compensated_target_voltage = round(int(response.registers[0]) * 0.1,2)
                logger.debug(".... GS Battery target voltage - temp compensated (V) " + str(gs_single_temp_compensated_target_voltage))

                response = client.read_holding_registers(reg + 26, count=1)
                GS_Single_AUX_Relay_Output_State = int(response.registers[0])
                logger.debug(".... GS Aux Relay state  " + str(GS_Single_AUX_Relay_Output_State))
                aux_relay_list=["disabled","enabled"]
                aux_relay=aux_relay_list[GS_Single_AUX_Relay_Output_State]

                response = client.read_holding_registers(reg + 28, count=1)
                GS_Single_L_Module_Transformer_Temperature = int(response.registers[0])
                logger.debug(".... GS L Transformer Temperature  " + str(GS_Single_L_Module_Transformer_Temperature))
                
                response = client.read_holding_registers(reg + 29, count=1)
                GS_Single_L_Module_Capacitor_Temperature = int(response.registers[0])
                logger.debug(".... GS L Capacitor Temperature  " + str(GS_Single_L_Module_Capacitor_Temperature))
  
                response = client.read_holding_registers(reg + 31, count=1)
                GS_Single_R_Module_FET_Temperature = int(response.registers[0])
                logger.debug(".... GS L FET Temperature  " + str(GS_Single_R_Module_FET_Temperature))                  

                response = client.read_holding_registers(reg + 32, count=1)
                GS_Single_R_Module_Transformer_Temperature = int(response.registers[0])
                logger.debug(".... GS L Transformer Temperature  " + str(GS_Single_R_Module_Transformer_Temperature))
                
                response = client.read_holding_registers(reg + 33, count=1)
                GS_Single_R_Module_Capacitor_Temperature = int(response.registers[0])
                logger.debug(".... GS L Capacitor Temperature  " + str(GS_Single_R_Module_Capacitor_Temperature))
 
                response = client.read_holding_registers(reg + 30, count=1)
                GS_Single_L_Module_FET_Temperature = int(response.registers[0])
                logger.debug(".... GS L FET Temperature  " + str(GS_Single_L_Module_FET_Temperature))    

                response = client.read_holding_registers(reg + 34, count=1)
                gs_single_battery_temperature = decode_int16(int(response.registers[0]))
                logger.debug(".... GS Battery temperature (V) " + str(gs_single_battery_temperature))
               
                response = client.read_holding_registers(reg + 22, count=1)
                GS_Split_Error_Flags = int(response.registers[0])
                logger.debug(".... GS Error Flags " + str(GS_Split_Error_Flags))
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
                
                response = client.read_holding_registers(reg + 23, count=1)
                GS_Single_Warning_Flags = int(response.registers[0])
                logger.debug(".... GS Warning Flags " + str(GS_Single_Warning_Flags))
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

                # GS data - JSON preparation
                devices_array={
                  "address": address,
                  "device_id": 5,
                  "inverter_L1_current": gs_single_inverter_output_current,
                  "buy_L1_current": gs_single_inverter_buy_current,
                  "charge_L1_current": gs_single_inverter_charge_current,
                  "ac_input_L1_voltage": gs_single_ac_input_voltage,
                  "ac_output_L1_voltage": gs_single_output_ac_voltage,
                  "sell_L1_current": GS_Single_Inverter_Sell_Current,
                  "inverter_L2_current": gs_single_inverter_l2_output_current,
                  "buy_L2_current": gs_single_inverter_buy_l2_current,
                  "charge_L2_current": gs_single_inverter_charge_l2_current,
                  "ac_input_L2_voltage": gs_single_ac_input_l2_voltage,
                  "ac_output_L2_voltage": gs_single_output_ac_l2_voltage,
                  "sell_L2_current": GS_Single_Inverter_Sell_l2_Current,                  
                  "operating_modes": operating_modes,
                  "trafo_L_temp": GS_Single_L_Module_Transformer_Temperature,
                  "capacitor_L_temp": GS_Single_L_Module_Capacitor_Temperature,
                  "fet_L_temperature": GS_Single_L_Module_FET_Temperature,
                  "trafo_R_temp": GS_Single_R_Module_Transformer_Temperature,
                  "capacitor_R_temp": GS_Single_R_Module_Capacitor_Temperature,
                  "fet_R_temperature": GS_Single_R_Module_FET_Temperature,
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
                
                # GS data - MQTT preparation   
                mqtt_devices.append({
                             "outback/inverters/" + str(inverters) + "/inverter_L1_current":gs_single_inverter_output_current,
                             "outback/inverters/" + str(inverters) + "/charge_L1_current"  :gs_single_inverter_charge_current,
                             "outback/inverters/" + str(inverters) + "/buy_L1_current"     :gs_single_inverter_buy_current,
                             "outback/inverters/" + str(inverters) + "/sell_L1_current"    :GS_Single_Inverter_Sell_Current,
                             "outback/inverters/" + str(inverters) + "/inverter_L2_current":gs_single_inverter_l2_output_current,
                             "outback/inverters/" + str(inverters) + "/charge_L2_current"  :gs_single_inverter_charge_l2_current,
                             "outback/inverters/" + str(inverters) + "/buy_L2_current"     :gs_single_inverter_buy_l2_current,
                             "outback/inverters/" + str(inverters) + "/sell_L2_current"    :GS_Single_Inverter_Sell_l2_Current,
                             "outback/inverters/" + str(inverters) + "/battery_voltage" :gs_single_battery_voltage,
                             "outback/inverters/" + str(inverters) + "/battery_voltage_compensated" :gs_single_temp_compensated_target_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_input_L1"        :gs_single_ac_input_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_output_L1"       :gs_single_output_ac_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_input_L2"        :gs_single_ac_input_l2_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_output_L2"       :gs_single_output_ac_l2_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_use"          :ac_use,
                             "outback/inverters/" + str(inverters) + "/operating_modes" :operating_modes,
                             "outback/inverters/" + str(inverters) + "/aux_relay"       :aux_relay,
                             "outback/inverters/" + str(inverters) + "/error_flags"     :error_flags,
                             "outback/inverters/" + str(inverters) + "/warning_modes"   :warning_flags,
                             "outback/inverters/" + str(inverters) + "/trafo_L_temp"      :GS_Single_L_Module_Transformer_Temperature,
                             "outback/inverters/" + str(inverters) + "/capacitor_L_temp"  :GS_Single_L_Module_Capacitor_Temperature,
                             "outback/inverters/" + str(inverters) + "/fet_L_temp"        :GS_Single_L_Module_FET_Temperature,
                             "outback/inverters/" + str(inverters) + "/trafo_R_temp"      :GS_Single_R_Module_Transformer_Temperature,
                             "outback/inverters/" + str(inverters) + "/capacitor_R_temp"  :GS_Single_R_Module_Capacitor_Temperature,
                             "outback/inverters/" + str(inverters) + "/fet_R_temp"        :GS_Single_R_Module_FET_Temperature                             
                             })
      
        except Exception as e:
            logger.warning("port: " + str(port) + " FXR module " + str(e))

        try:        
            if "Single Phase Radian Inverter Real Time Block" in blockResult['DID']:
                logger.debug(".. Detected a Single Phase Radian Inverter Real Time Block")
                inverters = inverters + 1
                single_inverters = single_inverters + 1
                response = client.read_holding_registers(reg + 2, count=1)
                port=(response.registers[0]-1)
                address=port+1
                logger.debug(".... Connected on HUB port " + str(response.registers[0]))

                # MQTT discovery - register detected inverter with HUB port label.
                detected_devices.append({
                    "type"  : "inverter",
                    "index" : inverters,
                    "port"  : port + 1,
                    "name"  : device_list[port]
                })
                logger.debug(".... HA device: Outback Inverter " + str(inverters))
       
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

                # Calculated summary - single phase inverter currents.
                inverter_total_current += gs_single_inverter_output_current
                buy_total_current      += gs_single_inverter_buy_current
                sell_total_current     += GS_Single_Inverter_Sell_Current
                sell_total_power       += GS_Single_Inverter_Sell_Current * gs_single_ac_input_voltage
                inverter_charge_total_current   += gs_single_inverter_charge_current
               
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
                  "operating_modes": operating_modes,
                  "trafo_temp": GS_Single_L_Module_Transformer_Temperature,
                  "capacitor_temp": GS_Single_L_Module_Capacitor_Temperature,
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
            if "FX Inverter Real Time Block" in blockResult['DID']:
                logger.debug(".. Detected a FX Inverter Real Time Block")
                inverters = inverters + 1
                # The FX/VFX family is single phase, so it feeds the same summary totals and the same
                # Home Assistant sensor names as the single phase Radian block above.
                single_inverters = single_inverters + 1

                # The whole block is fetched in one request instead of one request per register. The
                # AXS application note numbers the block from 1, so its register N is fx[N-1] here.
                response = client.read_holding_registers(reg, count=blockResult['size'] + 2)
                fx = response.registers

                port    = fx[2] - 1
                address = port + 1
                label   = port_label(port)
                logger.debug(".... Connected on HUB port " + str(fx[2]))

                # MQTT discovery - register detected FX inverter with HUB port label.
                detected_devices.append({
                    "type"  : "fx_inverter",
                    "index" : inverters,
                    "port"  : port + 1,
                    "name"  : label
                })
                inverter_index_by_address[address] = inverters
                logger.debug(".... HA device: Outback Inverter " + str(inverters))

                # Scale factors are read from the block rather than hardcoded, because OutBack
                # document them as firmware dependent. The defaults match the application note.
                dc_voltage_scale = sunspec_scale(fx[3],  -1)
                ac_current_scale = sunspec_scale(fx[4],   0)
                ac_voltage_scale = sunspec_scale(fx[5],   0)
                kwh_scale        = sunspec_scale(fx[27], -1)

                fx_inverter_output_current = round(fx[7] * ac_current_scale, 2)
                logger.debug(".... FX Inverted output current (A) " + str(fx_inverter_output_current))

                fx_inverter_charge_current = round(fx[8] * ac_current_scale, 2)
                logger.debug(".... FX Charger current (A) " + str(fx_inverter_charge_current))

                fx_inverter_buy_current = round(fx[9] * ac_current_scale, 2)
                logger.debug(".... FX Input current (A) " + str(fx_inverter_buy_current))

                fx_inverter_sell_current = round(fx[10] * ac_current_scale, 2)
                logger.debug(".... FX Sell current (A) " + str(fx_inverter_sell_current))

                fx_output_ac_voltage = round(fx[11] * ac_voltage_scale, 2)
                logger.debug(".... FX Voltage Out (V) " + str(fx_output_ac_voltage))

                fx_ac_input_voltage = round(fx[22] * ac_voltage_scale, 2)
                logger.debug(".... FX AC Input Voltage " + str(fx_ac_input_voltage))

                # Calculated summary - single phase inverter currents.
                inverter_total_current += fx_inverter_output_current
                buy_total_current      += fx_inverter_buy_current
                sell_total_current     += fx_inverter_sell_current
                sell_total_power       += fx_inverter_sell_current * fx_ac_input_voltage
                inverter_charge_total_current   += fx_inverter_charge_current

                operating_modes = decode_enum(fx[12], fx_operating_modes_list, "FX operating mode")
                logger.debug(".... FX Inverter Operating Mode " + str(fx[12]) + " " + operating_modes)

                error_flags = decode_flags(fx[13], fx_error_flags)
                logger.debug(".... FX Error Flags " + str(fx[13]) + " " + error_flags)

                warning_flags = decode_flags(fx[14], fx_warning_flags)
                logger.debug(".... FX Warning Flags " + str(fx[14]) + " " + warning_flags)

                fx_battery_voltage = round(fx[15] * dc_voltage_scale, 1)
                logger.debug(".... FX Battery voltage (V) " + str(fx_battery_voltage))

                fx_temp_compensated_target_voltage = round(fx[16] * dc_voltage_scale, 2)
                logger.debug(".... FX Battery target voltage - temp compensated (V) " + str(fx_temp_compensated_target_voltage))

                aux_relay = decode_enum(fx[17], fx_aux_relay_list, "FX aux relay state")
                logger.debug(".... FX Aux Relay state " + str(fx[17]) + " " + aux_relay)

                fx_transformer_temperature = to_int16(fx[18])
                logger.debug(".... FX Transformer Temperature " + str(fx_transformer_temperature))

                fx_capacitor_temperature = to_int16(fx[19])
                logger.debug(".... FX Capacitor Temperature " + str(fx_capacitor_temperature))

                fx_fet_temperature = to_int16(fx[20])
                logger.debug(".... FX FET Temperature " + str(fx_fet_temperature))

                ac_use = decode_enum(fx[23], fx_ac_use_list, "FX AC input state")
                logger.debug(".... FX AC USE (Y/N) " + str(fx[23]) + " " + ac_use)

                # Daily energy counters. The Radian blocks have no equivalent, so these are published
                # for FX hardware only and are not part of the shared inverter sensor set.
                fx_buy_kwh = round(fx[28] * kwh_scale, 2)
                logger.debug(".... FX Daily Buy (kWh) " + str(fx_buy_kwh))

                fx_sell_kwh = round(fx[29] * kwh_scale, 2)
                logger.debug(".... FX Daily Sell (kWh) " + str(fx_sell_kwh))

                fx_output_kwh = round(fx[30] * kwh_scale, 2)
                logger.debug(".... FX Daily Output (kWh) " + str(fx_output_kwh))

                fx_charger_kwh = round(fx[31] * kwh_scale, 2)
                logger.debug(".... FX Daily Charger (kWh) " + str(fx_charger_kwh))

                # FX data - JSON preparation
                # Field names match the single phase Radian block so that Home Assistant automations
                # and the JSON consumers work unchanged on either inverter family.
                devices_array={
                  "address": address,
                  "device_id": 5,
                  "inverter_current": fx_inverter_output_current,
                  "buy_current": fx_inverter_buy_current,
                  "charge_current": fx_inverter_charge_current,
                  "ac_input_voltage": fx_ac_input_voltage,
                  "ac_output_voltage": fx_output_ac_voltage,
                  "sell_current": fx_inverter_sell_current,
                  "operating_modes": operating_modes,
                  "trafo_temp": fx_transformer_temperature,
                  "capacitor_temp": fx_capacitor_temperature,
                  "fet_temperature": fx_fet_temperature,
                  "error_modes": [
                    error_flags
                  ],
                  "ac_mode": ac_use,
                  "battery_voltage":fx_battery_voltage,
                  "aux_relay":aux_relay,
                  "warning_modes": [
                    warning_flags
                  ],
                  "output_kwh": fx_output_kwh,
                  "buy_kwh": fx_buy_kwh,
                  "sell_kwh": fx_sell_kwh,
                  "charger_kwh": fx_charger_kwh,
                  "label":label}
                devices.append(devices_array)     # append FX data to devices

                # FX data - MariaDB SQL preparation
                # Reuses the existing monitormate_fx table and columns, so no schema change is needed.
                db_devices_values.append ((date_sql,address,5,fx_inverter_output_current,fx_inverter_charge_current,fx_inverter_buy_current,
                fx_ac_input_voltage,fx_output_ac_voltage,fx_inverter_sell_current,operating_modes,
                error_flags,ac_use,fx_battery_voltage,aux_relay,warning_flags))

                db_devices_sql.append ("INSERT INTO monitormate_fx \
                (date,address,device_id,inverter_current,charge_current,buy_current,ac_input_voltage,ac_output_voltage,\
                sell_current,operational_mode,error_modes,ac_mode,battery_voltage,misc,warning_modes) \
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")

                # FX data - MQTT preparation
                mqtt_devices.append({
                             "outback/inverters/" + str(inverters) + "/inverter_current":fx_inverter_output_current,
                             "outback/inverters/" + str(inverters) + "/charge_current"  :fx_inverter_charge_current,
                             "outback/inverters/" + str(inverters) + "/buy_current"     :fx_inverter_buy_current,
                             "outback/inverters/" + str(inverters) + "/sell_current"    :fx_inverter_sell_current,
                             "outback/inverters/" + str(inverters) + "/battery_voltage" :fx_battery_voltage,
                             "outback/inverters/" + str(inverters) + "/battery_voltage_compensated" :fx_temp_compensated_target_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_input"        :fx_ac_input_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_output"       :fx_output_ac_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_use"          :ac_use,
                             "outback/inverters/" + str(inverters) + "/operating_modes" :operating_modes,
                             "outback/inverters/" + str(inverters) + "/aux_relay"       :aux_relay,
                             "outback/inverters/" + str(inverters) + "/error_flags"     :error_flags,
                             "outback/inverters/" + str(inverters) + "/warning_modes"   :warning_flags,
                             "outback/inverters/" + str(inverters) + "/trafo_temp"      :fx_transformer_temperature,
                             "outback/inverters/" + str(inverters) + "/capacitor_temp"  :fx_capacitor_temperature,
                             "outback/inverters/" + str(inverters) + "/fet_temp"        :fx_fet_temperature,
                             "outback/inverters/" + str(inverters) + "/output_kwh"      :fx_output_kwh,
                             "outback/inverters/" + str(inverters) + "/buy_kwh"         :fx_buy_kwh,
                             "outback/inverters/" + str(inverters) + "/sell_kwh"        :fx_sell_kwh,
                             "outback/inverters/" + str(inverters) + "/charger_kwh"     :fx_charger_kwh
                             })

        except Exception as e:
            logger.warning("port: " + str(port) + " FX module " + str(e))

        try:
            if "FX Inverter Configuration Block" in blockResult['DID']:
                logger.debug(".. Detected a FX Inverter Configuration Block")

                response = client.read_holding_registers(reg, count=blockResult['size'] + 2)
                fxconfig = response.registers

                # The port is read from this block rather than reused from the previous one, so the
                # values always land on the inverter they belong to.
                fxconfig_address = fxconfig[2]
                fx_index = inverter_index_by_address.get(fxconfig_address)

                if fx_index is None:
                    logger.warning(".. FX Inverter Configuration Block on HUB port " + str(fxconfig_address) + " has no matching real time block. Skipped")
                else:
                    grid_input_mode = decode_enum(fxconfig[20], fx_grid_input_mode_list, "FX AC input type")
                    logger.debug(".... FX Grid input Mode " + str(fxconfig[20]) + " " + grid_input_mode)

                    charger_mode = decode_enum(fxconfig[25], fx_charger_mode_list, "FX charger operating mode")
                    logger.debug(".... FX Charger Mode " + str(fxconfig[25]) + " " + charger_mode)

                    # FX dataconfig - JSON preparation
                    various_array={
                      "address": fxconfig_address,
                      "device_id": 5,
                      "grid_input_mode": grid_input_mode,
                      "charger_mode": charger_mode
                      }
                    various.append(various_array)     # append FX data to devices

                    for device in devices:
                        if device.get("address") == fxconfig_address and device.get("device_id") == 5:
                            device["grid_input_mode"] = grid_input_mode
                            device["charger_mode"]    = charger_mode
                            break

                    # FX dataconfig - Mqtt preparation
                    mqtt_devices.append(
                            {"outback/inverters/" + str(fx_index) + "/grid_input_mode":grid_input_mode,
                             "outback/inverters/" + str(fx_index) + "/charger_mode"   :charger_mode})

        except Exception as e:
            logger.warning("port: " + str(port) + " FX config block " + str(e))

        try:
            if "Charge Controller Block" in blockResult['DID']:
                logger.debug(".. Detected a Charge Controller Block")
                chargers = chargers +1

                response = client.read_holding_registers(reg + 2, count=1)
                logger.debug(".... Connected on HUB port " + str(response.registers[0]))
                port=(response.registers[0]-1)
                address=port+1

                # MQTT discovery - register detected charge controller with HUB port label.
                detected_devices.append({
                    "type"  : "charger",
                    "index" : chargers,
                    "port"  : port + 1,
                    "name"  : device_list[port]
                })
                logger.debug(".... HA device: Outback " + str(device_list[port]).strip())
     
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
                
                response = client.read_holding_registers(reg + 13, count=1)
                CC_Watts = round(int(response.registers[0]),2)
                logger.debug(".... CC Actual_watts (W) " + str(CC_Watts))

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

                # Calculated summary - charger / PV totals.
                pv_total_power        += CC_Watts
                pv_daily_kwh          += CC_Todays_KW
                pv_total_current      += cc_array_current
                chargers_total_current += cc_batt_current
          
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
                  "charger_current": cc_batt_current,
                  "pv_current": cc_array_current,
                  "pv_voltage": cc_array_voltage,
                  "pv_power": CC_Watts,
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
                    "outback/chargers/" + str(chargers) + "/charger_current" :cc_batt_current,
                    "outback/chargers/" + str(chargers) + "/pv_current"     :cc_array_current,
                    "outback/chargers/" + str(chargers) + "/pv_voltage"     :cc_array_voltage,
                    "outback/chargers/" + str(chargers) + "/pv_power"       :CC_Watts,
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
                fndc_detected=True

                # MQTT discovery - register detected FLEXnet-DC with HUB port label.
                detected_devices.append({
                    "type"  : "fndc",
                    "index" : 1,
                    "port"  : port + 1,
                    "name"  : device_list[port]
                })
                logger.debug(".... HA device: Outback " + str(device_list[port]).strip())

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

                # Calculated summary - battery values based on FNDC shunts.
                # Every shunt the FNDC measures sits in a current path to or from the battery, so the
                # net battery current is their sum whatever each one is wired to. Shunts declared
                # unused are left out, so a disabled shunt reporting noise cannot skew the total.
                shunt_currents = [fn_shunt_a_current, fn_shunt_b_current, fn_shunt_c_current]

                battery_current  = round(sum(current for current, role in zip(shunt_currents, shunt_role_list)
                                             if role != 'unused') * -1, 2)
                battery_power    = round(fn_battery_voltage * battery_current, 0)
                battery_in_power = abs(battery_power) if battery_power < 0 else 0
                battery_out_power = battery_power if battery_power > 0 else 0

                # Per role totals, so a system can report what its shunts actually measure. Shunts
                # sharing a role are summed. Both the current and the power of a role are reported in
                # the direction that role normally runs: source roles positive while supplying the
                # battery, sink roles positive while drawing from it. The raw FNDC reading, in the
                # FNDC's own sign convention, stays available on the per shunt sensors.
                shunt_role_raw_current = {}
                shunt_role_current     = {}
                shunt_role_power       = {}
                for current, role in zip(shunt_currents, shunt_role_list):
                    if role not in SHUNT_SOURCE_ROLES + SHUNT_SINK_ROLES:
                        continue
                    shunt_role_raw_current[role] = round(shunt_role_raw_current.get(role, 0) + current, 2)

                for role, raw_current in shunt_role_raw_current.items():
                    direction = 1 if role in SHUNT_SOURCE_ROLES else -1
                    shunt_role_current[role] = round(raw_current * direction, 2)
                    shunt_role_power[role]   = round(fn_battery_voltage * raw_current * direction, 0)

                # diverted_current / diverted_power predate the role config, so they are still
                # published, but only when a shunt is actually declared as a diversion load.
                # diverted_current has always been the raw FNDC reading and keeps that convention,
                # so upgrading does not silently flip the sign of an existing sensor.
                if 'diverter' in shunt_role_raw_current:
                    diverted_current = shunt_role_raw_current['diverter']
                    diverted_power   = shunt_role_power['diverter']

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
                  "state_of_charge": fn_state_of_charge,
                  "shunt_enabled_a": Shunt_A_Enabled,
                  "shunt_enabled_b": Shunt_B_Enabled,
                  "shunt_enabled_c": Shunt_C_Enabled,
                  "charge_params_met": charge_params_met,
                  "relay_status": relay_status,
                  "relay_mode": relay_mode,
                  "battery_temperature": fn_battery_temperature,
                  "accumulated_ah_shunt_a": FN_Shunt_A_Accumulated_AH,
                  "accumulated_kwh_shunt_a": FN_Shunt_A_Accumulated_kWh,
                  "accumulated_ah_shunt_b": FN_Shunt_B_Accumulated_AH,
                  "accumulated_kwh_shunt_b": FN_Shunt_B_Accumulated_kWh,
                  "accumulated_ah_shunt_c": FN_Shunt_C_Accumulated_AH,
                  "accumulated_kwh_shunt_c": FN_Shunt_C_Accumulated_kWh,
                  "days_since_charge_met": FN_Days_Since_Charge_Parameters_Met,
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

                # Roles appear in the JSON only when they were actually set in config.cfg. An
                # installation that has not configured them keeps its previous output unchanged, and
                # the presence of these fields means a role was chosen rather than assumed.
                if shunt_roles_configured:
                    devices_array["shunt_a_role"] = shunt_role_list[0]
                    devices_array["shunt_b_role"] = shunt_role_list[1]
                    devices_array["shunt_c_role"] = shunt_role_list[2]

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
                     "outback/fndc/today_max_soc"        :FN_Todays_Maximum_SOC,
                     "outback/fndc/days_since_charge_met":FN_Days_Since_Charge_Parameters_Met,
                     "outback/fndc/today_net_input_ah"   :FN_Todays_NET_Input_AH,
                     "outback/fndc/today_net_output_ah"  :FN_Todays_NET_Output_AH,
                     "outback/fndc/todays_net_input_kWh" :FN_Todays_NET_Input_kWh,
                     "outback/fndc/todays_net_output_kWh":FN_Todays_NET_Output_kWh,
                     "outback/fndc/min_voltage"          :FN_Todays_Minimum_Battery_Voltage,
                     "outback/fndc/max_voltage"          :FN_Todays_Maximum_Battery_Voltage
                     })

        except Exception as e:
            logger.warning("port: " + str(port) + " FNDC module " + str(e))

        if "End of SunSpec" not in blockResult['DID']:
            reg = reg + blockResult['size'] + 2
        else:
            # Report what the scan found. A block that is present but not decoded used to be skipped
            # without a trace, which made unsupported hardware very hard to spot.
            logger.debug(" SunSpec blocks detected: " + ", ".join(detected_blocks))

            not_decoded = sorted({name for name in detected_blocks
                                  if name not in handled_blocks and name != "End of SunSpec"})
            if not_decoded:
                logger.debug(" SunSpec blocks present but not decoded by this script: " + ", ".join(not_decoded))

            if inverters == 0 and not any(name in inverter_blocks for name in detected_blocks):
                logger.warning(" No inverter real time block found. Blocks detected: " + ", ".join(detected_blocks))

            client.close()
            client = None
            mate_run = datetime.now()                                             
            running_time = round ((mate_run - start_run).total_seconds(),3)       
            logger.debug(" Mate connection closed")

            # MQTT discovery - publish Home Assistant device definitions once,
            # immediately after the first complete Mate3 scan.
            if MQTT_active == 'true' and MQTT_discovery_active == 'true' and mqtt_discovery_done == False:
                MQTT_auth = None
                if len(MQTT_username) > 0:
                    MQTT_auth = { 'username': MQTT_username, 'password': MQTT_password }

                logger.debug(" HA device: Outback Summary")
                logger.debug(" HA device: Outback System")
                publish_mqtt_discovery(detected_devices, MQTT_auth)
                mqtt_discovery_done = True
                logger.debug(" HA devices discovery completed")

            print("---------------------------------------------------------------------------")
            print(f"running time Mate:      {running_time:8.3f} sec")  
  
            break
  
    # MariaDB upload
    mariadb_run = datetime.now() 
    mydb = None
    mycursor = None
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
            
            if not fndc_detected:
                mydb.commit()
                logger.debug(" Summary of the day skipped - FNDC not detected")
            else:
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
            print(f"running time MariaDB:   {running_time:8.3f} sec")     

    except Exception as e:
        mariadb_run = datetime.now()
        logger.warning("MariaDB upload - " + str(e))

        if mycursor is not None:
            try:
                mycursor.close()
            except:
                pass

        if mydb is not None:
            try:
                mydb.close()
            except:
                pass

    # Summary data - JSON preparation
    # Only include values that are valid for the devices detected in this scan.
    date_now=curent_date_time.strftime("%Y-%m-%d") #current date
    summary={"date": date_now}

    # Charger / PV summary values
    if chargers > 0:
        summary["pv_total_power"]        = round(pv_total_power, 0)
        summary["pv_daily_kwh"]          = round(pv_daily_kwh, 2)
        summary["pv_total_current"]      = round(pv_total_current, 2)
        summary["chargers_total_current"] = round(chargers_total_current, 2)

    # FNDC / battery summary values
    if fndc_detected:
        summary["kwh_in"]            = FN_Todays_NET_Input_kWh
        summary["kwh_out"]           = FN_Todays_NET_Output_kWh
        summary["ah_in"]             = FN_Todays_NET_Input_AH
        summary["ah_out"]            = FN_Todays_NET_Output_AH
        summary["min_voltage"]       = FN_Todays_Minimum_Battery_Voltage
        summary["max_voltage"]       = FN_Todays_Maximum_Battery_Voltage
        summary["min_soc"]           = FN_Todays_Minimum_SOC
        summary["max_soc"]           = FN_Todays_Maximum_SOC
        summary["battery_current"]   = battery_current
        summary["battery_power"]     = battery_power
        summary["battery_in_power"]  = battery_in_power
        summary["battery_out_power"] = battery_out_power

        # One pair of values per shunt role in use, so nothing is reported for a role the system
        # does not have. Installations that have not configured roles keep exactly the values they
        # had before, rather than gaining sensors derived from an assumed role.
        if shunt_roles_configured:
            for role in sorted(shunt_role_current):
                summary["shunt_" + role + "_current"] = shunt_role_current[role]
                summary["shunt_" + role + "_power"]   = shunt_role_power[role]

        if diverted_current is not None:
            summary["diverted_current"]  = diverted_current
            summary["diverted_power"]    = diverted_power

    # Single phase inverter summary values
    if single_inverters > 0:
        summary["inverter_total_current"] = round(inverter_total_current, 2)
        summary["buy_total_current"]      = round(buy_total_current, 2)
        summary["sell_total_current"]     = round(sell_total_current, 2)
        summary["inverter_charge_total_current"]   = round(inverter_charge_total_current, 2)

    # Split phase inverter summary values
    if split_inverters > 0:
        summary["inverter_L1_total_current"] = round(inverter_L1_total_current, 2)
        summary["inverter_L2_total_current"] = round(inverter_L2_total_current, 2)
        summary["inverter_L1_total_power"]   = round(inverter_L1_total_power, 0)
        summary["inverter_L2_total_power"]   = round(inverter_L2_total_power, 0)
        summary["buy_L1_total_current"]      = round(buy_L1_total_current, 2)
        summary["buy_L2_total_current"]      = round(buy_L2_total_current, 2)
        summary["buy_L1_total_power"]        = round(buy_L1_total_power, 0)
        summary["buy_L2_total_power"]        = round(buy_L2_total_power, 0)
        summary["sell_L1_total_current"]     = round(sell_L1_total_current, 2)
        summary["sell_L2_total_current"]     = round(sell_L2_total_current, 2)
        summary["sell_L1_total_power"]       = round(sell_L1_total_power, 0)
        summary["sell_L2_total_power"]       = round(sell_L2_total_power, 0)
        summary["charge_L1_total_current"]   = round(charge_L1_total_current, 2)
        summary["charge_L2_total_current"]   = round(charge_L2_total_current, 2)
        summary["charge_L1_total_power"]     = round(charge_L1_total_power, 0)
        summary["charge_L2_total_power"]     = round(charge_L2_total_power, 0)

    # Total sell power is calculated for both single and split inverter systems.
    # Single phase: sell_current * ac_input_voltage.
    # Split phase: sell_L1_total_power + sell_L2_total_power.
    if single_inverters > 0 or split_inverters > 0:
        summary["sell_total_power"] = round(sell_total_power, 0)

    # summary values - send data via MQTT
    # Calculated summary values are published under outback/summary.
    mqtt_devices.append({})
    for key, value in summary.items():
        if key != "date":
            mqtt_devices[-1]["outback/summary/" + key] = value

    #JSON serialisation and save
    try:
        json_data={"time":time, "devices":devices, "summary":summary, "various":various}
        with open(os.path.join(output_path, 'mate_status.json'), 'w') as outfile:
            json.dump(json_data, outfile)
        
        if duplicate_active == 'true':
            #print(duplicate_active)
            #shutil.copy(os.path.join(output_path, 'mate_status.json'), os.path.join(duplicate_path, 'mate_status.json'))      #copy the file in second location
            shutil.copy(os.path.join(output_path, 'mate_status.json'), os.path.join(duplicate_path, 'mate_status.json'))
        json_run = datetime.now()                                                           
        running_time = round ((json_run - mariadb_run).total_seconds(),3)                   
        print(f"running time JSON:      {running_time:8.3f} sec")                

    except Exception as e:
        logger.exception("JSON read/write")
        json_run = datetime.now()
        
    # MQTT system sensor - script uptime in days for Home Assistant.
    uptime = round((datetime.now() - script_start_time).total_seconds() / 86400, 3)
    mqtt_devices.append({
        "outback/system/uptime": uptime
        })

    # MQTT send data to MQTT broker
    try:
        if MQTT_active=='true':
            MQTT_auth = None 
            if len(MQTT_username) > 0:
                MQTT_auth = { 'username': MQTT_username, 'password': MQTT_password }

            messages = []
            for mqtt_data in mqtt_devices:
                for topic, payload in mqtt_data.items():
                    messages.append((topic, payload, 0, True))  # QoS=0, retain=True

            topic = "outback/mate" 
            payload = json.dumps(json_data)
            messages.append((topic, payload, 0, True))
            
            publish.multiple(messages, hostname=MQTT_broker, port=MQTT_port, auth=MQTT_auth)
        
        mqtt_run = datetime.now()                                                   
        running_time = round ((mqtt_run - json_run).total_seconds(),3)              
        print(f"running time MQTT:      {running_time:8.3f} sec")
        
        # script uptime 
        uptime = round((datetime.now() - script_start_time).total_seconds() / 86400, 3)
        print(f"script uptime:          {uptime:8.3f} day")

    except Exception as e:
        logger.exception("MQTT module")


# Main execution mode
# daemon_active = true  -> run continuously and wait scan_frequency seconds between scans
# daemon_active = false -> run once and exit; useful for cron / task scheduler
def close_mate3_safely():
    global client
    try:
        if client is not None:
            client.close()
    except Exception:
        pass
    client = None

if daemon_active == 'true':
    while True:
        try:
            main()
        except Exception:
            logger.exception("Main loop error")
            close_mate3_safely()

        tm.sleep(scan_frequency)
else:
    try:
        ok = main()
        if ok == False:
            logger.critical("Single run failed")
    except Exception:
        logger.exception("Main loop error")
        close_mate3_safely()

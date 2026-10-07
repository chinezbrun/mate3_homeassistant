#!/usr/bin/env python3
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
from sdc import SDC_BLOCKS  # SDC (SunSpec Data Configuration)

script_ver = "1.5.1_20261007"
print("script version: " + script_ver)

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
# Apply allowed key=value CLI overrides to the loaded configuration.
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
config                 = apply_cli_overrides(config)  # CLI overrides - temporary config overrides from key=value arguments

# MATE3 connection
mate3_ip               = config.get('MATE3 connection', 'mate3_ip')
mate3_modbus           = config.get('MATE3 connection', 'mate3_modbus')
sunspec_start_reg      = 40000

# MariaDB connection
SQL_active             = config.get('Maria DB connection', 'SQL_active')
host                   = config.get('Maria DB connection', 'host')
db_port                = config.get('Maria DB connection', 'db_port')
user                   = config.get('Maria DB connection', 'user')
password               = config.get('Maria DB connection', 'password')
database               = config.get('Maria DB connection', 'database')
output_path            = config.get('Path', 'output_path')
duplicate_active       = config.get('Path', 'duplicate_active')
duplicate_path         = config.get('Path', 'duplicate_path')

# Resolve output path.
if output_path == "":
    output_path = os.path.join(working_dir, 'data')

# MQTT configuration
MQTT_active                 = config.get('MQTT', 'MQTT_active')
MQTT_discovery_active       = config.get('MQTT', 'MQTT_discovery_active', fallback='true')
MQTT_discovery_cleanup      = config.get('MQTT', 'MQTT_discovery_cleanup', fallback='false')
MQTT_broker                 = config.get('MQTT', 'MQTT_broker')
MQTT_port                   = int(config.get('MQTT', 'MQTT_port'))
MQTT_username               = config.get('MQTT', 'MQTT_username')
MQTT_password               = config.get('MQTT', 'MQTT_password')
MQTT_availability_threshold = int(config.get('MQTT', 'MQTT_availability_threshold', fallback='0'))

# MATE3 status and Home Assistant availability.
MQTT_status_topic       = "outback/status"
MQTT_availability_topic = "outback/availability"
MQTT_payload_online     = "online"
MQTT_payload_offline    = "offline"

daemon_active          = config.get('General', 'daemon_active', fallback='false')

try:
    scan_frequency = int(config.get('General', 'scan_frequency', fallback='60'))
    if scan_frequency < 10:
        raise ValueError
except:
    print("Too low scan_frequency, fallback to 10 sec")
    scan_frequency = 10

LOGGING_LEVEL_FILE     = config.get('General', 'LOGGING_LEVEL_FILE')
LOGGING_FILE_MAX_SIZE  = int(config.get('General', 'LOGGING_FILE_MAX_SIZE'))
LOGGING_FILE_MAX_FILES = int(config.get('General', 'LOGGING_FILE_MAX_FILES'))

print("working directory:       ", working_dir)
print("output location:         ", output_path)
print("duplicate output active: ", duplicate_active)
print("SQL active  :            ", SQL_active)
print("MQTT active :            ", MQTT_active)
print("MQTT discovery active:   ", MQTT_discovery_active)
print("MQTT_discovery_cleanup:  ", MQTT_discovery_cleanup)
print("MQTT availability threshold:", MQTT_availability_threshold)
print("daemon active:           ", daemon_active)
print("scan frequency:          ", scan_frequency, "sec")

# Logger setup
logger = logging.getLogger("outback")
logger.setLevel(LOGGING_LEVEL_FILE)  # Setează nivelul minim de logare

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(LOGGING_LEVEL_FILE)
# Formatter
console_formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s', datefmt='%Y%m%d %H:%M:%S')
console_handler.setFormatter(console_formatter)

# File handler
# Build log file path.
log_path = os.path.join(working_dir, 'data', 'events_rms.log')
file_handler = RotatingFileHandler(log_path, mode='a', maxBytes=LOGGING_FILE_MAX_SIZE*1000, backupCount=LOGGING_FILE_MAX_FILES, encoding=None, delay=False)
file_handler.setLevel(LOGGING_LEVEL_FILE)

# Formatter
file_formatter = logging.Formatter('%(asctime)s| RMS |%(levelname)8s| %(message)s ', datefmt='%Y%m%d %H:%M:%S')
file_handler.setFormatter(file_formatter)

# Add handlers to logger.
logger.addHandler(console_handler)
logger.addHandler(file_handler)


# Return a configured label or its fallback when the value is empty.
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

device_list = [         # used in main loop - HUB port labels from config.cfg
    get_config_label('Labels', 'port_1', 'Port1'),
    get_config_label('Labels', 'port_2', 'Port2'),
    get_config_label('Labels', 'port_3', 'Port3'),
    get_config_label('Labels', 'port_4', 'Port4'),
    get_config_label('Labels', 'port_5', 'Port5'),
    get_config_label('Labels', 'port_6', 'Port6'),
    get_config_label('Labels', 'port_7', 'Port7'),
    get_config_label('Labels', 'port_8', 'Port8')
    ]

shunt_list = [          # used in main loop - FLEXnet-DC shunt labels from config.cfg
    get_config_label('Labels', 'shunt_a', 'Solar'),
    get_config_label('Labels', 'shunt_b', 'Invertor'),
    get_config_label('Labels', 'shunt_c', 'Diverter')
    ]

# FLEXnet-DC shunt roles used by calculated summary values.
# Labels are display-only; roles define what each shunt measures.
SHUNT_SOURCE_ROLES = ("solar", "charger")                 # current normally flows into the battery
SHUNT_SINK_ROLES   = ("inverter", "load", "diverter")     # current normally flows out of the battery
SHUNT_OTHER_ROLES  = ("unused", "other")                  # not reported as a total of its own
SHUNT_ROLES        = SHUNT_SOURCE_ROLES + SHUNT_SINK_ROLES + SHUNT_OTHER_ROLES


# Return a validated FLEXnet-DC shunt role from config.cfg.
def get_shunt_role(option):
    role = config.get('Labels', option, fallback='').strip().lower()
    if role == "":
        return None
    if role not in SHUNT_ROLES:
        logger.warning("Unknown " + option + " '" + role + "' in config.cfg. Valid roles: " + ", ".join(SHUNT_ROLES))
        return None
    return role

shunt_role_list = [     # used in main loop - FLEXnet-DC shunt roles from config.cfg
    get_shunt_role('shunt_a_role'),
    get_shunt_role('shunt_b_role'),
    get_shunt_role('shunt_c_role')
    ]

# Keep legacy shunt C diverter behavior when no roles are configured.
if not any(role is not None for role in shunt_role_list):
    shunt_role_list = [None, None, 'diverter']
    shunt_roles_configured = False
else:
    shunt_roles_configured = True

# Map SunSpec DIDs to OutBack block names (AXS_APP_NOTE.PDF).
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

# SunSpec blocks decoded by this script.
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

# Inverter real-time blocks used to detect missing inverter data.
inverter_blocks = {
    "Split Phase Radian Inverter Real Time Block",
    "Single Phase Radian Inverter Real Time Block",
    "FX Inverter Real Time Block"
}

# Minimal SunSpec register decoder.
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


# INT16 conversion with FLEXnet-DC firmware workaround.
def decode_int16(signed_value):

    if signed_value > 32768+2000:
        return signed_value - 65535
    elif signed_value >= 32768:
        return int(32768 - signed_value)
    else:
        return signed_value


# Convert INT16 registers using two's complement.
def to_int16(register):
    return register - 65536 if register > 32767 else register


# Convert a SunSpec scale factor to a multiplier.
def sunspec_scale(register, default):
    scale_factor = to_int16(register)
    if not -10 <= scale_factor <= 10:
        logger.warning(".... SunSpec scale factor out of range (" + str(scale_factor) + "), using default " + str(default))
        scale_factor = default
    return 10 ** scale_factor


# Return SDC (SunSpec Data Configuration) aliases when available.
def get_sdc_values(did, field_name):
    field = SDC_BLOCKS[did]["fields"][field_name]

    if field["aliases"]:
        return field["aliases"]
    else:
        return field["values"]


# Decode an enum using SDC (SunSpec Data Configuration).
def decode_enum(value, values, description):
    key = str(value)
    if key in values:
        return values[key]
    logger.warning(".... Unexpected " + description + " value " + str(value))
    return "Unknown (" + str(value) + ")"


# Decode a bitfield using SDC (SunSpec Data Configuration).
def decode_flags(value, flags, none_text='Nothing'):
    if value == 0:
        return none_text
    set_flags = [text for bit, text in flags.items() if value & int(bit, 16)]
    if not set_flags:
        logger.warning(".... Unexpected bitfield value " + str(value))
        return "Unknown (" + str(value) + ")"
    return ', '.join(set_flags)


# Return the configured HUB port label, with a fallback for ports outside the configured range.
def port_label(port):
    if 0 <= port < len(device_list):
        return device_list[port]
    logger.warning(".... No label configured for HUB port " + str(port + 1))
    return "Port" + str(port + 1)


# Convert decimal to binary string
def binary(decimal):
    otherBase = ""
    while decimal != 0:
        otherBase  =  str(decimal % 2) + otherBase
        decimal    //=  2
    return otherBase


# Read and return the SunSpec common information block.
def get_common_block(basereg):
    length   = 69
    response = client.read_holding_registers(basereg, count=(length + 2))
    decoder = SunSpecDecoder(response.registers)

    return {
        'SunSpec_ID'      : decoder.decode_32bit_uint(),
        'SunSpec_DID'     : decoder.decode_16bit_uint(),
        'SunSpec_Length'  : decoder.decode_16bit_uint(),
        'Manufacturer'    : decoder.decode_string(size=32),
        'Model'           : decoder.decode_string(size=32),
        'Options'         : decoder.decode_string(size=16),
        'Version'         : decoder.decode_string(size=16),
        'SerialNumber'    : decoder.decode_string(size=32),
        'DeviceAddress'   : decoder.decode_16bit_uint(),
        'Next_DID'        : decoder.decode_16bit_uint(),
        'Next_DID_Length' : decoder.decode_16bit_uint(),
    }


# Read SunSpec header.
def getSunSpec(basereg):
    # SunSpec header starts with 0x53756e53 (21365, 28243).
    try:
        response = client.read_holding_registers(basereg, count=2)
    except:
        return None

    if response.registers[0] == 21365 and response.registers[1] == 28243:
        logger.debug(".. SunSpec device found. Reading Manufacturer info")
    else:
        return None
    # Manufacturer string starts at basereg + 4.
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


# Read the SunSpec block header and return its size, name and numeric DID.
def getBlock(basereg):
    try:
        register = client.read_holding_registers(basereg)
    except:
        return None
    blockID = int(register.registers[0])
    # Read block size.
    try:
        register = client.read_holding_registers(basereg + 1)
    except:
        return None
    blocksize = int(register.registers[0])
    blockname = mate3_did.get(blockID)
    if blockname is None:
        logger.warning("Unknown SunSpec device type with DID=" + str(blockID) + " at register " + str(basereg) + ". The scan stops here")
    return {"size": blocksize, "DID": blockname, "id": blockID}

# MATE3 Modbus connection.
# Open a new connection for each scan cycle.
client   = None
startReg = None


# Connect to MATE3 and locate the first OutBack SunSpec block.
def connect_mate3():
    global client, startReg

    try:
        logger.debug(".. Building MATE3 Modbus connection")
        client = ModbusClient(mate3_ip, port=mate3_modbus)
        client.connect()

        logger.debug(".. Checking MATE3 SunSpec")
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
        logger.debug(".. MATE3 connected")
        return True

    except Exception as e:
        logger.warning(".. Failed to connect to MATE3. Enable SunSpec and check port. Retrying next cycle: " + str(e))
        try:
            if client is not None:
                client.close()
        except Exception:
            pass
        client = None
        return False

# Main loop
#--------------------------------------------------------------

script_start_time = datetime.now()

# Publish MQTT discovery once after the first successful MATE3 scan.
mqtt_discovery_done = False


# Sensor IDs by inverter family, used for discovery cleanup.
INVERTER_SENSOR_IDS = ["inverter_current", "charge_current", "buy_current", "sell_current", "battery_voltage", "battery_voltage_compensated", "ac_input", "ac_output", "ac_use", "operating_modes", "aux_relay", "error_flags", "warning_modes", "trafo_temp", "capacitor_temp", "fet_temp", "grid_input_mode", "charger_mode"]
FX_INVERTER_SENSOR_IDS = INVERTER_SENSOR_IDS + ["output_kwh", "buy_kwh", "sell_kwh", "charger_kwh"]
SPLIT_INVERTER_SENSOR_IDS = ["inverter_L1_current", "charge_L1_current", "buy_L1_current", "sell_L1_current", "inverter_L2_current", "charge_L2_current", "buy_L2_current", "sell_L2_current", "battery_voltage", "battery_voltage_compensated", "ac_input_L1", "ac_output_L1", "ac_input_L2", "ac_output_L2", "ac_use", "operating_modes", "aux_relay", "error_flags", "warning_modes", "trafo_L_temp", "capacitor_L_temp", "fet_L_temp", "trafo_R_temp", "capacitor_R_temp", "fet_R_temp", "grid_input_mode", "charger_mode"]

# All inverter sensor IDs that may require discovery cleanup.
INVERTER_SENSOR_IDS_ALL = sorted(set(INVERTER_SENSOR_IDS + FX_INVERTER_SENSOR_IDS + SPLIT_INVERTER_SENSOR_IDS))

# MQTT discovery cleanup.
# Empty retained payloads remove unused discovery and state topics.
# Cleanup is opt-in through MQTT_discovery_cleanup.
discovery_cleanup_hint_logged = False


# Last published MATE3 communication status and Home Assistant availability.
mate3_status            = None
mqtt_availability_state = None
mate3_failed_cycles     = 0


def publish_mqtt_state(topic, state):
    if MQTT_active != 'true':
        return False

    MQTT_auth = None
    if len(MQTT_username) > 0:
        MQTT_auth = { 'username': MQTT_username, 'password': MQTT_password }

    try:
        publish.single(topic, state, hostname=MQTT_broker, port=MQTT_port,
                       auth=MQTT_auth, qos=0, retain=True)
        return True
    except Exception:
        logger.exception("MQTT state publish")
        return False


# Remove stale Home Assistant discovery entities when cleanup is enabled.
def retract_discovery(dev_name, topic_prefix, stale_ids, MQTT_auth):
    if not stale_ids:
        return

    stale_ids = sorted(stale_ids)

    if MQTT_discovery_cleanup != 'true':
        # Whether any of these actually exist in Home Assistant is unknowable without reading the
        # broker back, which this deliberately does not do - on a new installation none of them ever
        # existed. So report what is not published rather than claiming anything was orphaned.
        global discovery_cleanup_hint_logged
        logger.debug(".... HA sensors not published for " + dev_name + ": " + ", ".join(stale_ids))
        if not discovery_cleanup_hint_logged:
            logger.info(" Unused HA sensors may exist. Set MQTT_discovery_cleanup=true to remove them")
            discovery_cleanup_hint_logged = True
        return

    for stale_id in stale_ids:
        # The config topic removes the entity, the state topic clears the value behind it. Without the
        # second one a re-enabled sensor would show its previous reading until the next scan.
        config_topic = "homeassistant/sensor/" + dev_name + "/" + dev_name + "_" + stale_id + "/config"
        state_topic  = topic_prefix + "/" + stale_id
        publish.single(config_topic, "", hostname=MQTT_broker, port=MQTT_port, auth=MQTT_auth, qos=0, retain=True)
        publish.single(state_topic,  "", hostname=MQTT_broker, port=MQTT_port, auth=MQTT_auth, qos=0, retain=True)
        logger.debug(".... HA sensor retracted: " + dev_name + "_" + stale_id)


# Publish MQTT discovery for devices detected during the MATE3 scan.
def publish_mqtt_discovery(detected_devices, MQTT_auth):

    if MQTT_active != 'true' or MQTT_discovery_active != 'true':
        return

    manufacturer = "Outback Power"
    sw_ver       = script_ver

    # Track which Summary sensors apply to detected hardware.
    summary_charger        = False
    summary_fndc           = False
    summary_inverter       = False
    summary_split_inverter = False

    # Publish HA sensors for each detected device.
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

            # Shunt labels affect display names only; IDs and topics stay stable.
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
        # FX/VFX uses the shared inverter topics plus its daily energy counters.
        elif dev_type == "fx_inverter":

            summary_inverter = True

            dev_name     = "outback_inverter_" + str(dev_idx)
            display_name = "Outback Inverter " + str(dev_idx)
            topic_prefix = "outback/inverters/" + str(dev_idx)

            names        = FX_INVERTER_SENSOR_IDS
            ids          = FX_INVERTER_SENSOR_IDS
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

        # Clear inverter sensors not used by the detected family.
        if dev_type in ("inverter", "fx_inverter", "split_inverter"):
            retract_discovery(dev_name, topic_prefix, set(INVERTER_SENSOR_IDS_ALL) - set(ids), MQTT_auth)

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

            msg["avty_t"] = MQTT_availability_topic

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
    # Publish calculated totals under OutBack Summary.

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

    # Add an active Summary sensor to discovery.
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

    add_summary_sensor("pv_total_power",                "power",   "measurement",      "W",   active=summary_charger)
    add_summary_sensor("pv_daily_kwh",                  "energy",  "total_increasing", "kWh", active=summary_charger)
    add_summary_sensor("pv_total_current",              "current", "measurement",      "A",   active=summary_charger)
    add_summary_sensor("chargers_total_current",        "current", "measurement",      "A",   active=summary_charger)

    add_summary_sensor("battery_current",               "current", "measurement",      "A",   active=summary_fndc)
    add_summary_sensor("battery_power",                 "power",   "measurement",      "W",   active=summary_fndc)
    add_summary_sensor("battery_in_power",              "power",   "measurement",      "W",   active=summary_fndc)
    add_summary_sensor("battery_out_power",             "power",   "measurement",      "W",   active=summary_fndc)

    # Add current and power sensors for configured shunt roles.
    for role in SHUNT_SOURCE_ROLES + SHUNT_SINK_ROLES:
        role_active = summary_fndc and shunt_roles_configured and role in shunt_role_list
        add_summary_sensor("shunt_" + role + "_current", "current", "measurement", "A", active=role_active)
        add_summary_sensor("shunt_" + role + "_power",   "power",   "measurement", "W", active=role_active)

    diverter_active = summary_fndc and 'diverter' in shunt_role_list
    add_summary_sensor("diverted_current",              "current", "measurement",      "A",   active=diverter_active)
    add_summary_sensor("diverted_power",                "power",   "measurement",      "W",   active=diverter_active)

    add_summary_sensor("inverter_total_current",        "current", "measurement",      "A",   active=summary_inverter)
    add_summary_sensor("buy_total_current",             "current", "measurement",      "A",   active=summary_inverter)
    add_summary_sensor("sell_total_current",            "current", "measurement",      "A",   active=summary_inverter)
    add_summary_sensor("inverter_charge_total_current", "current", "measurement",      "A",   active=summary_inverter)

    # Total sell power supports both single and split phase systems.
    add_summary_sensor("sell_total_power",              "power",   "measurement",      "W",   active=summary_inverter or summary_split_inverter)

    add_summary_sensor("inverter_L1_total_current",     "current", "measurement",      "A",   active=summary_split_inverter)
    add_summary_sensor("inverter_L2_total_current",     "current", "measurement",      "A",   active=summary_split_inverter)
    add_summary_sensor("inverter_L1_total_power",       "power",   "measurement",      "W",   active=summary_split_inverter)
    add_summary_sensor("inverter_L2_total_power",       "power",   "measurement",      "W",   active=summary_split_inverter)
    add_summary_sensor("buy_L1_total_current",          "current", "measurement",      "A",   active=summary_split_inverter)
    add_summary_sensor("buy_L2_total_current",          "current", "measurement",      "A",   active=summary_split_inverter)
    add_summary_sensor("buy_L1_total_power",            "power",   "measurement",      "W",   active=summary_split_inverter)
    add_summary_sensor("buy_L2_total_power",            "power",   "measurement",      "W",   active=summary_split_inverter)
    add_summary_sensor("sell_L1_total_current",         "current", "measurement",      "A",   active=summary_split_inverter)
    add_summary_sensor("sell_L2_total_current",         "current", "measurement",      "A",   active=summary_split_inverter)
    add_summary_sensor("sell_L1_total_power",           "power",   "measurement",      "W",   active=summary_split_inverter)
    add_summary_sensor("sell_L2_total_power",           "power",   "measurement",      "W",   active=summary_split_inverter)
    add_summary_sensor("charge_L1_total_current",       "current", "measurement",      "A",   active=summary_split_inverter)
    add_summary_sensor("charge_L2_total_current",       "current", "measurement",      "A",   active=summary_split_inverter)
    add_summary_sensor("charge_L1_total_power",         "power",   "measurement",      "W",   active=summary_split_inverter)
    add_summary_sensor("charge_L2_total_power",         "power",   "measurement",      "W",   active=summary_split_inverter)

    # Clear Summary sensors not used by the current configuration.
    retract_discovery(dev_name, "outback/summary", set(catalogue) - set(ids), MQTT_auth)

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

        msg["avty_t"] = MQTT_availability_topic

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
    # Internal script/runtime metrics, not physical MATE3 registers.
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

        msg["avty_t"] = MQTT_availability_topic

        msg["dev"] = {
            "identifiers"  : [dev_name],
            "manufacturer" : manufacturer,
            "model"        : model,
            "name"         : display_name,
            "sw_version"   : sw_ver
        }

        message = json.dumps(msg)

        publish.single(state_topic, message, hostname=MQTT_broker, port=MQTT_port, auth=MQTT_auth, qos=0, retain=True)


# Read the detected SunSpec blocks and build the current system data.
def main():
    global mqtt_discovery_done, client, startReg, mate3_status, mqtt_availability_state, mate3_failed_cycles

    if connect_mate3() == False:
        if mate3_status != MQTT_payload_offline:
            if publish_mqtt_state(MQTT_status_topic, MQTT_payload_offline):
                mate3_status = MQTT_payload_offline
                logger.info(" MATE3 status: " + MQTT_payload_offline)

        if daemon_active == 'true':
            if MQTT_availability_threshold > 0:
                mate3_failed_cycles += 1
                if mate3_failed_cycles <= MQTT_availability_threshold:
                    logger.info(" MATE3 communication failure: " + str(mate3_failed_cycles) + "/" + str(MQTT_availability_threshold) + " cycles")
                if mate3_failed_cycles == MQTT_availability_threshold and mqtt_availability_state != MQTT_payload_offline:
                    if publish_mqtt_state(MQTT_availability_topic, MQTT_payload_offline):
                        mqtt_availability_state = MQTT_payload_offline
                        logger.info(" MQTT availability: " + MQTT_payload_offline)
        else:
            # Single run: availability follows the MATE3 result immediately; no threshold.
            if mqtt_availability_state != MQTT_payload_offline:
                if publish_mqtt_state(MQTT_availability_topic, MQTT_payload_offline):
                    mqtt_availability_state = MQTT_payload_offline
                    logger.info(" MQTT availability: " + MQTT_payload_offline)
        return False

    # Build discovery data during the normal SunSpec scan.
    detected_devices   = []                           # used for MQTT discovery - detected devices from current MATE3 scan
    devices            = []                           # used for JSON file - list of data for all devices
    various            = []                           # used for JSON file - different data not connected with MateMonitoring project
    db_devices_values  = []                           # used for MariaDB upload - list of all data for all devices
    db_devices_sql     = []                           # used for MariaDB upload - list of all data for all devices
    mqtt_devices       = []                           # used for MQTT - list with topics and payloads

    # Calculated summary values from the current SunSpec scan.
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

    start_run = datetime.now()                  # used only for runtime calculation

    curent_date_time = datetime.now()
    date_str         = curent_date_time.strftime("%Y-%m-%dT%H:%M:%S")
    date_sql         = datetime.now().replace(second=0, microsecond=0)

    time = {                                      # used for JSON file - server time now
        "relay_local_time"  : date_str,
        "mate_local_time"   : date_str,
        "server_local_time" : date_str
    }

    inverters                 = 0      # used to count number of inverters detected
    chargers                  = 0      # used to count number of chargers detected
    single_inverters          = 0      # used to count single phase inverters for conditional summary JSON
    split_inverters           = 0      # used to count split phase inverters for conditional summary JSON
    fndc_detected             = False  # used to include FNDC/battery values in summary JSON only when FNDC exists
    detected_blocks           = []     # used to report the SunSpec blocks seen during this scan
    inverter_index_by_address = {}     # links inverter configuration blocks to real time blocks
    charger_index_by_address  = {}     # links charge controller configuration blocks to real time blocks
    charger_data_by_address   = {}     # stores charge controller real time values until its config block
    fndc_data_by_address      = {}     # stores FNDC real time values until its config block
    port                      = None   # HUB port of the block being decoded, reported by the error handlers
    reg = startReg
    for block in range(0, 30):
        blockResult = getBlock(reg)
        if blockResult is None or blockResult.get('DID') is None:
            logger.warning(".. Failed to read SunSpec block. Cycle will retry later")
            break

        detected_blocks.append(blockResult['DID'])

        try:
            if "Split Phase Radian Inverter Real Time Block" in blockResult['DID']:
                response = client.read_holding_registers(
                    reg,
                    count=blockResult["size"] + 2
                )
                radian_split = response.registers
                logger.debug(".. Detected a Split Phase Radian Inverter Real Time Block")
                inverters = inverters + 1
                split_inverters = split_inverters + 1
                port=(radian_split[2]-1)
                address=port+1
                logger.debug(".... Connected on HUB port " + str(radian_split[2]))

                # Register split phase inverter for MQTT discovery.
                detected_devices.append({
                    "type"  : "split_inverter",
                    "index" : inverters,
                    "port"  : port + 1,
                    "name"  : device_list[port]
                })
                logger.debug(".... HA device: Outback Inverter " + str(inverters))
                inverter_index_by_address[address] = inverters

                # Inverter L1 phase data
                gs_single_inverter_output_current = round(radian_split[7],2)
                logger.debug(".... GS L1 Inverted output current (A) " + str(gs_single_inverter_output_current))

                gs_single_inverter_charge_current = round(radian_split[8],2)
                logger.debug(".... GS L1 Charger current (A) " + str(gs_single_inverter_charge_current))

                gs_single_inverter_buy_current = round(radian_split[9],2)
                logger.debug(".... GS L1 Input current (A) " + str(gs_single_inverter_buy_current))

                GS_Single_Inverter_Sell_Current = round(radian_split[10],2)
                logger.debug(".... GS L1 Sell current (A) " + str(GS_Single_Inverter_Sell_Current))

                gs_single_ac_input_voltage = round(radian_split[11],2)
                logger.debug(".... GS L1 AC Input Voltage " + str(gs_single_ac_input_voltage))

                gs_single_output_ac_voltage = round(radian_split[13],2)
                logger.debug(".... GS L1 Voltage Out (V) " + str(gs_single_output_ac_voltage))

                # Inverter L2 phase data
                gs_single_inverter_l2_output_current = round(radian_split[14],2)
                logger.debug(".... GS L2 Inverted output current (A) " + str(gs_single_inverter_l2_output_current))

                gs_single_inverter_charge_l2_current = round(radian_split[15],2)
                logger.debug(".... GS L2 Charger current (A) " + str(gs_single_inverter_charge_l2_current))

                gs_single_inverter_buy_l2_current = round(radian_split[16],2)
                logger.debug(".... GS L2 Buy current (A) " + str(gs_single_inverter_buy_l2_current))

                GS_Single_Inverter_Sell_l2_Current = round(radian_split[17],2)
                logger.debug(".... GS L2 Sell current (A) " + str(GS_Single_Inverter_Sell_l2_Current))

                gs_single_ac_input_l2_voltage = round(radian_split[18],2)
                logger.debug(".... GS L2 AC Input Voltage " + str(gs_single_ac_input_l2_voltage))

                gs_single_output_ac_l2_voltage = round(radian_split[20],2)
                logger.debug(".... GS L2 Voltage Out (V) " + str(gs_single_output_ac_l2_voltage))

                # Aggregate split phase inverter values by phase.
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

                gs_single_inverter_operating_mode = int(radian_split[21])
                operating_modes = decode_enum(gs_single_inverter_operating_mode, get_sdc_values(blockResult["id"], "GS_Split_Inverter_Operating_mode"), "Radian operating mode")
                logger.debug(".... GS Inverter Operating Mode " + str(gs_single_inverter_operating_mode) +" "+ operating_modes)

                gs_single_ac_input_state = round(int(radian_split[38]),2)
                ac_use = decode_enum(gs_single_ac_input_state, get_sdc_values(blockResult["id"], "GS_Split_AC_Input_State"), "Radian AC input state")
                logger.debug(".... GS AC USE (Y/N) " + str(gs_single_ac_input_state) + " " + ac_use)

                gs_single_battery_voltage = round(int(radian_split[24]) * 0.1,1)
                logger.debug(".... GS Battery voltage (V) " + str(gs_single_battery_voltage))

                gs_single_temp_compensated_target_voltage = round(int(radian_split[25]) * 0.1,2)
                logger.debug(".... GS Battery target voltage - temp compensated (V) " + str(gs_single_temp_compensated_target_voltage))

                GS_Single_AUX_Relay_Output_State = int(radian_split[26])
                logger.debug(".... GS Aux Relay state  " + str(GS_Single_AUX_Relay_Output_State))
                aux_relay = decode_enum(GS_Single_AUX_Relay_Output_State, get_sdc_values(blockResult["id"], "GS_Split_AUX_Relay_Output_State"), "Radian aux relay state")

                GS_Single_L_Module_Transformer_Temperature = int(radian_split[28])
                logger.debug(".... GS L Transformer Temperature  " + str(GS_Single_L_Module_Transformer_Temperature))

                GS_Single_L_Module_Capacitor_Temperature = int(radian_split[29])
                logger.debug(".... GS L Capacitor Temperature  " + str(GS_Single_L_Module_Capacitor_Temperature))

                GS_Single_R_Module_FET_Temperature = int(radian_split[31])
                logger.debug(".... GS R FET Temperature  " + str(GS_Single_R_Module_FET_Temperature))

                GS_Single_R_Module_Transformer_Temperature = int(radian_split[32])
                logger.debug(".... GS R Transformer Temperature  " + str(GS_Single_R_Module_Transformer_Temperature))

                GS_Single_R_Module_Capacitor_Temperature = int(radian_split[33])
                logger.debug(".... GS R Capacitor Temperature  " + str(GS_Single_R_Module_Capacitor_Temperature))

                GS_Single_L_Module_FET_Temperature = int(radian_split[30])
                logger.debug(".... GS L FET Temperature  " + str(GS_Single_L_Module_FET_Temperature))

                gs_single_battery_temperature = decode_int16(int(radian_split[34]))
                logger.debug(".... GS Battery temperature " + str(gs_single_battery_temperature))

                GS_Split_Error_Flags = int(radian_split[22])
                logger.debug(".... GS Error Flags " + str(GS_Split_Error_Flags))
                error_flags = decode_flags(GS_Split_Error_Flags, get_sdc_values(blockResult["id"], "GS_Split_Error_Flags"))

                GS_Single_Warning_Flags = int(radian_split[23])
                logger.debug(".... GS Warning Flags " + str(GS_Single_Warning_Flags))
                warning_flags = decode_flags(GS_Single_Warning_Flags, get_sdc_values(blockResult["id"], "GS_Split_Warning_Flags"))

                # GS data - JSON preparation
                devices_array={
                  "address"              : address,
                  "device_id"            : 5,
                  "inverter_L1_current"  : gs_single_inverter_output_current,
                  "buy_L1_current"       : gs_single_inverter_buy_current,
                  "charge_L1_current"    : gs_single_inverter_charge_current,
                  "ac_input_L1_voltage"  : gs_single_ac_input_voltage,
                  "ac_output_L1_voltage" : gs_single_output_ac_voltage,
                  "sell_L1_current"      : GS_Single_Inverter_Sell_Current,
                  "inverter_L2_current"  : gs_single_inverter_l2_output_current,
                  "buy_L2_current"       : gs_single_inverter_buy_l2_current,
                  "charge_L2_current"    : gs_single_inverter_charge_l2_current,
                  "ac_input_L2_voltage"  : gs_single_ac_input_l2_voltage,
                  "ac_output_L2_voltage" : gs_single_output_ac_l2_voltage,
                  "sell_L2_current"      : GS_Single_Inverter_Sell_l2_Current,
                  "operating_modes"      : operating_modes,
                  "trafo_L_temp"         : GS_Single_L_Module_Transformer_Temperature,
                  "capacitor_L_temp"     : GS_Single_L_Module_Capacitor_Temperature,
                  "fet_L_temperature"    : GS_Single_L_Module_FET_Temperature,
                  "trafo_R_temp"         : GS_Single_R_Module_Transformer_Temperature,
                  "capacitor_R_temp"     : GS_Single_R_Module_Capacitor_Temperature,
                  "fet_R_temperature"    : GS_Single_R_Module_FET_Temperature,
                  "error_modes"          : [
                    error_flags
                  ],
                  "ac_mode"         : ac_use,
                  "battery_voltage" : gs_single_battery_voltage,
                  "aux_relay"       : aux_relay,
                  "warning_modes"   : [
                    warning_flags
                  ],
                  "label":device_list[port]}
                devices.append(devices_array)     # append FXR data to devices

                # GS data - MQTT preparation
                mqtt_devices.append({
                             "outback/inverters/" + str(inverters) + "/inverter_L1_current"         : gs_single_inverter_output_current,
                             "outback/inverters/" + str(inverters) + "/charge_L1_current"           : gs_single_inverter_charge_current,
                             "outback/inverters/" + str(inverters) + "/buy_L1_current"              : gs_single_inverter_buy_current,
                             "outback/inverters/" + str(inverters) + "/sell_L1_current"             : GS_Single_Inverter_Sell_Current,
                             "outback/inverters/" + str(inverters) + "/inverter_L2_current"         : gs_single_inverter_l2_output_current,
                             "outback/inverters/" + str(inverters) + "/charge_L2_current"           : gs_single_inverter_charge_l2_current,
                             "outback/inverters/" + str(inverters) + "/buy_L2_current"              : gs_single_inverter_buy_l2_current,
                             "outback/inverters/" + str(inverters) + "/sell_L2_current"             : GS_Single_Inverter_Sell_l2_Current,
                             "outback/inverters/" + str(inverters) + "/battery_voltage"             : gs_single_battery_voltage,
                             "outback/inverters/" + str(inverters) + "/battery_voltage_compensated" : gs_single_temp_compensated_target_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_input_L1"                 : gs_single_ac_input_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_output_L1"                : gs_single_output_ac_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_input_L2"                 : gs_single_ac_input_l2_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_output_L2"                : gs_single_output_ac_l2_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_use"                      : ac_use,
                             "outback/inverters/" + str(inverters) + "/operating_modes"             : operating_modes,
                             "outback/inverters/" + str(inverters) + "/aux_relay"                   : aux_relay,
                             "outback/inverters/" + str(inverters) + "/error_flags"                 : error_flags,
                             "outback/inverters/" + str(inverters) + "/warning_modes"               : warning_flags,
                             "outback/inverters/" + str(inverters) + "/trafo_L_temp"                : GS_Single_L_Module_Transformer_Temperature,
                             "outback/inverters/" + str(inverters) + "/capacitor_L_temp"            : GS_Single_L_Module_Capacitor_Temperature,
                             "outback/inverters/" + str(inverters) + "/fet_L_temp"                  : GS_Single_L_Module_FET_Temperature,
                             "outback/inverters/" + str(inverters) + "/trafo_R_temp"                : GS_Single_R_Module_Transformer_Temperature,
                             "outback/inverters/" + str(inverters) + "/capacitor_R_temp"            : GS_Single_R_Module_Capacitor_Temperature,
                             "outback/inverters/" + str(inverters) + "/fet_R_temp"                  : GS_Single_R_Module_FET_Temperature
                             })

        except Exception as e:
            logger.warning("port: " + str(port) + " FXR module " + str(e))

        try:
            if "Single Phase Radian Inverter Real Time Block" in blockResult['DID']:
                response = client.read_holding_registers(
                    reg,
                    count=blockResult["size"] + 2
                )
                radian_single = response.registers
                logger.debug(".. Detected a Single Phase Radian Inverter Real Time Block")
                inverters = inverters + 1
                single_inverters = single_inverters + 1
                port=(radian_single[2]-1)
                address=port+1
                logger.debug(".... Connected on HUB port " + str(radian_single[2]))

                # Register inverter for MQTT discovery.
                detected_devices.append({
                    "type"  : "inverter",
                    "index" : inverters,
                    "port"  : port + 1,
                    "name"  : device_list[port]
                })
                logger.debug(".... HA device: Outback Inverter " + str(inverters))
                inverter_index_by_address[address] = inverters

                # Inverter Output current
                gs_single_inverter_output_current = round(radian_single[7],2)
                logger.debug(".... FXR Inverted output current (A) " + str(gs_single_inverter_output_current))

                gs_single_inverter_charge_current = round(radian_single[8],2)
                logger.debug(".... FXR Charger current (A) " + str(gs_single_inverter_charge_current))

                gs_single_inverter_buy_current = round(radian_single[9],2)
                logger.debug(".... FXR Input current (A) " + str(gs_single_inverter_buy_current))

                gs_single_ac_input_voltage = round(radian_single[30],2)
                logger.debug(".... FXR AC Input Voltage " + str(gs_single_ac_input_voltage))

                gs_single_output_ac_voltage = round(radian_single[13],2)
                logger.debug(".... FXR Voltage Out (V) " + str(gs_single_output_ac_voltage))

                GS_Single_Inverter_Sell_Current = round(radian_single[10],2)
                logger.debug(".... FXR Sell current (A) " + str(GS_Single_Inverter_Sell_Current))

                # Aggregate single phase inverter currents.
                inverter_total_current += gs_single_inverter_output_current
                buy_total_current      += gs_single_inverter_buy_current
                sell_total_current     += GS_Single_Inverter_Sell_Current
                sell_total_power       += GS_Single_Inverter_Sell_Current * gs_single_ac_input_voltage
                inverter_charge_total_current   += gs_single_inverter_charge_current

                gs_single_inverter_operating_mode = int(radian_single[14])
                operating_modes = decode_enum(gs_single_inverter_operating_mode, get_sdc_values(blockResult["id"], "GS_Single_Inverter_Operating_mode"), "Radian operating mode")
                logger.debug(".... FXR Inverter Operating Mode " + str(gs_single_inverter_operating_mode) +" "+ operating_modes)

                gs_single_ac_input_state = round(int(radian_single[31]),2)
                ac_use = decode_enum(gs_single_ac_input_state, get_sdc_values(blockResult["id"], "GS_Single_AC_Input_State"), "Radian AC input state")
                logger.debug(".... FXR AC USE (Y/N) " + str(gs_single_ac_input_state) + " " + ac_use)

                gs_single_battery_voltage = round(int(radian_single[17]) * 0.1,1)
                logger.debug(".... FXR Battery voltage (V) " + str(gs_single_battery_voltage))

                gs_single_temp_compensated_target_voltage = round(int(radian_single[18]) * 0.1,2)
                logger.debug(".... FXR Battery target voltage - temp compensated (V) " + str(gs_single_temp_compensated_target_voltage))

                GS_Single_AUX_Relay_Output_State = int(radian_single[19])
                logger.debug(".... FXR Aux Relay state  " + str(GS_Single_AUX_Relay_Output_State))
                aux_relay = decode_enum(GS_Single_AUX_Relay_Output_State, get_sdc_values(blockResult["id"], "GS_Single_AUX_Relay_Output_State"), "Radian aux relay state")

                GS_Single_L_Module_Transformer_Temperature = int(radian_single[21])
                logger.debug(".... FXR L Transformer Temperature  " + str(GS_Single_L_Module_Transformer_Temperature))

                GS_Single_L_Module_Capacitor_Temperature = int(radian_single[22])
                logger.debug(".... FXR L Capacitor Temperature  " + str(GS_Single_L_Module_Capacitor_Temperature))

                GS_Single_L_Module_FET_Temperature = int(radian_single[23])
                logger.debug(".... FXR L FET Temperature  " + str(GS_Single_L_Module_FET_Temperature))

                gs_single_battery_temperature = decode_int16(int(radian_single[27]))
                logger.debug(".... FXR Battery temperature " + str(gs_single_battery_temperature))

                GS_Split_Error_Flags = int(radian_single[15])
                logger.debug(".... FXR Error Flags " + str(GS_Split_Error_Flags))
                error_flags = decode_flags(GS_Split_Error_Flags, get_sdc_values(blockResult["id"], "GS_Single_Error_Flags"))

                GS_Single_Warning_Flags = int(radian_single[16])
                logger.debug(".... FXR Warning Flags " + str(GS_Single_Warning_Flags))
                warning_flags = decode_flags(GS_Single_Warning_Flags, get_sdc_values(blockResult["id"], "GS_Single_Warning_Flags"))

                # FXR data - JSON preparation
                devices_array={
                  "address"           : address,
                  "device_id"         : 5,
                  "inverter_current"  : gs_single_inverter_output_current,
                  "buy_current"       : gs_single_inverter_buy_current,
                  "charge_current"    : gs_single_inverter_charge_current,
                  "ac_input_voltage"  : gs_single_ac_input_voltage,
                  "ac_output_voltage" : gs_single_output_ac_voltage,
                  "sell_current"      : GS_Single_Inverter_Sell_Current,
                  "operating_modes"   : operating_modes,
                  "trafo_temp"        : GS_Single_L_Module_Transformer_Temperature,
                  "capacitor_temp"    : GS_Single_L_Module_Capacitor_Temperature,
                  "fet_temperature"   : GS_Single_L_Module_FET_Temperature,
                  "error_modes"       : [
                    error_flags
                  ],
                  "ac_mode"         : ac_use,
                  "battery_voltage" : gs_single_battery_voltage,
                  "aux_relay"       : aux_relay,
                  "warning_modes"   : [
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
                             "outback/inverters/" + str(inverters) + "/inverter_current"            : gs_single_inverter_output_current,
                             "outback/inverters/" + str(inverters) + "/charge_current"              : gs_single_inverter_charge_current,
                             "outback/inverters/" + str(inverters) + "/buy_current"                 : gs_single_inverter_buy_current,
                             "outback/inverters/" + str(inverters) + "/sell_current"                : GS_Single_Inverter_Sell_Current,
                             "outback/inverters/" + str(inverters) + "/battery_voltage"             : gs_single_battery_voltage,
                             "outback/inverters/" + str(inverters) + "/battery_voltage_compensated" : gs_single_temp_compensated_target_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_input"                    : gs_single_ac_input_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_output"                   : gs_single_output_ac_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_use"                      : ac_use,
                             "outback/inverters/" + str(inverters) + "/operating_modes"             : operating_modes,
                             "outback/inverters/" + str(inverters) + "/aux_relay"                   : aux_relay,
                             "outback/inverters/" + str(inverters) + "/error_flags"                 : error_flags,
                             "outback/inverters/" + str(inverters) + "/warning_modes"               : warning_flags,
                             "outback/inverters/" + str(inverters) + "/trafo_temp"                  : GS_Single_L_Module_Transformer_Temperature,
                             "outback/inverters/" + str(inverters) + "/capacitor_temp"              : GS_Single_L_Module_Capacitor_Temperature,
                             "outback/inverters/" + str(inverters) + "/fet_temp"                    : GS_Single_L_Module_FET_Temperature
                             })

        except Exception as e:
            logger.warning("port: " + str(port) + " FXR module " + str(e))

        try:
            if "Radian Inverter Configuration Block" in blockResult['DID']:
                response = client.read_holding_registers(
                    reg,
                    count=blockResult["size"] + 2
                )
                radian_config = response.registers
                config_address = int(radian_config[2])
                radian_index = inverter_index_by_address.get(config_address)

                if radian_index is None:
                    logger.warning(".. Radian Inverter Configuration Block on HUB port " + str(config_address) + " has no matching real time block. Skipped")
                else:
                    GSconfig_Grid_Input_Mode = int(radian_config[26])
                    grid_input_mode = decode_enum(GSconfig_Grid_Input_Mode, get_sdc_values(blockResult["id"], "GSconfig_Grid_Input_Mode"), "Radian grid input mode")
                    logger.debug(".... FXR Grid input Mode " + str(GSconfig_Grid_Input_Mode) + " " + grid_input_mode)

                    GSconfig_Charger_Operating_Mode = int(radian_config[24])
                    charger_mode = decode_enum(GSconfig_Charger_Operating_Mode, get_sdc_values(blockResult["id"], "GSconfig_Charger_Operating_Mode"), "Radian charger operating mode")
                    logger.debug(".... FXR Charger Mode " + str(GSconfig_Charger_Operating_Mode) + " " + charger_mode)

                    various_array={
                      "address"         : config_address,
                      "device_id"       : 5,
                      "grid_input_mode" : grid_input_mode,
                      "charger_mode"    : charger_mode
                      }
                    various.append(various_array)

                    for device in devices:
                        if device.get("address") == config_address and device.get("device_id") == 5:
                            device["grid_input_mode"] = grid_input_mode
                            device["charger_mode"] = charger_mode
                            break

                    mqtt_devices.append(
                            {"outback/inverters/" + str(radian_index) + "/grid_input_mode":grid_input_mode,
                             "outback/inverters/" + str(radian_index) + "/charger_mode"   :charger_mode})

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

                # Aggregate single phase inverter currents.
                inverter_total_current += fx_inverter_output_current
                buy_total_current      += fx_inverter_buy_current
                sell_total_current     += fx_inverter_sell_current
                sell_total_power       += fx_inverter_sell_current * fx_ac_input_voltage
                inverter_charge_total_current   += fx_inverter_charge_current

                operating_modes = decode_enum(fx[12], get_sdc_values(blockResult["id"], "FX_Inverter_Operating_Mode"), "FX operating mode")
                logger.debug(".... FX Inverter Operating Mode " + str(fx[12]) + " " + operating_modes)

                error_flags = decode_flags(fx[13], get_sdc_values(blockResult["id"], "FX_Error_Flags"))
                logger.debug(".... FX Error Flags " + str(fx[13]) + " " + error_flags)

                warning_flags = decode_flags(fx[14], get_sdc_values(blockResult["id"], "FX_Warning_Flags"))
                logger.debug(".... FX Warning Flags " + str(fx[14]) + " " + warning_flags)

                fx_battery_voltage = round(fx[15] * dc_voltage_scale, 1)
                logger.debug(".... FX Battery voltage (V) " + str(fx_battery_voltage))

                fx_temp_compensated_target_voltage = round(fx[16] * dc_voltage_scale, 2)
                logger.debug(".... FX Battery target voltage - temp compensated (V) " + str(fx_temp_compensated_target_voltage))

                aux_relay = decode_enum(fx[17], get_sdc_values(blockResult["id"], "FX_AUX_Output_State"), "FX aux relay state")
                logger.debug(".... FX Aux Relay state " + str(fx[17]) + " " + aux_relay)

                fx_transformer_temperature = to_int16(fx[18])
                logger.debug(".... FX Transformer Temperature " + str(fx_transformer_temperature))

                fx_capacitor_temperature = to_int16(fx[19])
                logger.debug(".... FX Capacitor Temperature " + str(fx_capacitor_temperature))

                fx_fet_temperature = to_int16(fx[20])
                logger.debug(".... FX FET Temperature " + str(fx_fet_temperature))

                ac_use = decode_enum(fx[23], get_sdc_values(blockResult["id"], "FX_AC_Input_State"), "FX AC input state")
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
                  "address"           : address,
                  "device_id"         : 5,
                  "inverter_current"  : fx_inverter_output_current,
                  "buy_current"       : fx_inverter_buy_current,
                  "charge_current"    : fx_inverter_charge_current,
                  "ac_input_voltage"  : fx_ac_input_voltage,
                  "ac_output_voltage" : fx_output_ac_voltage,
                  "sell_current"      : fx_inverter_sell_current,
                  "operating_modes"   : operating_modes,
                  "trafo_temp"        : fx_transformer_temperature,
                  "capacitor_temp"    : fx_capacitor_temperature,
                  "fet_temperature"   : fx_fet_temperature,
                  "error_modes"       : [
                    error_flags
                  ],
                  "ac_mode"         : ac_use,
                  "battery_voltage" : fx_battery_voltage,
                  "aux_relay"       : aux_relay,
                  "warning_modes"   : [
                    warning_flags
                  ],
                  "output_kwh"  : fx_output_kwh,
                  "buy_kwh"     : fx_buy_kwh,
                  "sell_kwh"    : fx_sell_kwh,
                  "charger_kwh" : fx_charger_kwh,
                  "label"       : label}
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
                             "outback/inverters/" + str(inverters) + "/inverter_current"            : fx_inverter_output_current,
                             "outback/inverters/" + str(inverters) + "/charge_current"              : fx_inverter_charge_current,
                             "outback/inverters/" + str(inverters) + "/buy_current"                 : fx_inverter_buy_current,
                             "outback/inverters/" + str(inverters) + "/sell_current"                : fx_inverter_sell_current,
                             "outback/inverters/" + str(inverters) + "/battery_voltage"             : fx_battery_voltage,
                             "outback/inverters/" + str(inverters) + "/battery_voltage_compensated" : fx_temp_compensated_target_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_input"                    : fx_ac_input_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_output"                   : fx_output_ac_voltage,
                             "outback/inverters/" + str(inverters) + "/ac_use"                      : ac_use,
                             "outback/inverters/" + str(inverters) + "/operating_modes"             : operating_modes,
                             "outback/inverters/" + str(inverters) + "/aux_relay"                   : aux_relay,
                             "outback/inverters/" + str(inverters) + "/error_flags"                 : error_flags,
                             "outback/inverters/" + str(inverters) + "/warning_modes"               : warning_flags,
                             "outback/inverters/" + str(inverters) + "/trafo_temp"                  : fx_transformer_temperature,
                             "outback/inverters/" + str(inverters) + "/capacitor_temp"              : fx_capacitor_temperature,
                             "outback/inverters/" + str(inverters) + "/fet_temp"                    : fx_fet_temperature,
                             "outback/inverters/" + str(inverters) + "/output_kwh"                  : fx_output_kwh,
                             "outback/inverters/" + str(inverters) + "/buy_kwh"                     : fx_buy_kwh,
                             "outback/inverters/" + str(inverters) + "/sell_kwh"                    : fx_sell_kwh,
                             "outback/inverters/" + str(inverters) + "/charger_kwh"                 : fx_charger_kwh
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
                    grid_input_mode = decode_enum(fxconfig[20], get_sdc_values(blockResult["id"], "FXconfig_AC_Input_Type"), "FX AC input type")
                    logger.debug(".... FX Grid input Mode " + str(fxconfig[20]) + " " + grid_input_mode)

                    charger_mode = decode_enum(fxconfig[25], get_sdc_values(blockResult["id"], "FXconfig_Charger_Operating_Mode"), "FX charger operating mode")
                    logger.debug(".... FX Charger Mode " + str(fxconfig[25]) + " " + charger_mode)

                    # FX dataconfig - JSON preparation
                    various_array={
                      "address"         : fxconfig_address,
                      "device_id"       : 5,
                      "grid_input_mode" : grid_input_mode,
                      "charger_mode"    : charger_mode
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
                response = client.read_holding_registers(
                    reg,
                    count=blockResult["size"] + 2
                )
                charger = response.registers
                logger.debug(".. Detected a Charge Controller Block")
                chargers = chargers +1

                logger.debug(".... Connected on HUB port " + str(charger[2]))
                port=(charger[2]-1)
                address=port+1

                # MQTT discovery - register detected charge controller with HUB port label.
                detected_devices.append({
                    "type"  : "charger",
                    "index" : chargers,
                    "port"  : port + 1,
                    "name"  : device_list[port]
                })
                logger.debug(".... HA device: Outback " + str(device_list[port]).strip())

                cc_batt_current = round(int(charger[10]) * 0.1,2)    # correction value *0.1
                logger.debug(".... CC Battery Current (A) " + str(cc_batt_current))

                cc_array_current = round(int(charger[11]),2)
                logger.debug(".... CC Array Current (A) " + str(cc_array_current))

                cc_array_voltage = round(int(charger[9]) * 0.1,2)
                logger.debug(".... CC Array Voltage " + str(cc_array_voltage))

                CC_Todays_KW = round(int(charger[18]) * 0.1,2)
                logger.debug(".... CC Daily_KW (KW) " + str(CC_Todays_KW))

                CC_Watts = round(int(charger[13]),2)
                logger.debug(".... CC Actual_watts (W) " + str(CC_Watts))

                cc_charger_state = round(int(charger[12]),2)
                logger.debug(".... CC Charger State " + str(cc_charger_state))  # 0=Silent,1=Float,2=Bulk,3=Absorb,4=EQ
                cc_mode = decode_enum(cc_charger_state, get_sdc_values(blockResult["id"], "CC_Charger_State"), "charge controller state")

                cc_batt_voltage = round(int(charger[8]) * 0.1,2)
                logger.debug(".... CC Battery Voltage (V) " + str(cc_batt_voltage))

                CC_Todays_AH = round(int(charger[19]),2)
                logger.debug(".... CC Daily_AH (A) " + str(CC_Todays_AH))

                # Calculated summary - charger / PV totals.
                pv_total_power        += CC_Watts
                pv_daily_kwh          += CC_Todays_KW
                pv_total_current      += cc_array_current
                chargers_total_current += cc_batt_current

                charger_index_by_address[address] = chargers
                charger_data_by_address[address] = {
                    "charger_current" : cc_batt_current,
                    "pv_current"      : cc_array_current,
                    "pv_voltage"      : cc_array_voltage,
                    "pv_power"        : CC_Watts,
                    "charge_mode"     : cc_mode,
                    "battery_voltage" : cc_batt_voltage,
                    "daily_ah"        : CC_Todays_AH,
                    "daily_kwh"       : CC_Todays_KW
                }

            if "Charge Controller Configuration block" in blockResult['DID']:           #some CC parameters are in configuration block
                response = client.read_holding_registers(
                    reg,
                    count=blockResult["size"] + 2
                )
                charger_config = response.registers
                logger.debug(".. Charge Controller Configuration block")
                logger.debug(".... Connected on HUB port " + str(charger_config[2]))
                port=(charger_config[2]-1)
                address=port+1
                charger_index = charger_index_by_address.get(address)
                charger_data = charger_data_by_address.get(address)
                if charger_index is None or charger_data is None:
                    raise RuntimeError("Charge Controller Configuration Block has no matching real time block on HUB port " + str(address))

                cc_batt_current = charger_data["charger_current"]
                cc_array_current = charger_data["pv_current"]
                cc_array_voltage = charger_data["pv_voltage"]
                CC_Watts = charger_data["pv_power"]
                cc_mode = charger_data["charge_mode"]
                cc_batt_voltage = charger_data["battery_voltage"]
                CC_Todays_AH = charger_data["daily_ah"]
                CC_Todays_KW = charger_data["daily_kwh"]

                CCconfig_AUX_Mode   = int(charger_config[32])
                logger.debug(".... CC Aux Mode " + str(CCconfig_AUX_Mode))

                aux_mode = decode_enum(CCconfig_AUX_Mode, get_sdc_values(blockResult["id"], "CCconfig_AUX_Mode"), "charge controller AUX mode")

                CCconfig_AUX_State  = int(charger_config[34])
                logger.debug(".... CC Aux State " + str(CCconfig_AUX_State))
                aux_state = decode_enum(CCconfig_AUX_State, get_sdc_values(blockResult["id"], "CCconfig_AUX_State"), "charge controller AUX state")

                CCconfig_Faults = int(charger_config[9])
                logger.debug(".... CC Error Flags " + str(CCconfig_Faults))
                error_flags = decode_flags(CCconfig_Faults, get_sdc_values(blockResult["id"], "CCconfig_Faults"))

                # Charge controller data - JSON preparation
                devices_array= {
                  "address"         : address,
                  "device_id"       : 3,
                  "charger_current" : cc_batt_current,
                  "pv_current"      : cc_array_current,
                  "pv_voltage"      : cc_array_voltage,
                  "pv_power"        : CC_Watts,
                  "aux"             : aux_mode,
                  "aux_mode"        : aux_state,
                  "error_modes"     : [
                    error_flags
                  ],
                  "charge_mode"     : cc_mode,
                  "battery_voltage" : cc_batt_voltage,
                  "daily_ah"        : CC_Todays_AH,
                  "daily_kwh"       : CC_Todays_KW,
                  "label"           : device_list[port]
                    }
                devices.append(devices_array)

                # Charge controller data - MariaDB SQL preparation
                db_devices_values.append((date_sql,address,3,cc_batt_current,cc_array_current,cc_array_voltage,CC_Todays_KW,aux_mode,aux_state,error_flags,cc_mode,cc_batt_voltage,CC_Todays_AH))
                db_devices_sql.append ("INSERT INTO monitormate_cc \
                (date,address,device_id,charge_current,pv_current,pv_voltage,daily_kwh,aux_mode,aux,error_modes,charge_mode,battery_voltage,daily_ah) \
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")

                # Charge controller data - MQTT preparation
                mqtt_devices.append({
                    "outback/chargers/" + str(charger_index) + "/charger_current" : cc_batt_current,
                    "outback/chargers/" + str(charger_index) + "/pv_current"      : cc_array_current,
                    "outback/chargers/" + str(charger_index) + "/pv_voltage"      : cc_array_voltage,
                    "outback/chargers/" + str(charger_index) + "/pv_power"        : CC_Watts,
                    "outback/chargers/" + str(charger_index) + "/aux"             : aux_mode,
                    "outback/chargers/" + str(charger_index) + "/aux_mode"        : aux_state,
                    "outback/chargers/" + str(charger_index) + "/error_modes"     : error_flags,
                    "outback/chargers/" + str(charger_index) + "/battery_voltage" : cc_batt_voltage,
                    "outback/chargers/" + str(charger_index) + "/daily_ah"        : CC_Todays_AH,
                    "outback/chargers/" + str(charger_index) + "/daily_kwh"       : CC_Todays_KW,
                    "outback/chargers/" + str(charger_index) + "/charge_mode"     : cc_mode
                    })

        except Exception as e:
            logger.warning("port: " + str(port) + " CC module " + str(e))

        try:
            if "FLEXnet-DC Real Time Block" in blockResult['DID']:
                response = client.read_holding_registers(
                    reg,
                    count=blockResult["size"] + 2
                )
                fndc = response.registers
                logger.debug(".. Detect a FLEXnet-DC Real Time Block")

                logger.debug(".... Connected on HUB port " + str(fndc[2]))
                port=(fndc[2]-1)
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

                fn_shunt_a_current = round(decode_int16(int(fndc[8])) * 0.1,2)
                logger.debug(".... FN Shunt A Current (A) " + str(fn_shunt_a_current))

                fn_shunt_b_current = round(decode_int16(fndc[9]) * 0.1,2)
                logger.debug(".... FN Shunt B Current (A) " + str(fn_shunt_b_current))

                fn_shunt_c_current = round(decode_int16(int(fndc[10])) * 0.1,2)
                logger.debug(".... FN Shunt C Current (A) " + str(fn_shunt_c_current))

                fn_battery_voltage = round(int(fndc[11]) * 0.1,2)
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

                fn_state_of_charge = int(fndc[27])
                logger.debug(".... FN State of Charge " + str(fn_state_of_charge))

                FN_Status_Flags  = int(fndc[14])
                logger.debug(".... FN Status Flag " + str(FN_Status_Flags))
                charge_params_met="false"
                if FN_Status_Flags & 0x0002:
                    charge_params_met="true"
                logger.debug(".... FN Charge Parameters Met " + str(FN_Status_Flags ) + " " + charge_params_met)
                relay_status="disabled"
                if FN_Status_Flags & 0x0001:
                    relay_status="enabled"
                logger.debug(".... FN Relay Status " + str(FN_Status_Flags ) + " " + relay_status)

                fn_battery_temperature = decode_int16(int(fndc[13]))
                logger.debug(".... FN Battery Temperature " + str(fn_battery_temperature))

                FN_Shunt_A_Accumulated_AH = round(decode_int16(int(fndc[15])),2)
                logger.debug(".... FN FN_Shunt_A_Accumulated_AH " + str(FN_Shunt_A_Accumulated_AH))

                FN_Shunt_A_Accumulated_kWh = round(decode_int16(int(fndc[16])) * 0.01,2)
                logger.debug(".... FN FN_Shunt_A_Accumulated_kWh " + str(FN_Shunt_A_Accumulated_kWh))

                FN_Shunt_B_Accumulated_AH = round(decode_int16(int(fndc[17])),2)
                logger.debug(".... FN FN_Shunt_B_Accumulated_AH " + str(FN_Shunt_B_Accumulated_AH))

                FN_Shunt_B_Accumulated_kWh = round(decode_int16(int(fndc[18])) * 0.01,2)
                logger.debug(".... FN FN_Shunt_B_Accumulated_kWh " + str(FN_Shunt_B_Accumulated_kWh))

                FN_Shunt_C_Accumulated_AH = round(decode_int16(int(fndc[19])),2)
                logger.debug(".... FN FN_Shunt_C_Accumulated_AH " + str(FN_Shunt_C_Accumulated_AH))

                FN_Shunt_C_Accumulated_kWh = round(decode_int16(int(fndc[20])) * 0.01,2)
                logger.debug(".... FN FN_Shunt_C_Accumulated_kWh " + str(FN_Shunt_C_Accumulated_kWh))

                FN_Days_Since_Charge_Parameters_Met = round(int((fndc[26])) * 0.1,2)
                logger.debug(".... FN days_since_full " + str(FN_Days_Since_Charge_Parameters_Met))

                FN_Todays_Minimum_SOC = int(fndc[28])
                logger.debug(".... FN Todays_Minimum_SOC " + str(FN_Todays_Minimum_SOC))

                FN_Todays_Maximum_SOC = int(fndc[29])
                logger.debug(".... FN Todays_Maximum_SOC " + str(FN_Todays_Maximum_SOC))

                FN_Todays_NET_Input_AH = round(int(fndc[30]),2)
                logger.debug(".... FN Todays_NET_Input_AH " + str(FN_Todays_NET_Input_AH))

                FN_Todays_NET_Input_kWh = round(int(fndc[31]) * 0.01,2)
                logger.debug(".... FN Todays_NET_Input_kWh " + str(FN_Todays_NET_Input_kWh))

                FN_Todays_NET_Output_AH = round(int(fndc[32]),2)
                logger.debug(".... FN Todays_NET_Output_AH " + str(FN_Todays_NET_Output_AH))

                FN_Todays_NET_Output_kWh = round(int(fndc[33]) * 0.01,2)
                logger.debug(".... FN Todays_NET_Output_kWh " + str(FN_Todays_NET_Output_kWh))

                FN_Charge_Factor_Corrected_NET_Battery_AH = round(decode_int16(int(fndc[36])),2)
                logger.debug(".... FN Charge_Factor_Corrected_NET_Battery_AH " + str(FN_Charge_Factor_Corrected_NET_Battery_AH))

                FN_Charge_Factor_Corrected_NET_Battery_kWh = round(decode_int16(int(fndc[37])) * 0.01,2)
                logger.debug(".... FN_Charge_Factor_Corrected_NET_Battery_kWh " + str(FN_Charge_Factor_Corrected_NET_Battery_kWh))

                FN_Todays_Minimum_Battery_Voltage = round(decode_int16(int(fndc[38])) * 0.1 ,2)
                logger.debug(".... FN_Todays_Minimum_Battery_Voltage " + str(FN_Todays_Minimum_Battery_Voltage))

                FN_Todays_Maximum_Battery_Voltage = round(decode_int16(int(fndc[41])) * 0.1 ,2)
                logger.debug(".... FN_Todays_Maximum_Battery_Voltage " + str(FN_Todays_Maximum_Battery_Voltage))

                fndc_data_by_address[address] = {
                    "shunt_a_current"                      : fn_shunt_a_current, "shunt_b_current": fn_shunt_b_current,
                    "shunt_c_current"                      : fn_shunt_c_current, "battery_voltage": fn_battery_voltage,
                    "state_of_charge"                      : fn_state_of_charge, "charge_params_met": charge_params_met,
                    "relay_status"                         : relay_status,
                    "battery_temperature"                  : fn_battery_temperature,
                    "accumulated_ah_shunt_a"               : FN_Shunt_A_Accumulated_AH, "accumulated_kwh_shunt_a": FN_Shunt_A_Accumulated_kWh,
                    "accumulated_ah_shunt_b"               : FN_Shunt_B_Accumulated_AH, "accumulated_kwh_shunt_b": FN_Shunt_B_Accumulated_kWh,
                    "accumulated_ah_shunt_c"               : FN_Shunt_C_Accumulated_AH, "accumulated_kwh_shunt_c": FN_Shunt_C_Accumulated_kWh,
                    "days_since_charge_met"                : FN_Days_Since_Charge_Parameters_Met, "today_min_soc": FN_Todays_Minimum_SOC,
                    "today_max_soc"                        : FN_Todays_Maximum_SOC, "today_net_input_ah": FN_Todays_NET_Input_AH,
                    "today_net_output_ah"                  : FN_Todays_NET_Output_AH, "today_net_input_kwh": FN_Todays_NET_Input_kWh,
                    "today_net_output_kwh"                 : FN_Todays_NET_Output_kWh,
                    "charge_factor_corrected_net_batt_ah"  : FN_Charge_Factor_Corrected_NET_Battery_AH,
                    "charge_factor_corrected_net_batt_kwh" : FN_Charge_Factor_Corrected_NET_Battery_kWh,
                    "min_voltage"                          : FN_Todays_Minimum_Battery_Voltage, "max_voltage": FN_Todays_Maximum_Battery_Voltage
                }

            if "FLEXnet-DC Configuration Block" in blockResult['DID']:
                response = client.read_holding_registers(
                    reg,
                    count=blockResult["size"] + 2
                )
                fndc_config = response.registers
                logger.debug(".. Detect a FLEXnet-DC Configuration Block")

                logger.debug(".... Connected on HUB port " + str(fndc_config[2]))
                port=(fndc_config[2]-1)
                address=port+1
                fndc_data = fndc_data_by_address.get(address)
                if fndc_data is None:
                    raise RuntimeError("FLEXnet-DC Configuration Block has no matching real time block on HUB port " + str(address))

                fn_shunt_a_current = fndc_data["shunt_a_current"]
                fn_shunt_b_current = fndc_data["shunt_b_current"]
                fn_shunt_c_current = fndc_data["shunt_c_current"]
                fn_battery_voltage = fndc_data["battery_voltage"]
                fn_state_of_charge = fndc_data["state_of_charge"]
                charge_params_met = fndc_data["charge_params_met"]
                relay_status = fndc_data["relay_status"]
                fn_battery_temperature = fndc_data["battery_temperature"]
                FN_Shunt_A_Accumulated_AH = fndc_data["accumulated_ah_shunt_a"]
                FN_Shunt_A_Accumulated_kWh = fndc_data["accumulated_kwh_shunt_a"]
                FN_Shunt_B_Accumulated_AH = fndc_data["accumulated_ah_shunt_b"]
                FN_Shunt_B_Accumulated_kWh = fndc_data["accumulated_kwh_shunt_b"]
                FN_Shunt_C_Accumulated_AH = fndc_data["accumulated_ah_shunt_c"]
                FN_Shunt_C_Accumulated_kWh = fndc_data["accumulated_kwh_shunt_c"]
                FN_Days_Since_Charge_Parameters_Met = fndc_data["days_since_charge_met"]
                FN_Todays_Minimum_SOC = fndc_data["today_min_soc"]
                FN_Todays_Maximum_SOC = fndc_data["today_max_soc"]
                FN_Todays_NET_Input_AH = fndc_data["today_net_input_ah"]
                FN_Todays_NET_Output_AH = fndc_data["today_net_output_ah"]
                FN_Todays_NET_Input_kWh = fndc_data["today_net_input_kwh"]
                FN_Todays_NET_Output_kWh = fndc_data["today_net_output_kwh"]
                FN_Charge_Factor_Corrected_NET_Battery_AH = fndc_data["charge_factor_corrected_net_batt_ah"]
                FN_Charge_Factor_Corrected_NET_Battery_kWh = fndc_data["charge_factor_corrected_net_batt_kwh"]
                FN_Todays_Minimum_Battery_Voltage = fndc_data["min_voltage"]
                FN_Todays_Maximum_Battery_Voltage = fndc_data["max_voltage"]

                FNconfig_Shunt_A_Enabled = int(fndc_config[14])
                Shunt_A_Enabled = decode_enum(FNconfig_Shunt_A_Enabled, get_sdc_values(blockResult["id"], "FNconfig_Shunt_A_Enabled"), "FNDC shunt A enabled")
                logger.debug(".... FN Shunt_A_Enabled " + Shunt_A_Enabled)

                FNconfig_Shunt_B_Enabled = int(fndc_config[15])
                Shunt_B_Enabled = decode_enum(FNconfig_Shunt_B_Enabled, get_sdc_values(blockResult["id"], "FNconfig_Shunt_B_Enabled"), "FNDC shunt B enabled")
                logger.debug(".... FN Shunt_B_Enabled " + Shunt_B_Enabled)

                FNconfig_Shunt_C_Enabled = int(fndc_config[16])
                Shunt_C_Enabled = decode_enum(FNconfig_Shunt_C_Enabled, get_sdc_values(blockResult["id"], "FNconfig_Shunt_C_Enabled"), "FNDC shunt C enabled")
                logger.debug(".... FN Shunt_C_Enabled " + Shunt_C_Enabled)

                FNconfig_Relay_Control = int(fndc_config[17])
                relay_mode="unknown"
                if FNconfig_Relay_Control==0:
                    relay_mode="off"
                if FNconfig_Relay_Control==1:
                    relay_mode="auto"
                if FNconfig_Relay_Control==2:
                    relay_mode="on"
                logger.debug(".... FN Relay Mode " + str(FNconfig_Relay_Control) + " " + relay_mode)

                # FNDC data - JSON preparation
                devices_array= {
                  "address"                              : address,
                  "device_id"                            : 4,
                  "shunt_a_current"                      : fn_shunt_a_current,
                  "shunt_b_current"                      : fn_shunt_b_current,
                  "shunt_c_current"                      : fn_shunt_c_current,
                  "battery_voltage"                      : fn_battery_voltage,
                  "state_of_charge"                      : fn_state_of_charge,
                  "shunt_enabled_a"                      : Shunt_A_Enabled,
                  "shunt_enabled_b"                      : Shunt_B_Enabled,
                  "shunt_enabled_c"                      : Shunt_C_Enabled,
                  "charge_params_met"                    : charge_params_met,
                  "relay_status"                         : relay_status,
                  "relay_mode"                           : relay_mode,
                  "battery_temperature"                  : fn_battery_temperature,
                  "accumulated_ah_shunt_a"               : FN_Shunt_A_Accumulated_AH,
                  "accumulated_kwh_shunt_a"              : FN_Shunt_A_Accumulated_kWh,
                  "accumulated_ah_shunt_b"               : FN_Shunt_B_Accumulated_AH,
                  "accumulated_kwh_shunt_b"              : FN_Shunt_B_Accumulated_kWh,
                  "accumulated_ah_shunt_c"               : FN_Shunt_C_Accumulated_AH,
                  "accumulated_kwh_shunt_c"              : FN_Shunt_C_Accumulated_kWh,
                  "days_since_charge_met"                : FN_Days_Since_Charge_Parameters_Met,
                  "today_min_soc"                        : FN_Todays_Minimum_SOC,
                  "today_net_input_ah"                   : FN_Todays_NET_Input_AH,
                  "today_net_output_ah"                  : FN_Todays_NET_Output_AH,
                  "today_net_input_kwh"                  : FN_Todays_NET_Input_kWh,
                  "today_net_output_kwh"                 : FN_Todays_NET_Output_kWh,
                  "charge_factor_corrected_net_batt_ah"  : FN_Charge_Factor_Corrected_NET_Battery_AH,
                  "charge_factor_corrected_net_batt_kwh" : FN_Charge_Factor_Corrected_NET_Battery_kWh,
                  "label"                                : device_list[port],
                  "shunt_a_label"                        : shunt_list[0],
                  "shunt_b_label"                        : shunt_list[1],
                  "shunt_c_label"                        : shunt_list[2]
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
                     "outback/fndc/battery_voltage"       : fn_battery_voltage,
                     "outback/fndc/state_of_charge"       : fn_state_of_charge,
                     "outback/fndc/battery_temperature"   : fn_battery_temperature,
                     "outback/fndc/shunt_a_current"       : fn_shunt_a_current,
                     "outback/fndc/shunt_c_current"       : fn_shunt_c_current,
                     "outback/fndc/shunt_b_current"       : fn_shunt_b_current,
                     "outback/fndc/charge_params_met"     : charge_params_met,
                     "outback/fndc/today_min_soc"         : FN_Todays_Minimum_SOC,
                     "outback/fndc/today_max_soc"         : FN_Todays_Maximum_SOC,
                     "outback/fndc/days_since_charge_met" : FN_Days_Since_Charge_Parameters_Met,
                     "outback/fndc/today_net_input_ah"    : FN_Todays_NET_Input_AH,
                     "outback/fndc/today_net_output_ah"   : FN_Todays_NET_Output_AH,
                     "outback/fndc/todays_net_input_kWh"  : FN_Todays_NET_Input_kWh,
                     "outback/fndc/todays_net_output_kWh" : FN_Todays_NET_Output_kWh,
                     "outback/fndc/min_voltage"           : FN_Todays_Minimum_Battery_Voltage,
                     "outback/fndc/max_voltage"           : FN_Todays_Maximum_Battery_Voltage
                     })

        except Exception as e:
            logger.warning("port: " + str(port) + " FNDC module " + str(e))

        if "End of SunSpec" not in blockResult['DID']:
            reg = reg + blockResult['size'] + 2
        else:
            # Report what the scan found. A block that is present but not decoded used to be skipped
            # without a trace, which made unsupported hardware very hard to spot.
            logger.debug(".. SunSpec blocks detected: " + ", ".join(detected_blocks))

            not_decoded = sorted({name for name in detected_blocks
                                  if name not in handled_blocks and name != "End of SunSpec"})
            if not_decoded:
                logger.debug(".. SunSpec blocks present but not decoded by this script: " + ", ".join(not_decoded))

            if inverters == 0 and not any(name in inverter_blocks for name in detected_blocks):
                logger.warning(" No inverter real time block found. Blocks detected: " + ", ".join(detected_blocks))

            client.close()
            client = None
            mate_run = datetime.now()
            running_time = round ((mate_run - start_run).total_seconds(),3)
            logger.debug(".. Mate connection closed")

            # MQTT discovery - publish Home Assistant device definitions once,
            # immediately after the first complete MATE3 scan.
            if MQTT_active == 'true' and MQTT_discovery_active == 'true' and mqtt_discovery_done == False:
                MQTT_auth = None
                if len(MQTT_username) > 0:
                    MQTT_auth = { 'username': MQTT_username, 'password': MQTT_password }

                logger.debug(".... HA device: Outback Summary")
                logger.debug(".... HA device: Outback System")
                publish_mqtt_discovery(detected_devices, MQTT_auth)
                mqtt_discovery_done = True
                logger.debug(".. HA devices discovery completed")

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
                logger.debug(".. Summary of the day skipped - FNDC not detected")
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
                    logger.debug(".. Summary of the day - first record completed")
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

    # JSON serialisation and save
    try:
        json_data={"time":time, "devices":devices, "summary":summary, "various":various}
        with open(os.path.join(output_path, 'mate_status.json'), 'w') as outfile:
            json.dump(json_data, outfile)

        if duplicate_active == 'true':
            # print(duplicate_active)
            # shutil.copy(os.path.join(output_path, 'mate_status.json'), os.path.join(duplicate_path, 'mate_status.json'))      #copy the file in second location
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

            if mate3_status != MQTT_payload_online:
                if publish_mqtt_state(MQTT_status_topic, MQTT_payload_online):
                    mate3_status = MQTT_payload_online
                    logger.info(" MATE3 status: " + MQTT_payload_online)

            if mqtt_availability_state != MQTT_payload_online:
                if publish_mqtt_state(MQTT_availability_topic, MQTT_payload_online):
                    mqtt_availability_state = MQTT_payload_online
                    logger.info(" MQTT availability: " + MQTT_payload_online)

            mate3_failed_cycles = 0

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
# Close the MATE3 Modbus connection without interrupting shutdown.
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
            if mate3_status != MQTT_payload_offline:
                if publish_mqtt_state(MQTT_status_topic, MQTT_payload_offline):
                    mate3_status = MQTT_payload_offline
                    logger.info(" MATE3 status: " + MQTT_payload_offline)
            close_mate3_safely()

        tm.sleep(scan_frequency)
else:
    try:
        ok = main()
        if ok == False:
            logger.critical("Single run failed")
    except Exception:
        logger.exception("Main loop error")
        if mate3_status != MQTT_payload_offline:
            if publish_mqtt_state(MQTT_status_topic, MQTT_payload_offline):
                mate3_status = MQTT_payload_offline
                logger.info(" MATE3 status: " + MQTT_payload_offline)
        close_mate3_safely()

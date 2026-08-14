import logging
from logging.handlers import RotatingFileHandler
import json
import time
from datetime import datetime
from pymodbus.client import ModbusTcpClient as ModbusClient
from configparser import ConfigParser
import sys, os
from sdc import SDC_BLOCKS

script_ver = "1.0.0_20260814"
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

# Creates the application logger.
logger = logging.getLogger("outback")
logger.setLevel(LOGGING_LEVEL_FILE)  # Sets the minimum logging level.

# Console handler.
console_handler = logging.StreamHandler()
console_handler.setLevel(LOGGING_LEVEL_FILE)
# Console formatter.
console_formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s', datefmt='%Y%m%d %H:%M:%S')
console_handler.setFormatter(console_formatter)

# merge paths to use proper separators windows or Linux
log_path = os.path.join(working_dir, 'data', 'events_cms.log')
file_handler = RotatingFileHandler(log_path , mode='a', maxBytes=LOGGING_FILE_MAX_SIZE*1000, backupCount=LOGGING_FILE_MAX_FILES, encoding=None, delay=False)
file_handler.setLevel(LOGGING_LEVEL_FILE)

# File formatter.
file_formatter = logging.Formatter('%(asctime)s| CMS |%(levelname)8s| %(message)s ',datefmt='%Y%m%d %H:%M:%S') 
file_handler.setFormatter(file_formatter)

# Adds the handlers to the logger.
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Creates a blank mate_input batch.
# An empty time_taken marks the batch as pending.
def blankjsonfile():
    mate_input = {
        "time_taken": "",
        "commands": {}
    }

    json_path = os.path.join(output_path, 'mate_input.json')
    with open(json_path, 'w') as outfile:
        json.dump(mate_input, outfile, indent=1)
    logger.info(".. multiple input json file created")


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
    # Initializes the SunSpec register decoder.
    def __init__(self, registers):
        self.registers = registers
        self.offset = 0
        
    # Decodes one unsigned 16-bit register.
    def decode_16bit_uint(self):
        value = self.registers[self.offset]
        self.offset += 1
        return value
    
    # Decodes two registers as one unsigned 32-bit value.
    def decode_32bit_uint(self):
        value = (self.registers[self.offset] << 16) + self.registers[self.offset + 1]
        self.offset += 2
        return value
    
    # Decodes a fixed-size SunSpec string.
    def decode_string(self, size):
        string_data = ''.join([chr((self.registers[i] >> 8) & 0xFF) + chr(self.registers[i] & 0xFF) for i in range(self.offset, self.offset + (size // 2))])
        self.offset += size // 2
        return string_data.strip()
    
# Read SunSpec Header with logic from pymodbus example
# Converts an unsigned register value to signed INT16.
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
# Converts a decimal integer to a binary string.
def binary(decimal) :
    otherBase = ""
    while decimal != 0 :
        otherBase  =  str(decimal % 2) + otherBase
        decimal    //=  2
    return otherBase

# Reads and decodes the SunSpec common information block.
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
# Verifies the SunSpec header and OutBack manufacturer signature.
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
        logger.debug(".. Not an Outback Power device. Detected " + str(manufacturer))
        return None
    try:
        register = client.read_holding_registers(basereg + 3)
    except:
        return None
    blocksize = int(register.registers[0])
    return blocksize

# Reads one SunSpec block header and returns its DID and size.
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
        logger.debug(".. Unknown device type with DID=" + str(blockID))
    return {"size": blocksize, "DID": blockname}

# Maximum write/read-back attempts for one target.
WRITE_RETRIES = 3


# Saves mate_input.json to the configured output path.
def save_mate_input(mate_input):
    json_path = os.path.join(output_path, 'mate_input.json')
    with open(json_path, 'w') as outfile:
        json.dump(mate_input, outfile, indent=1)


# Loads mate_input.json and creates a blank file when missing.
def load_mate_input():
    json_path = os.path.join(output_path, 'mate_input.json')

    try:
        with open(json_path, 'r') as infile:
            mate_input = json.load(infile)
    except Exception as e:
        logger.warning(".. multiple input json file not found " + str(e))
        blankjsonfile()
        with open(json_path, 'r') as infile:
            mate_input = json.load(infile)

    return mate_input


# Returns all SDC definitions matching a command name.
def get_command_definitions(command_name):
    command_definitions = []

    for did in SDC_BLOCKS:
        for field_name in SDC_BLOCKS[did]["fields"]:
            field = SDC_BLOCKS[did]["fields"][field_name]

            if field["command_name"] == command_name:
                command_definitions.append({
                    "did": did,
                    "field_name": field_name,
                    "field": field
                })

    return command_definitions


# Validates that every requested command exists in the SDC.
def validate_command_names(commands):
    for command_name in commands:
        if len(get_command_definitions(command_name)) == 0:
            raise ValueError("command_name not found in SDC: " + str(command_name))


# Scans SunSpec blocks and records detected OutBack devices.
def scan_blocks(startReg):
    detected_blocks = []
    reg = startReg
    end_of_sunspec = False

    for block in range(0, 30):
        blockResult = getBlock(reg)

        if blockResult is None:
            raise RuntimeError("failed to read SunSpec block at register " + str(reg))

        # getBlock is preserved exactly from the original Change. Read the
        # numeric DID here because command matching is based on DID, not name.
        response = client.read_holding_registers(reg, count=1)
        block_did = int(response.registers[0])

        detected_block = {
            "did": block_did,
            "name": blockResult["DID"],
            "size": blockResult["size"],
            "base_register": reg,
            "address": None
        }

        # Configuration blocks confirmed by ReadMateStatusModBus use offset 2
        # as device address / HUB port.
        if block_did in (64112, 64114, 64116, 64119):
            response = client.read_holding_registers(reg + 2, count=1)
            detected_block["address"] = int(response.registers[0])

        detected_blocks.append(detected_block)

        logger.debug(
            ".. Detected block DID=" + str(detected_block["did"]) +
            " name=" + str(detected_block["name"]) +
            " size=" + str(detected_block["size"]) +
            " base_register=" + str(detected_block["base_register"]) +
            " address=" + str(detected_block["address"])
        )

        if block_did == 65535:
            end_of_sunspec = True
            break

        reg = reg + blockResult["size"] + 2

    if end_of_sunspec == False:
        raise RuntimeError("End of SunSpec block DID 65535 not found")

    return detected_blocks


# Matches one command to compatible detected SunSpec blocks.
def get_command_targets(command_name, detected_blocks):
    command_definitions = get_command_definitions(command_name)
    targets = []

    for detected_block in detected_blocks:
        for command_definition in command_definitions:
            if detected_block["did"] == command_definition["did"]:
                targets.append({
                    "did": detected_block["did"],
                    "name": detected_block["name"],
                    "size": detected_block["size"],
                    "base_register": detected_block["base_register"],
                    "address": detected_block["address"],
                    "field_name": command_definition["field_name"],
                    "field": command_definition["field"]
                })

    return targets


# Reads the scale-factor register required by the target field.
def read_scale_factor(target):
    scale_factor_name = target["field"]["scale_factor"]

    if scale_factor_name is None:
        return None

    scale_factor_field = SDC_BLOCKS[target["did"]]["fields"].get(scale_factor_name)

    if scale_factor_field is None:
        raise ValueError("scale factor field not found in SDC: " + str(scale_factor_name))

    scale_factor_register = target["base_register"] + scale_factor_field["offset"]
    response = client.read_holding_registers(scale_factor_register, count=1)
    scale_factor_value = int(response.registers[0])

    if scale_factor_field["type"] == "int16" and scale_factor_value >= 32768:
        scale_factor_value = scale_factor_value - 65536

    return scale_factor_value


# Validates user input and converts it to the raw register value.
def prepare_value(command_name, target, value):
    field = target["field"]
    field_type = field["type"]

    # First implementation supports only the types agreed for Change.
    if field_type != "uint16" and field_type != "int16":
        raise ValueError("data type not implemented: " + str(field_type))

    if field["values"] is not None:
        raw_value = None
        aliases = field.get("aliases", {})

        # Preferred CLI aliases use the same raw keys as values.
        for raw in aliases:
            if str(aliases[raw]) == str(value):
                raw_value = int(raw)
                break

        # Fall back to the canonical SDC values when no CLI alias matches.
        if raw_value is None:
            for raw in field["values"]:
                if str(field["values"][raw]) == str(value):
                    raw_value = int(raw)
                    break

        if raw_value is None:
            raise ValueError("value not found in SDC: " + str(value))

    else:
        try:
            numeric_value = float(value)
        except:
            raise ValueError("numeric value required: " + str(value))

        # Numeric limits are expressed in the documented/user unit,
        # before the scale factor is applied.
        min_value = field.get("min")
        max_value = field.get("max")

        if min_value is not None and numeric_value < min_value:
            raise ValueError("value below minimum " + str(min_value))

        if max_value is not None and numeric_value > max_value:
            raise ValueError("value above maximum " + str(max_value))

        scale_factor = read_scale_factor(target)

        if scale_factor is None:
            raw_value = numeric_value
        else:
            raw_value = numeric_value / (10 ** scale_factor)

        if raw_value != int(raw_value):
            raise ValueError("value cannot be represented by register: " + str(value))

        raw_value = int(raw_value)

    if field_type == "int16":
        if raw_value < -32768 or raw_value > 32767:
            raise ValueError("int16 value outside range")
        raw_value = raw_value & 0xFFFF
    else:
        if raw_value < 0 or raw_value > 65535:
            raise ValueError("uint16 value outside range")

    return raw_value


# Converts a raw register value back to the user-facing value for write logs.
# Enumerated fields use aliases/values; numeric fields apply the SDC scale factor.
def format_write_value(target, raw_value):
    field = target["field"]

    if field.get("values") is not None:
        aliases = field.get("aliases") or {}
        display_raw = raw_value

        if field.get("type") == "int16" and raw_value >= 32768:
            display_raw = raw_value - 65536

        raw_key = str(display_raw)

        if raw_key in aliases:
            return aliases[raw_key]

        if raw_key in field["values"]:
            return field["values"][raw_key]

        return display_raw

    scale_factor = read_scale_factor(target)

    if scale_factor is None:
        return raw_value

    value = raw_value * (10 ** scale_factor)

    if isinstance(value, float) and value.is_integer():
        return int(value)

    return value


# Writes one target using SDC access and verification rules.
def write_target(command_name, target, raw_value, requested_value):
    field = target["field"]
    access = field["rw"]
    register = target["base_register"] + field["offset"]

    logger.debug(
        ".... target DID=" + str(target["did"]) +
        " address=" + str(target["address"]) +
        " base_register=" + str(target["base_register"]) +
        " offset=" + str(field["offset"]) +
        " register=" + str(register)
    )
    logger.debug(".... requested value: " + str(requested_value) + ", raw value: " + str(raw_value))

    # Fail-safe write protection from SDC.
    if field.get("write_active", False) != True:
        logger.warning(
            ".... " + command_name + " change to: " + str(requested_value) +
            " FAILED - blocked by SDC write_active=False"
        )
        return False

    # R/W: read, write when required, read-back and verify.
    if access == "R/W":
        loop = 0

        while loop < WRITE_RETRIES:
            try:
                response = client.read_holding_registers(register, count=1)
                current_value = int(response.registers[0])
                logger.debug(
                    ".... current value: " + str(format_write_value(target, current_value)) +
                    ", raw value: " + str(current_value)
                )

                if current_value == raw_value:
                    logger.info(".... " + command_name + " already set to: " + str(requested_value))
                    return True

                logger.debug(".... updating " + command_name + " to: " + str(requested_value))
                rw = client.write_register(register, raw_value)

                if rw is not None and rw.isError():
                    raise RuntimeError("write failed")

                response = client.read_holding_registers(register, count=1)
                read_back_value = int(response.registers[0])
                logger.debug(
                    ".... read-back value: " + str(format_write_value(target, read_back_value)) +
                    ", raw value: " + str(read_back_value)
                )

                if read_back_value == raw_value:
                    logger.debug(".... writing verification: all good")
                    logger.info(".... " + command_name + " changed to: " + str(requested_value))
                    return True

            except Exception as e:
                logger.warning(
                    ".... " + command_name + " change to: " + str(requested_value) +
                    " FAILED - " + str(e) +
                    " DID=" + str(target["did"]) +
                    " address=" + str(target["address"]) +
                    " base_register=" + str(target["base_register"]) +
                    " offset=" + str(field["offset"]) +
                    " register=" + str(register)
                )

            loop = loop + 1
            logger.debug(".... writing verification loop " + str(loop))

        logger.warning(
            ".... " + command_name + " change to: " + str(requested_value) +
            " FAILED - verification failed" +
            " DID=" + str(target["did"]) +
            " address=" + str(target["address"]) +
            " base_register=" + str(target["base_register"]) +
            " offset=" + str(field["offset"]) +
            " register=" + str(register)
        )
        return False

    # W: write only. Do not read a register documented as write-only.
    if access == "W":
        loop = 0

        while loop < WRITE_RETRIES:
            try:
                logger.debug(".... updating " + command_name + " to: " + str(requested_value))
                rw = client.write_register(register, raw_value)

                if rw is not None and rw.isError() == False:
                    logger.info(".... " + command_name + " changed to: " + str(requested_value))
                    return True

            except Exception as e:
                logger.warning(
                    ".... " + command_name + " change to: " + str(requested_value) +
                    " FAILED - " + str(e) +
                    " DID=" + str(target["did"]) +
                    " address=" + str(target["address"]) +
                    " base_register=" + str(target["base_register"]) +
                    " offset=" + str(field["offset"]) +
                    " register=" + str(register)
                )

            loop = loop + 1
            logger.debug(".... writing loop " + str(loop))

        logger.warning(
            ".... " + command_name + " change to: " + str(requested_value) +
            " FAILED - write failed" +
            " DID=" + str(target["did"]) +
            " address=" + str(target["address"]) +
            " base_register=" + str(target["base_register"]) +
            " offset=" + str(field["offset"]) +
            " register=" + str(register)
        )
        return False

    logger.warning(
        ".... " + command_name + " change to: " + str(requested_value) +
        " FAILED - access=" + str(access) + " is not writable according to SDC"
    )
    return False


# Executes one command on all compatible detected targets.
def execute_command(command_name, value, detected_blocks):
    targets = get_command_targets(command_name, detected_blocks)

    if len(targets) == 0:
        logger.warning(".... " + command_name + " change to: " + str(value) + " FAILED - no compatible detected hardware")
        return False

    command_result = True

    for target in targets:
        try:
            raw_value = prepare_value(command_name, target, value)
        except Exception as e:
            logger.warning(
                ".... " + command_name + " change to: " + str(value) +
                " FAILED - " + str(e) +
                " DID=" + str(target["did"]) +
                " address=" + str(target["address"]) +
                " base_register=" + str(target["base_register"]) +
                " offset=" + str(target["field"]["offset"])
            )
            command_result = False
            continue

        if write_target(command_name, target, raw_value, value) == False:
            command_result = False

    return command_result


# Lists only SDC commands validated for write, with their CLI values.
# COMMAND width is 52 so long command names remain aligned with UNIT.
def print_command_list():
    rows = {}

    for did in SDC_BLOCKS:
        block = SDC_BLOCKS[did]

        for field_name in block["fields"]:
            field = block["fields"][field_name]
            command_name = field.get("command_name")

            if (
                command_name is None or
                str(command_name) == "" or
                field.get("write_active", False) is not True
            ):
                continue

            if field.get("values") is not None:
                aliases = field.get("aliases") or {}
                cli_values = []

                for raw in field["values"]:
                    if raw in aliases:
                        cli_values.append(str(aliases[raw]))
                    else:
                        cli_values.append(str(field["values"][raw]))

                values_text = " | ".join(cli_values)
            elif field.get("type") in ("uint16", "int16"):
                values_text = "numeric"
            else:
                values_text = ""

            if command_name not in rows:
                rows[command_name] = {
                    "command": command_name,
                    "unit": "" if field.get("unit") is None else str(field.get("unit")),
                    "values": values_text
                }

    print("")
    print("WRITE ENABLED")
    print("=" * len("WRITE ENABLED"))
    print("")
    print("{:<52}{:<12}{}".format("COMMAND", "UNIT", "CLI VALUES"))
    print("-" * 102)

    for command_name in sorted(rows):
        row = rows[command_name]
        print("{:<52}{:<12}{}".format(
            row["command"], row["unit"], row["values"]
        ))
    print("-" * 102)

# Shows CLI usage and available input options.
def print_help():
    script_name = os.path.basename(sys.argv[0])
    print("")
    print("Usage:")
    print("  " + script_name + " command value [command value ...]")
    print("  " + script_name + " --list")
    print("  " + script_name + " --help")
    print("")
    print("Options:")
    print("  --help    Show this help message")
    print("  --list    List all WRITE ENABLED SDC commands")
    print("")
    print("Without CLI arguments:")
    print("  commands are read from mate_input.json")


# Parses command/value pairs supplied through the CLI.
def get_cli_commands():
    arguments = sys.argv[1:]
    requested_changes = {}

    if len(arguments) % 2 != 0:
        raise ValueError("CLI arguments must be command_name value pairs")

    for index in range(0, len(arguments), 2):
        requested_changes[arguments[index]] = arguments[index + 1]

    validate_command_names(requested_changes)

    return requested_changes


# Returns the JSON batch only while time_taken is blank.
def get_mate_commands(mate_input):
    if "time_taken" not in mate_input:
        raise ValueError("mate_input time_taken field is missing")
    if mate_input["time_taken"] != "":
        return {}
    if "commands" not in mate_input or not isinstance(mate_input["commands"], dict):
        raise ValueError("mate_input commands section is missing or invalid")

    requested_changes = dict(mate_input["commands"])
    if len(requested_changes) == 0:
        return requested_changes
    validate_command_names(requested_changes)
    return requested_changes


mate_input = load_mate_input()
# Input mode flags: CLI bypasses JSON; utility mode is --help/--list.
# input_error controls the final exit status.
cli_mode = len(sys.argv) > 1
utility_mode = False
input_error = False

try:
    if cli_mode:
        if len(sys.argv) == 2 and sys.argv[1] == "--help":
            print_help()
            sys.exit(0)
        elif len(sys.argv) == 2 and sys.argv[1] == "--list":
            print_command_list()
            sys.exit(0)
        else:
            requested_changes = get_cli_commands()
            logger.debug(".. CLI mode: mate_input commands ignored")
    else:
        requested_changes = get_mate_commands(mate_input)
except Exception as e:
    logger.error(".. input preparation failed: " + str(e))
    requested_changes = {}
    input_error = True


print ("working directory: " +  working_dir)
print ("output path      : " + output_path)
print ("variables initialization completed")


# =======================================This is the main loop =====================================
#------------------------------------------------
# MATE3 ModBus Interface
#------------------------------------------------

if len(requested_changes) > 0:

    print(".. waiting few seconds ")
    time.sleep(1)
    start_run  = datetime.now()
    logger.debug(".. Building MATE3 MODBUS connection")

    # Batch remains successful only if every requested command succeeds.
    client = None
    batch_result = True
    command_results = {}

    try:
        client = ModbusClient(mate3_ip, port=mate3_modbus)
        logger.debug(".. Make sure we are indeed connected to an Outback power system")
        reg = sunspec_start_reg
        size = getSunSpec(reg)

        if size is None:
            raise RuntimeError("We have failed to detect an Outback system")

        logger.debug(".. Connected OK to an Outback system")

        startReg = reg + size + 4
        detected_blocks = scan_blocks(startReg)

        for command_name in requested_changes:
            command_result = execute_command(
                command_name,
                requested_changes[command_name],
                detected_blocks
            )

            command_results[command_name] = command_result

            if command_result == False:
                batch_result = False

    except Exception as e:
        batch_result = False
        logger.error(".. Change execution failed: " + str(e))

    finally:
        if client is not None:
            client.close()
            logger.debug(".. Mate connection closed ")


    if not cli_mode:
        # Mark the JSON batch as processed only after processing has finished.
        mate_input["time_taken"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            save_mate_input(mate_input)
        except Exception as e:
            batch_result = False
            logger.error(".. unable to save final mate_input status " + str(e))


    if batch_result:
        logger.info(".. batch result: SUCCESS")
    else:
        logger.warning(".. batch result: FAILED")


    end_run = datetime.now()
    running_time = round ((end_run - start_run).total_seconds(),3)
    print("Mate response time:    ",format(running_time,".3f")," sec")

else:
    if not utility_mode and not input_error:
        logger.info (".. nothing to write")


print(".. done ")

if input_error:
    sys.exit(1)

if len(requested_changes) > 0 and batch_result == False:
    sys.exit(1)

sys.exit(0)

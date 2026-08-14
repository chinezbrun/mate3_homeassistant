# ChangeMateStatusModBus 1.0 Upgrade Notes

Version 1.0 is a breaking ChangeMate release.

## What changed

- Writable command definitions moved out of the hardcoded ChangeMate command logic and into `sdc.py`.
- The SDC (SunSpec Data Configuration) is a shared Single Source of Truth based on the OutBack AXS Port SunSpec application note.
- ChangeMate dynamically scans the SunSpec blocks reported by the connected MATE3/MATE3S and matches commands by DID.
- `write_active` provides an explicit write safety gate.
- Enumerated values, aliases, numeric limits, access properties and scale factors are validated from SDC metadata.
- CLI supports multiple `command_name value` pairs in one run.
- `R/W` writes are verified by read-back.
- `mate_input.json` remains a supported input mechanism, but the 1.0 command structure is not backward compatible with older ChangeMate versions.

## Required upgrade action

Do not copy the new `ChangeMateStatusModBus.py` over an older installation by itself.

Upgrade the matching set:

```text
ChangeMateStatusModBus.py
sdc.py
```

If an existing `mate_input.json` is used by external automation, update it to the new command structure.

If `mate_input.json` does not exist, ChangeMate creates an empty one automatically.

Then review any external automation that writes to `mate_input.json` or calls ChangeMate from the command line.

## `write_active`

Parameters shipped with `write_active=True` are commands that have been enabled and tested for writing with ChangeMate.

The SDC also contains additional parameters defined by the OutBack AXS Port SunSpec application note as `R/W` (read/write). ChangeMate can write to these parameters when their `write_active` setting is explicitly changed to `True`.

Parameters shipped with `write_active=False` have not necessarily been individually tested with ChangeMate on actual hardware. Enabling writes to these parameters is therefore the user's responsibility.

> **Use with caution:** Writing incorrect values to device configuration registers may change the operation of the connected OutBack equipment. Only enable `write_active` for additional `R/W` parameters if you understand the corresponding register and its valid values.

ChangeMate still applies the SDC validation rules when a parameter is enabled, including access type, datatype, allowed values or numeric limits, and scale factors.

ChangeMate 1.0 currently implements `uint16` and `int16` write datatypes. A different writable datatype is rejected even if `write_active=True`.

## Finding available commands

List all commands currently enabled for writing and their accepted values:

```bash
python3 ChangeMateStatusModBus.py --list
```

Show command-line usage:

```bash
python3 ChangeMateStatusModBus.py --help
```

The list is generated directly from the SDC, so it reflects the command definitions used by the installed version.

## CLI usage

A command is passed as a `command_name value` pair.

For example, to change Schedule 1 AC mode to `MiniGrid`:

```bash
python3 ChangeMateStatusModBus.py system_sched_1_ac_mode MiniGrid
```

Multiple commands can be executed in the same run:

```bash
python3 ChangeMateStatusModBus.py system_sched_1_ac_mode MiniGrid system_sched_1_ac_mode_hour 8
```

Another example using an enumerated command value:

```bash
python3 ChangeMateStatusModBus.py system_bulk_charge_enable_disable StartBulk
```

Use the command names and accepted values displayed by `--list`. ChangeMate validates each requested value against the corresponding SDC definition before attempting the write.

## Using `mate_input.json`

Without command-line arguments, ChangeMate reads commands from `mate_input.json`.

The basic structure is:

```json
{
  "time_taken": "",
  "commands": {
    "command_name": "value"
  }
}
```

Multiple commands can be submitted in the same file:

```json
{
  "time_taken": "",
  "commands": {
    "command_1": "value_1",
    "command_2": "value_2"
  }
}
```

Only the commands required for that operation should be included.

A `mate_input_example.json` containing all currently write-enabled commands is provided in the ChangeMate documentation directory for reference.

The example file is intentionally marked as already processed to prevent accidental execution. To use it as a template, copy only the required commands to `mate_input.json` and set `time_taken` to an empty string.

## Recommended first test

First run:

```bash
python3 ChangeMateStatusModBus.py --list
```

Select a known command and one of the accepted values shown in the list, then execute it manually. For example:

```bash
python3 ChangeMateStatusModBus.py system_sched_1_ac_mode MiniGrid
```

Then inspect `data/events_cms.log` and confirm that the command reports `SUCCESS` and, for an `R/W` field, successful read-back verification.

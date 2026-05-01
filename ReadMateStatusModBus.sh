#!/bin/bash
# navigate in the directory where python script is placed
cd /mnt/web/mate3_homeassistant
# open the script in a separate terminal - better control and visualisation of console's messages
lxterminal -e python3 ReadMateStatusModBus.py
# if not just run it 
#python3 ReadMateStatusModBus.py
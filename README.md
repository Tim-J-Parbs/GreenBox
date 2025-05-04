# GreenBox
Connect to a berlin green GreenBox using python. 

This program implements most communication functionality of the official GreenBox app. Reading and setting brightness (per lamp) and wake times is supported, as is reading water level.
To facilitate communication, this runs asynchronously. 

# Usage
Usage is very straight-forward: 
```
from greenbox import GreenBox
import asyncio
async def run():
    async with GreenBox("34:86:5d:19:58:b6") as box:
        await box.light_on()
asyncio.run(run())
```
Some caveats - greenboxes publish all their data in a sort of round-robin way and send one data point after the other. This means that for a second after instancing the class, not every information is available. I opted for not pausing for a second or two, but you might! 

I did not include a scanner utility. Bring your own greenbox device MAC. 

Also, I am unsure what long-term usage does to your device, and I'm not responsible for any damage you do.

As there is no documentation for the BLE communication, there is some guesswork involved. Feel free to shoot me a message if something does not work on your end. 

I mostly use this to communicate with a HomeAssistant instance via MQTT. This is quite rudimentary at the moment. 
In `connector.py`, connection data is read from `secrets.py` (only template in this repo). Fill in your device MAC and MQTT broker data, and `connector.py` will expose water and light status to the MQTT topics

`home-assistant/greenbox/water_lvl`
`home-assistant/greenbox/light_on`



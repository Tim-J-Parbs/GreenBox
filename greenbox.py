import asyncio
from bleak import BleakClient
import os
import datetime
import logging
from greenbox_message_ids import *

logger = logging.getLogger(__name__)

class GreenBox:
    """ GreenBox connector from python.
        Provides a context manager which allows control over a single Berlin GreenBox.
        Call with known MAC (in "XX:XX:XX:XX:XX:XX") format. Scanner comes soon.
        Tested with base model. """
    def __init__(self, device_address):
        self.wake_hours_utc = 0
        self.wake_minutes_utc = 0
        self.hours_on = 0
        self.wake_hours_weekend_utc = 0
        self.wake_minutes_weekend_utc = 0
        self.hours_on_weekend = 0
        self.weekend_enabled = False
        self.lamps_on = 0 # Don't know why this is sometimes > 1?
        self.lamp_lvl = [0,0,0]
        self.water_lvl = 0
        self._data_store = {}
        self._debug = True
        self._device_address = device_address
        self._client = BleakClient(self._device_address)
        self._bt_write_lock = asyncio.Lock()
        self._notification_queue = asyncio.Queue()
        return

    def get_data(self) -> dict:
        return {
            k: getattr(self, k)
            for k, v in self.__dict__.items()
            if not k.startswith('_')
        }

    def proc_all(self, data, timestamp):
        # Data logging. Will be removed soon
        hex_data = data.hex()
        if hex_data in self._data_store.keys():
            self._data_store[hex_data]["timestamp"] = timestamp
        else:
            parsed_id, parsed_val = self.parse_7b_notification(data)
            self._data_store[hex_data] = {"timestamp": timestamp, "first_timestamp": timestamp,
                                                   "raw_val": f"{list(data)}, {hex_data}", "val_id":parsed_id,
                                                   "parsed": f"{parsed_id}, {parsed_val}"}
    def proc_known_ids(self, data):
        """ Parse currently readable data into human-readable form."""
        val_id, val = self.parse_7b_notification(data)
        if val_id == gb_wake_time:
            self.wake_hours_utc = val // 100
            self.wake_minutes_utc = val - self.wake_hours_utc * 100
        if val_id == gb_wake_hours:
            self.hours_on = val
        if val_id == gb_wake_hours_wknd:
            self.weekend_enabled = val > 0
            self.hours_on_weekend = val
        if val_id == gb_wake_time_wknd:
            self.wake_hours_weekend_utc  = val // 100
            self.wake_minutes_weekend_utc = val - self.wake_hours_weekend_utc * 100
        if val_id == gb_water:
            self.water_lvl = val
        if val_id in gb_lamps:
            self.lamp_lvl[gb_lamps.index(val_id)] = val
        if val_id == gb_light_on:
            self.lamps_on = val

    def create_7b_message(self, data, control_id):
        """ Create a formatted 7b message to send to device."""
        value_high = data >> 8
        value_low = data - (value_high << 8)
        values = [control_id, value_high, value_low]
        checksum = self.gen_checksum(values)
        byte_message = bytes([238, control_id, value_high, value_low, checksum, 239])
        return byte_message

    def parse_7b_notification(self, data):
        """ Parse 7 bytes of notification data. There are some rare notifications
            which are longer, but I don't currently know what's going on with them."""
        if len(data) > 7:
            return -1, data
        if len(data) < 4:
            return 0, 0

        payload = data[1:4]
        status_id, value_high, value_low = payload
        value = (value_high << 8) | value_low
        return status_id, value

    def gen_checksum(self, values):
        """ Generates the checksum, in a sarcastic tone.
            Without this magic bit, messages get ignored.
            Other applications use big boy CRC here, so this took really long to figure out."""
        return (gb_checksum_base - sum(values)) % 256

    async def lamp_control(self, strength, lamp_id):
        """ Control one of the 3 lamps. Strength between 0 and 100, lamp_id between 0 and 2"""
        msg = self.create_7b_message(self.valchk(strength, 100), gb_lamps[lamp_id])
        await self.write_to_status(msg)
        return

    async def light_on(self):
        await self.write_to_status(self.create_7b_message(1, gb_light_on))

    async def light_off(self):
        await self.write_to_status(self.create_7b_message(0, gb_light_on))

    async def light_toggle(self):
        await self.write_to_status(self.create_7b_message(not self.lamps_on, gb_light_on))

    async def set_wake_time_utc(self, hours, minutes):
        """ Control wake time (in UTC!) for device. """
        hours, minutes = self.valchk(hours, 24), self.valchk(minutes,60)
        msg = self.create_7b_message(hours*100+minutes, gb_wake_time)
        await self.write_to_status(msg)
        return

    async def set_wake_time_weekend_utc(self, hours, minutes):
        """ Control wake time on weekends (in UTC!) for device. """
        hours, minutes = self.valchk(hours, 24), self.valchk(minutes, 60)
        msg = self.create_7b_message(hours*100+minutes, gb_wake_time_wknd)
        await self.write_to_status(msg)
        return

    def valchk(self, val, max_val, min_val=0):
        """ Helper function limits the range of values. """
        return max(min(int(val), max_val), min_val)

    async def process_incoming(self):
        """ Process data queue entries."""
        try:
            while True:
                sender, data = await self._notification_queue.get()
                timestamp = datetime.datetime.now().isoformat(sep=' ', timespec='milliseconds')
                self.proc_known_ids(data)
                self.proc_all(data, timestamp)
        except asyncio.CancelledError:
            print("Notification processor stopped.")

    async def update(self):
        """ CLI status screen """
        os.system('cls' if os.name == 'nt' else 'clear')
        self.show_status()
        if self._debug:
            self.show_all()
        await asyncio.sleep(0.5)

    def show_status(self):
        time_str = (f"Wake time: {self.wake_hours_utc:02d}:{self.wake_minutes_utc:02d} (UTC)"
                   f" for {self.hours_on } hours")
        if self.weekend_enabled:
            time_str += (
                f" and Wake_time: {self.wake_hours_weekend_utc:02d}:{self.wake_minutes_weekend_utc:02d} (UTC)"
                f" for {self.hours_on_weekend} hours on weekends")
        print(time_str)
        print("Lamps:")
        for i, l in enumerate(self.lamp_lvl):
            print(f'Lamp {i}: {l}')
        print("Water level:")
        print(f"{self.water_lvl} / 100")

    def show_all(self):
        print('\n')
        print(f"{'Fields':<5} | {'Added ':<10} | {'Updated ':<10}| {'Raw value':<10} | {'Parsed':<10}")
        print("-" * 35)
        sorted_list = self._data_store.items()
        for field, info in sorted_list:
            if info['val_id'] not in gb_unkown_ids:
                print(
                    f"{field:<5} |  {info['timestamp']} | {info['first_timestamp']} | {info['raw_val']}| {info['parsed']}")
        print('\nUnknown data:')
        print(f"{'Field':<5} | {'Added ':<10} | {'Updated ':<10}| {'Raw value':<10} | {'Parsed':<10}")
        print("-" * 35)
        sorted_list = self._data_store.items()
        for field, info in sorted_list:
            if info['val_id'] in gb_unkown_ids:
                print(
                    f"{field:<5} |  {info['timestamp']} | {info['first_timestamp']} | {info['raw_val']}| {info['parsed']}")

    async def scan_uuids(self):
        """ Utility which scans open UUIDs. Unused, but nice to have. """
        for service in self._client.services:
            print(f"Service {service.uuid}")
            for char in service.characteristics:
                print(f"  Char UUID: {char.uuid} - Handle: {char.handle} - Props: {char.properties}")

    async def __aenter__(self):
        try:
            await self._client.connect()
            await self._client.start_notify(gb_characteristic_uuid,
                                           lambda s, d: self._notification_queue.put_nowait((s, d)))
            self._queue_worker = asyncio.create_task(self.process_incoming())
            print("Connected, listening to device..")
        except Exception as Err:
            print(f"Unexpected error: {Err}")
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb):
        print("Shutting down...")
        await self._client.stop_notify(gb_characteristic_uuid)
        await self._client.disconnect()

    async def write_to_status(self, data):
        await self.safe_write_no_response(gb_characteristic_uuid, data)

    async def safe_write_no_response(self, char_uuid, data):
        async with self._bt_write_lock:
            await self._client.write_gatt_char(char_uuid, data, response=False)



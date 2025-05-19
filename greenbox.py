import asyncio
from bleak import BleakClient
import os
from datetime import datetime, timezone, timedelta
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
        self.timeout_seconds = 20
        self.wake_minutes_weekend_utc = 0
        self.hours_on_weekend = 0
        self.weekend_enabled = False
        self.light_status = 0
        self.light_on = False
        self.lamp_lvl = [0,0,0]
        self.water_lvl = 0
        self.timestamp = None
        self._data_store = {}
        self._debug = True
        self._device_address = device_address
        self._client = BleakClient(self._device_address)
        self.update_timestamp()
        self._bt_write_lock = asyncio.Lock()
        self._notification_queue = asyncio.Queue()

        return
    def update_timestamp(self):
        self.timestamp = datetime.now(timezone.utc)

    def is_connected(self):
        delta = timedelta(seconds=self.timeout_seconds)
        now_utc = datetime.now(timezone.utc)
        return (self.timestamp + delta) > now_utc

    def get_data(self) -> dict:
        value_dict= {
            k: getattr(self, k)
            for k, v in self.__dict__.items()
            if not k.startswith('_')
        }
        value_dict['is_connected'] = self.is_connected()
        return value_dict

    def proc_all(self, data, timestamp):
        # Data logging. Will be removed soon
        hex_data = data.hex()
        if hex_data in self._data_store.keys():
            self._data_store[hex_data]["timestamp"] = timestamp
        else:
            parsed_id, parsed_val = self.parse_7b_notification(data)
            self._data_store[hex_data] = {"timestamp": timestamp, "first_timestamp": timestamp,
                                                   "raw_val": list(data), "val_id":parsed_id,
                                                   "parsed": [parsed_id, parsed_val]}
    def proc_known_ids(self, data):
        """ Parse currently readable data into human-readable form."""
        # I should probably move all these values into timestamped containers to see if any go out of date.
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
            self.light_status = val
            self.check_light_status()
        self.update_timestamp()

    def check_light_status(self):
        """ Greenboxes do some funky stuff with their light.
            If I'm not mistaken, they transmit a status '3' for their light_status in case they
            followed their programming for the last period of time (weirdly 30h 10m). Otherwise they
            transmit 0 or 1, for on or off."""
        # If not connected, return -1
        if not self.is_connected():
            self.light_on = -1
            return
        if self.light_status == 3:
            now_utc = datetime.now(timezone.utc)
            start_time = now_utc.replace(hour=self.wake_hours_utc, minute=self.wake_minutes_utc,
                                         second=0, microsecond=0)
            if start_time > now_utc:
                # Go back a day if we haven’t reached the start yet but may be in active period
                start_time -= timedelta(days=1)

            duration = timedelta(hours=self.hours_on, minutes=0)
            end_time = start_time + duration
            self.light_on = int(start_time <= now_utc < end_time)
        else:
            self.light_on = int(self.light_status == 1)
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

    async def turn_light_on(self):
        await self.write_to_status(self.create_7b_message(1, gb_light_on))

    async def turn_light_off(self):
        await self.write_to_status(self.create_7b_message(0, gb_light_on))

    async def toggle_light(self):
        await self.write_to_status(self.create_7b_message(not self.light_on, gb_light_on))

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
                timestamp = datetime.now().isoformat(sep=' ', timespec='milliseconds')
                self.proc_known_ids(data)
                self.proc_all(data, timestamp)
        except asyncio.CancelledError:
            print("Notification processor stopped.")

    def update(self):
        """ CLI status screen """
        os.system('cls' if os.name == 'nt' else 'clear')
        self.show_status()
        if self._debug:
            self.show_all()

    def show_status(self):
        time_str = (f"Wake time: {self.wake_hours_utc:02d}:{self.wake_minutes_utc:02d} (UTC)"
                   f" for {self.hours_on } hours")
        if self.weekend_enabled:
            time_str += (
                f" and Wake_time: {self.wake_hours_weekend_utc:02d}:{self.wake_minutes_weekend_utc:02d} (UTC)"
                f" for {self.hours_on_weekend} hours on weekends")
        print(time_str)
        print(f"Light is {'on' if self.light_on else 'off'}.")
        print("Lamps:")
        for i, l in enumerate(self.lamp_lvl):
            print(f'Lamp {i}: {l}')
        print("Water level:")
        print(f"{self.water_lvl} / 100")

    def show_all(self):
        print('\n')
        sorted_list = self._data_store.items()
        self.print_status(sorted_list, lambda x: x['val_id'] not in gb_unkown_ids)
        print('\nUnknown data:')
        self.print_status(sorted_list, lambda x: x['val_id'] in gb_unkown_ids)

    def print_status(self, status_list, condition):
        header = f"{'Fields':<12} | {'Added ':<23} | {'Updated ':<24}| {'Raw value':<25} | {'Command':<14}"
        print(header)
        print("-" * len(header))
        for field, info in status_list:
            if condition(info):
                raw_info =[f"{int(i):3d}" for i in info['raw_val']]
                parsed_info = [f"{int(i):3d}" for i in info['parsed']]
                print(f"{field} | {info['first_timestamp']} | {info['timestamp']} | [{' '.join(raw_info)}] | {' -> '.join(parsed_info)}")
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
            print("Connected, waiting for data...")
            await asyncio.sleep(2)
            print("Now listening to device..")
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



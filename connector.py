from greenbox import GreenBox
from secrets import *
import paho.mqtt.client as mqtt
import asyncio
import threading

class Communicator:
    def __init__(self, broker, command_topic, data_topics):
        self.broker = broker
        self.command_topic = command_topic
        self.data_topics = data_topics
        self.command_queue = asyncio.Queue()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='Greenbox')
        # Connect to the MQTT broker
        self._setup_callbacks()
        self.client.connect(MQTT_BROKER, MQTT_PORT, 6000)

    def _setup_callbacks(self):
        def on_connect(client, userdata, flags, rc, props=None):
            print("MQTT Connected.")
            client.subscribe(self.command_topic)

        def on_message(client, userdata, msg):
            print(f"Received MQTT: {msg.topic} -> {msg.payload.decode()}")
            # Push to asyncio-safe queue via event loop
            asyncio.run_coroutine_threadsafe(
                self.command_queue.put(msg.payload.decode()), asyncio.get_event_loop()
            )
        self.client.on_connect = on_connect
        self.client.on_message = on_message

    def start(self):
        def run():
            self.client.connect(self.broker)
            self.client.loop_forever()
        threading.Thread(target=run, daemon=True).start()

    async def receive_command(self):
        return await self.command_queue.get()

    def publish(self, payload_list):
        for topic, payload in zip(self.data_topics, payload_list):
            self.client.publish(topic, payload)

async def run_communication():
    base = "home-assistant/greenbox/"
    communicator = Communicator(
        broker=MQTT_BROKER,
        command_topic=f"{base}command",
        data_topics=[f"{base}water_lvl", f"{base}light_on"]
    )
    communicator.start()

    async with GreenBox(DEVICE_ADDR) as connector:
        while True:
            try:
                command_task = asyncio.create_task(communicator.receive_command())
                cmd, _ = await asyncio.wait({command_task}, timeout=2)

                if command_task in cmd:
                    #command = command_task.result()
                    connector.toggle_light()

                data = connector.get_data()
                communicator.publish([data['water_lvl'], data['lamps_on']])

                await asyncio.sleep(10)
            except asyncio.CancelledError:
                break

if __name__ == "__main__":
    try:
        asyncio.run(run_communication())
    except KeyboardInterrupt:
        print("Stopped.")
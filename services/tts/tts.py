import requests
import json
import paho.mqtt.client as mqtt
from jarvis_tts import JarvisTTS
import time

tts = JarvisTTS(
    voice="bm_lewis"
)
tts.say("Loading."); # warm starting

# create the client
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.connect("localhost", 1883, keepalive=30)



def on_message(client, userdata, msg):
    text = msg.payload.decode()
    print(f"Received: {text}")
    try:
        response = tts.say(text)
        print(f"Response: {response}")
        # publish to the broker
        client.publish("jarvis/tts/response", response)
    except Exception as e:
        print(f"Error calling brain: {e}")
        
client.on_message = on_message
client.subscribe("jarvis/brain/response")
client.loop_start()

print("tts listening...")
try:
    while True:
        time.sleep(1) # the main loop, keep the program running
except KeyboardInterrupt:
    print("Shutting down...")
    client.disconnect()
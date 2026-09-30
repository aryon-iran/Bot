import os
import threading
from flask import Flask
from pyrobale.client import Client
from pyrobale.objects import Message

TOKEN = "1097976151:cgPxiahbDONHRDNBjYpOQW1_vcKYTrrN3n0"

app = Flask(__name__)

@app.route("/")
def health():
    return "Bot is alive", 200

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

bot = Client(TOKEN)

@bot.on_command("start")
async def start_handler(message: Message):
    await message.reply("سلام! من بات شما هستم. 👋")

@bot.on_message()
async def echo_handler(message: Message):
    if message.text:
        await message.reply(f"شما گفتید: {message.text}")

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    print("Bot is running...")
    bot.run()

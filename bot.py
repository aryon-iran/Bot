from balecore import Bot, Message

# توکن ربات خود را از @BotFather در پیام رسان بله دریافت کنید
bot = Bot(token="92641667:3VfaPWTAGd-oKPsn86I7mUB-Nffx5GWzU70")

@bot.on_message(command="/start")
async def start_handler(message: Message):
    await message.reply("سلام! به ربات خوش آمدید 👋")

if __name__ == "__main__":
    bot.run()
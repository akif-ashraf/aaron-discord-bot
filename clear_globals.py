import asyncio
import os
import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@client.event
async def on_ready():
    synced = await tree.sync()
    print(f"Global commands after wipe: {len(synced)}", flush=True)
    await client.close()


client.run(TOKEN)

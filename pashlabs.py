import os
import aiohttp
import re
import discord
from dotenv import load_dotenv
from discord import app_commands
from discord.ext import commands
import sqlite3
message_history = {}
import google.generativeai as genai

load_dotenv()
GOOGLE_AI_KEY = os.getenv("GOOGLE_AI_KEY")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
MAX_HISTORY = os.getenv("MAX_HISTORY")

# AI Configuration
genai.configure(api_key='AIzaSyDc1lqafzC9qiGuSdyYFOEOQMUDsphUv3U')
text_generation_config = {
    "temperature": 0.9,
    "top_p":1,
    "top_k":1,
    "max_output_tokens":512,
}
image_generation_config = {
    "temperature":0.4,
    "top_p":1,
    "top_k":32,
    "max_output_tokens":512,
}
safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]
text_model = genai.GenerativeModel(model_name="gemini-pro", generation_config=text_generation_config, safety_settings=safety_settings)
image_model = genai.GenerativeModel(model_name="gemini-pro-vision", generation_config=image_generation_config, safety_settings=safety_settings)

# Database Connection
conn = sqlite3.connect('user_personalities.db')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS user_personalities (
    user_id INTEGER PRIMARY KEY,
    personality TEXT,
    interests TEXT,
    conversation_style TEXT
)''')
c.execute('''CREATE TABLE IF NOT EXISTS welcome_messages (
    server_id INTEGER PRIMARY KEY,
    welcome_channel_id INTEGER,
    welcome_message TEXT
)''')
conn2 = sqlite3.connect('globe.db')
c2 = conn2.cursor()
c2.execute('''CREATE TABLE IF NOT EXISTS user_servers (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    servers TEXT
)''')

# Discord Code
bot = commands.Bot(command_prefix="!", intents=discord.Intents.default(), name="Pash")

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.tree.command(name="set")
async def set(interaction: discord.Interaction, description: str):
    """Sets your personality description."""
    user_id = interaction.user.id
    personality_description = description
    c.execute('''INSERT OR REPLACE INTO user_personalities (user_id, personality) VALUES (?, ?)''', (user_id, personality_description))
    conn.commit()
    await interaction.response.send_message(f"Your personality description has been set to '{description}'.")

@bot.tree.command(name="reset")
async def reset(interaction: discord.Interaction):
    """Resets your personality description to the default."""
    user_id = interaction.user.id
    c.execute('''DELETE FROM user_personalities WHERE user_id = ?''', (user_id,))
    conn.commit()
    await interaction.response.send_message("Your personality description has been reset to the default.")

@bot.event
async def on_message(message):
    # Ignore messages sent by the bot
    if message.author == bot.user or message.mention_everyone:
        return

    # Check if the bot is mentioned or the message is a DM
    if bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel):
        # Start Typing to seem like something happened
        cleaned_text = clean_discord_message(message.content)
        async with message.channel.typing():
            # Check for image attachments
            if message.attachments:
                print("New Image Message FROM:" + str(message.author.id) + ": " + cleaned_text)
                # Currently no chat history for images
                for attachment in message.attachments:
                    if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif' ]):
                        #await message.add_reaction('🎨')
                        async with aiohttp.ClientSession() as session:
                            async with session.get(attachment.url) as resp:
                                if resp.status != 200:
                                    await message.channel.send('Unable to download the image.')
                                    return
                                image_data = await resp.read()
                                response_text = await generate_response_with_image_and_text(image_data, cleaned_text)
                                await split_and_send_messages(message, response_text, 1700)
                                return
            else:
                print("New Message FROM:" + str(message.author.id) + ": " + cleaned_text)
                # Check for Keyword Reset
                if "RESET" in cleaned_text:
                    # End back message
                    if message.author.id in message_history:
                        del message_history[message.author.id]
                    await message.channel.send("History reset was successful for " + str(message.author.name))
                    return

                # Check for Set Personality Command
                if cleaned_text.startswith(".set"):
                    # Extract the user's desired personality description
                    personality_description = cleaned_text[len(".set"):].strip()
                    # Update the user's personality in the database
                    user_id = message.author.id
                    c.execute('''INSERT OR REPLACE INTO user_personalities (user_id, personality) VALUES (?, ?)''', (user_id, personality_description))
                    conn.commit()
                    await message.channel.send("Your personality has been successfully updated.")
                    return

                # Check if history is disabled just send response
                if(MAX_HISTORY == 1):
                    response_text = await generate_response_with_text(cleaned_text)
                    await split_and_send_messages(message, response_text, 1700)
                    return;

                # Add users question to history
                update_message_history(message.author.id,cleaned_text)
                user_id = message.author.id
                c.execute('''SELECT personality FROM user_personalities WHERE user_id = ?''', (user_id,))
                personality_description = c.fetchone()
                if personality_description is None:
                    personality_description = "You are pash discord bot made by Pashlabs. You are helpfull and you are kind and you use emojis a lot."
                else:
                    personality_description = personality_description[0]
                response_text = await generate_response_with_text(personality_description, get_formatted_message_history(message.author.id))
                # add AI response to history
                update_message_history(message.author.id,response_text)
                # Split the Message so discord does not get upset
                await split_and_send_messages(message, response_text, 1700)

@bot.tree.command(name="welcome")
async def welcome(interaction: discord.Interaction, welcome_channel: discord.TextChannel, welcome_enabled: bool):
    """Sets up the welcome message for the server."""
    # Get the server ID
    server_id = interaction.guild.id

    # Check if the welcome message is enabled
    if welcome_enabled:
        # Update the database with the welcome message and channel
        c.execute('''INSERT OR REPLACE INTO welcome_messages (server_id, welcome_channel_id, welcome_message) VALUES (?, ?, ?)''', (server_id, welcome_channel.id, f"Welcome <@{interaction.user.id}> to the server!"))
        conn.commit()

        # Send a confirmation message to the user
        await interaction.response.send_message(f"Welcome message has been enabled for {welcome_channel.name} channel.")
    else:
        # Disable the welcome message
        c.execute('''DELETE FROM welcome_messages WHERE server_id = ?''', (server_id,))
        conn.commit()

        # Send a confirmation message to the user
        await interaction.response.send_message("Welcome message has been disabled.")

@bot.event
async def on_member_join(member):
    """Sends a welcome message to new members."""
    # Get the server ID
    server_id = member.guild.id

    # Check if the welcome message is enabled
    c.execute('''SELECT welcome_channel_id, welcome_message FROM welcome_messages WHERE server_id = ?''', (server_id,))
    result = c.fetchone()
    if result is not None:
        # Get the welcome channel and message
        welcome_channel_id, welcome_message = result

        # Send the welcome message to the new member
        welcome_channel = member.guild.get_channel(welcome_channel_id)
        await welcome_channel.send(welcome_message)

async def generate_response_with_text(personality_description, message_text):
    prompt_parts = [personality_description, message_text]
    print("Got textPrompt: " + message_text)
    response = text_model.generate_content(prompt_parts)
    if(response._error):
        return "❌" +  str(response._error)
    return response.text

async def generate_response_with_image_and_text(image_data, text):
    image_parts = [{"mime_type": "image/jpeg", "data": image_data}]
    prompt_parts = [image_parts[0], f"\n{text if text else 'What is this a picture of?'}"]
    response = image_model.generate_content(prompt_parts)
    if(response._error):
        return "❌" +  str(response._error)
    return response.text

def update_message_history(user_id, text):
    # Check if user_id already exists in the dictionary
    if user_id in message_history:
        # Append the new message to the user's message list
        message_history[user_id].append(text)
        # If there are more than 12 messages, remove the oldest one
        #if len(message_history[user_id]) > MAX_HISTORY:
        #    message_history[user_id].pop(0)
    else:
        # If the user_id does not exist, create a new entry with the message
        message_history[user_id] = [text]

def get_formatted_message_history(user_id):
    """
    Function to return the message history for a given user_id with two line breaks between each message.
    """
    if user_id in message_history:
        # Join the messages with two line breaks
        return '\n\n'.join(message_history[user_id])
    else:
        return "No messages found for this user."

async def split_and_send_messages(message_system, text, max_length):
    # Split the string into parts
    messages = []
    for i in range(0, len(text), max_length):
        sub_message = text[i:i+max_length]
        messages.append(sub_message)
    # Send each part as a separate message
    for string in messages:
        await message_system.reply(string, mention_author=True)

def clean_discord_message(input_string):
    # Create a regular expression pattern to match text between < and >
    bracket_pattern = re.compile(r'<[^>]+>')
    # Replace text between brackets with an empty string
    cleaned_content = bracket_pattern.sub('', input_string)
    return cleaned_content

# Run Bot
bot.run('MTIxNDU5NDA5NTM5ODI2MDgyOA.GwcLM6.c6c88WWdfmrAjzzcoCF7F5VIWik_qATswakL40')




import os
from venv import logger
from ollama import Client
import ollama
from dotenv import load_dotenv
import asyncio
load_dotenv()


client = Client(
    host="https://ollama.com",
    headers={'Authorization': f'Bearer {"a5adf1fd3324416190a18b35827960ab.sDQCli6rRyWVgI9fI_IVQGUi"}'}
)


async def ask_ai_local(messages):
    response = await asyncio.to_thread(
        ollama.chat,
        model="qwen2.5vl:32b",
        messages=messages,
    )
    return response["message"]["content"]


async def ask_ai_cloud(messages):
    response = await asyncio.to_thread(
        client.chat,
        model="gemma4:31b-cloud",
        messages=messages,
    )
    return response["message"]["content"]


async def ask_ai(messages: list):
    try:
        return await ask_ai_local(messages)
    except Exception as e:
        logger.warning("Local AI failed: %s", e)
        return await ask_ai_cloud(messages)
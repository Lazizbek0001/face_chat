import os
from venv import logger
from ollama import Client
import ollama
from dotenv import load_dotenv
import asyncio
load_dotenv()

MODELS = [
    {
        "id": "gpt-oss:120b-cloud",
        "name": "GPT-OSS 120B",
        "description": "Best reasoning",
    },
    {
        "id": "glm-4.7:cloud",
        "name": "GLM 4.7",
        "description": "Reasoning & coding",
    },
    {
        "id": "gemma4:31b-cloud",
        "name": "Gemma 4 31B",
        "description": "Best all-around",
    },
    {
        "id": "gemma3:27b-cloud",
        "name": "Gemma 3 27B",
        "description": "Large model",
    },
    {
        "id": "gpt-oss:20b-cloud",
        "name": "GPT-OSS 20B",
        "description": "Fast reasoning",
    },
    {
        "id": "gemma3:12b-cloud",
        "name": "Gemma 3 12B",
        "description": "Balanced",
    },
    {
        "id": "gemma3:4b-cloud",
        "name": "Gemma 3 4B",
        "description": "Fastest",
    },
]

client = Client(
    host="https://ollama.com",
    headers={'Authorization': f'Bearer {"a5adf1fd3324416190a18b35827960ab.sDQCli6rRyWVgI9fI_IVQGUi"}'}
)

async def ask_ai_cloud(messages, model="gemma4:31b-cloud"):
    response = await asyncio.to_thread(
        client.chat,
        model=model,
        messages=messages,
    )
    return response["message"]["content"]

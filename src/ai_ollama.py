import os
from ollama import Client
import ollama
from dotenv import load_dotenv

load_dotenv
client = Client(
    host="https://ollama.com",
    headers={'Authorization': f'Bearer {os.getenv("OLLAMA_KEY")}'}
)
async def ask_ai_cloud(messages: list):
    response = client.chat(
        model='gpt-oss:20b-cloud', 
        messages=messages
    )
    return response['message']['content']
    

async def ask_ai_local(messages: list):
    response = ollama.chat(
        model="qwen2.5vl:32b",
        messages=messages
    )
    return response["message"]["content"]
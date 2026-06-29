import os
from ollama import Client
import ollama

client = Client(
    host="https://ollama.com",
    headers={'Authorization': f'Bearer {"571d151a68364224a3b2f25af1e8de3b.1N6yIthmQx7XxnnGbHjZocSz"}'}
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
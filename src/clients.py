import os

from klavis import Klavis
from openai import OpenAI


KLAVIS_API_KEY = os.getenv("KLAVIS_API_KEY")
PLATFORM_NAME = os.getenv("PLATFORM_NAME", "MyApp")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY" )
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

def klavis_client():
    return Klavis(api_key=KLAVIS_API_KEY)


def openai_client():
    return OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

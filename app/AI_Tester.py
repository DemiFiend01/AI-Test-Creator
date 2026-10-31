from google import genai
from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("API_KEY")
if not api_key:
    raise ValueError("Missing Gemini API key. Set API_KEY in your .env file.")

client = genai.Client(api_key=api_key)

interaction = client.interactions.create(
    model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
    input="Tell me how this model and API works and how to modify the model to get the most out of it."
)
print(interaction.output_text)
